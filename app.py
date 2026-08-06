"""
28 (Irupathiyettu) — Kerala card game, online multiplayer via Streamlit.

Simplifications from the traditional rules (kept for playability):
- Trump suit is announced to all players as soon as it's chosen, rather than
  staying concealed until someone can't follow suit.
- Bidding increments by 1 point (min 14).
- Round scoring: bidding team gets +1 match point if they meet/beat their bid,
  -1 if they fail. First team to the room's target score wins the game.

Seats 0 & 2 are Team A. Seats 1 & 3 are Team B.

Modes:
- Classic: standard rules above.
- Royal Pair: if one player holds both King & Queen of trump, their team gets
  a +4 point bonus at trump reveal (like the "pair"/"marriage" bonus in 29).

Deal types:
- Half Deal (traditional): 4 cards dealt, bidding happens, then the other 4.
- Full Deal: all 8 cards dealt upfront, before bidding.

Fairness: the deck is shuffled with random.SystemRandom (OS entropy, not a
predictable seed), and the deal always starts from the seat left of the
rotating dealer, so no seat is systematically favoured over a session.
"""

import random
import string
import threading
import time

import streamlit as st

import auth
import sounds

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

# ---------------------------------------------------------------------------
# Game constants
# ---------------------------------------------------------------------------
SUITS = ["♠", "♥", "♦", "♣"]
RANK_ORDER = ["7", "8", "Q", "K", "10", "A", "9", "J"]  # low -> high, per suit
RANK_POINTS = {"J": 3, "9": 2, "A": 1, "10": 1, "K": 0, "Q": 0, "8": 0, "7": 0}
MIN_BID = 14
MAX_BID = 28
BID_CONFIRM_THRESHOLD = 19
TEAM_LABEL = {0: "Team A", 1: "Team B"}

_lock = threading.Lock()
_SECURE_RANDOM = random.SystemRandom()


def rank_value(rank):
    return RANK_ORDER.index(rank)


def make_deck():
    """Fisher-Yates shuffle using OS randomness so no seat/deal order is
    predictable or favoured across rounds."""
    deck = [r + s for s in SUITS for r in RANK_ORDER]
    _SECURE_RANDOM.shuffle(deck)
    return deck


def card_rank(card):
    return card[:-1]


def card_suit(card):
    return card[-1]


def team_of(seat):
    return 0 if seat in (0, 2) else 1


# ---------------------------------------------------------------------------
# Shared room store (persists across all sessions in this server process)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_rooms():
    return {}


def new_room_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


def new_room_state(game_mode, deal_type, target_score):
    return {
        "players": {},       # seat(int) -> {"username","display_name","bot"}
        "phase": "lobby",    # lobby, bidding, choose_trump, playing, round_over, game_over
        "dealer": 0,
        "deck": [],
        "hands": {0: [], 1: [], 2: [], 3: []},
        "bid_value": MIN_BID - 1,
        "bid_seat": None,
        "active_bidders": set(),
        "turn": None,
        "trump_suit": None,
        "trump_seat": None,
        "current_trick": [],
        "trick_leader": None,
        "tricks_played": 0,
        "round_points": {0: 0, 1: 0},
        "match_score": {0: 0, 1: 0},
        "log": [],
        "round_summary": None,
        "game_mode": game_mode,          # "classic" | "royal_pair"
        "deal_type": deal_type,          # "half" | "full"
        "target_score": target_score,
        "game_start_time": None,
        "game_over_summary": None,
        "stats_recorded": False,
        "events": [],
        "event_seq": 0,
    }


def log(state, msg):
    state["log"].append(msg)
    state["log"] = state["log"][-8:]


def emit(state, event_type):
    state["event_seq"] += 1
    state["events"].append({"seq": state["event_seq"], "type": event_type})
    state["events"] = state["events"][-20:]


# ---------------------------------------------------------------------------
# Game engine
# ---------------------------------------------------------------------------
def start_new_round(state):
    state["deck"] = make_deck()
    deck = state["deck"]
    hands = {0: [], 1: [], 2: [], 3: []}
    order = [(state["dealer"] + 1 + i) % 4 for i in range(4)]

    if state["deal_type"] == "full":
        for i, seat in enumerate(order):
            hands[seat] = deck[i * 8:(i + 1) * 8]
        state["deck_remainder"] = []
    else:
        for i, seat in enumerate(order):
            hands[seat] = deck[i * 4:(i + 1) * 4]
        state["deck_remainder"] = deck[16:]

    state["hands"] = hands
    state["phase"] = "bidding"
    state["bid_value"] = MIN_BID - 1
    state["bid_seat"] = None
    state["active_bidders"] = {0, 1, 2, 3}
    state["turn"] = (state["dealer"] + 1) % 4
    state["trump_suit"] = None
    state["trump_seat"] = None
    state["current_trick"] = []
    state["trick_leader"] = None
    state["tricks_played"] = 0
    state["round_points"] = {0: 0, 1: 0}
    state["round_summary"] = None
    if state["game_start_time"] is None:
        state["game_start_time"] = time.time()
    log(state, f"New round. Dealer is seat {state['dealer'] + 1}.")
    emit(state, "deal")


def advance_bid_turn(state):
    active = state["active_bidders"]
    if len(active) <= 1:
        finish_bidding(state)
        return
    nxt = (state["turn"] + 1) % 4
    while nxt not in active:
        nxt = (nxt + 1) % 4
    state["turn"] = nxt


def finish_bidding(state):
    active = state["active_bidders"]
    if state["bid_seat"] is None:
        state["bid_seat"] = state["dealer"]
        state["bid_value"] = MIN_BID
        log(state, f"All passed. Dealer (seat {state['dealer']+1}) forced to bid {MIN_BID}.")
    else:
        winner = state["bid_seat"] if not active else next(iter(active))
        state["bid_seat"] = winner
    state["phase"] = "choose_trump"
    state["turn"] = state["bid_seat"]
    log(state, f"Seat {state['bid_seat']+1} won the bid at {state['bid_value']}.")


def do_bid(state, seat, value):
    state["bid_value"] = value
    state["bid_seat"] = seat
    log(state, f"Seat {seat + 1} bids {value}.")
    emit(state, "bid")
    advance_bid_turn(state)


def do_pass(state, seat):
    state["active_bidders"].discard(seat)
    log(state, f"Seat {seat + 1} passes.")
    emit(state, "pass")
    if state["bid_seat"] is not None and len(state["active_bidders"]) <= 1:
        finish_bidding(state)
    elif not state["active_bidders"]:
        finish_bidding(state)
    else:
        advance_bid_turn(state)


def choose_trump(state, suit):
    state["trump_suit"] = suit
    bidder = state["trump_seat"] = state["bid_seat"]

    if state["deal_type"] == "half":
        rem = state["deck_remainder"]
        order = [(state["dealer"] + 1 + i) % 4 for i in range(4)]
        for i, seat in enumerate(order):
            state["hands"][seat] += rem[i * 4:(i + 1) * 4]

    state["phase"] = "playing"
    leader = (state["dealer"] + 1) % 4
    state["trick_leader"] = leader
    state["turn"] = leader
    log(state, f"Seat {bidder + 1} sets trump: {suit}. Trump is now open.")
    emit(state, "trump_reveal")

    if state["game_mode"] == "royal_pair":
        for seat, hand in state["hands"].items():
            ranks_here = {card_rank(c) for c in hand if card_suit(c) == suit}
            if {"K", "Q"}.issubset(ranks_here):
                pteam = team_of(seat)
                state["round_points"][pteam] += 4
                log(state, f"Royal Pair! Seat {seat+1} holds K+Q of trump — "
                           f"{TEAM_LABEL[pteam]} +4 bonus points.")
                emit(state, "pair")
                break


def legal_moves(hand, led_suit):
    if led_suit is None:
        return hand
    matching = [c for c in hand if card_suit(c) == led_suit]
    return matching if matching else hand


def resolve_trick(state):
    trick = state["current_trick"]
    trump = state["trump_suit"]
    led_suit = card_suit(trick[0][1])
    trump_cards = [(s, c) for s, c in trick if card_suit(c) == trump]
    pool = trump_cards if trump_cards else [(s, c) for s, c in trick if card_suit(c) == led_suit]
    winner_seat, winner_card = max(pool, key=lambda sc: rank_value(card_rank(sc[1])))
    points = sum(RANK_POINTS[card_rank(c)] for _, c in trick)
    wteam = team_of(winner_seat)
    state["round_points"][wteam] += points
    log(state, f"Seat {winner_seat + 1} wins the trick (+{points} pts) for {TEAM_LABEL[wteam]}.")
    state["current_trick"] = []
    state["tricks_played"] += 1
    state["trick_leader"] = winner_seat
    state["turn"] = winner_seat
    if state["tricks_played"] == 8:
        finish_round(state)
    else:
        emit(state, "trick_win")


def finish_round(state):
    bidder_seat = state["bid_seat"]
    bidder_team = team_of(bidder_seat)
    other_team = 1 - bidder_team
    made = state["round_points"][bidder_team] >= state["bid_value"]

    for seat, p in state["players"].items():
        if p.get("bot"):
            continue
        auth.record_round(p["username"], seat == bidder_seat, made if seat == bidder_seat else None)

    if made:
        state["match_score"][bidder_team] += 1
        result = (f"{TEAM_LABEL[bidder_team]} made their bid of {state['bid_value']} "
                   f"(scored {state['round_points'][bidder_team]}). +1 match point.")
    else:
        state["match_score"][other_team] += 1
        result = (f"{TEAM_LABEL[bidder_team]} failed their bid of {state['bid_value']} "
                   f"(only scored {state['round_points'][bidder_team]}). "
                   f"{TEAM_LABEL[other_team]} +1 match point.")
    state["round_summary"] = result
    state["phase"] = "round_over"
    log(state, result)
    emit(state, "trick_win")

    if max(state["match_score"].values()) >= state["target_score"]:
        end_game(state)


def end_game(state):
    winner = 0 if state["match_score"][0] > state["match_score"][1] else 1
    loser = 1 - winner
    margin = state["match_score"][winner] - state["match_score"][loser]
    duration = time.time() - (state["game_start_time"] or time.time())

    team_a_users = [p["username"] for s, p in state["players"].items() if team_of(s) == 0 and not p.get("bot")]
    team_b_users = [p["username"] for s, p in state["players"].items() if team_of(s) == 1 and not p.get("bot")]

    if not state["stats_recorded"]:
        for seat, p in state["players"].items():
            if p.get("bot"):
                continue
            won = team_of(seat) == winner
            auth.record_game(p["username"], won, margin, duration)
        auth.save_game_history(
            state.get("room_code", ""), team_a_users, team_b_users,
            state["match_score"][0], state["match_score"][1],
            TEAM_LABEL[winner], margin, state["game_mode"], duration,
        )
        state["stats_recorded"] = True

    state["phase"] = "game_over"
    state["game_over_summary"] = {
        "winner": TEAM_LABEL[winner], "margin": margin, "duration": duration,
        "score_a": state["match_score"][0], "score_b": state["match_score"][1],
    }
    log(state, f"Game over! {TEAM_LABEL[winner]} wins by {margin} point(s).")
    emit(state, "game_win")


def play_card(state, seat, card):
    hand = state["hands"][seat]
    led_suit = card_suit(state["current_trick"][0][1]) if state["current_trick"] else None
    if card not in legal_moves(hand, led_suit):
        return False
    hand.remove(card)
    is_cut = (led_suit is not None and card_suit(card) == state["trump_suit"] and led_suit != state["trump_suit"])
    is_high = card_rank(card) == "J" and card_suit(card) == state["trump_suit"]
    state["current_trick"].append((seat, card))
    if len(state["current_trick"]) == 4:
        resolve_trick(state)
    else:
        state["turn"] = (state["turn"] + 1) % 4
        if is_high:
            emit(state, "high_card")
        elif is_cut:
            emit(state, "cut")
    return True


def bot_act(state, seat):
    if state["phase"] == "bidding":
        if _SECURE_RANDOM.random() < 0.55 or state["bid_value"] + 1 > MAX_BID:
            do_pass(state, seat)
        else:
            do_bid(state, seat, state["bid_value"] + 1)
    elif state["phase"] == "choose_trump" and state["turn"] == seat:
        hand = state["hands"][seat]
        suit_counts = {s: sum(1 for c in hand if card_suit(c) == s) for s in SUITS}
        best = max(suit_counts, key=suit_counts.get)
        choose_trump(state, best)
    elif state["phase"] == "playing" and state["turn"] == seat:
        led_suit = card_suit(state["current_trick"][0][1]) if state["current_trick"] else None
        moves = legal_moves(state["hands"][seat], led_suit)
        play_card(state, seat, _SECURE_RANDOM.choice(moves))


def run_bots(state):
    for _ in range(60):
        if state["phase"] in ("lobby", "round_over", "game_over"):
            return
        seat = state["turn"]
        player = state["players"].get(seat)
        if player and player.get("bot"):
            bot_act(state, seat)
        else:
            return


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="28 — Kerala Card Game", page_icon="🃏", layout="centered")

for key, default in [("username", None), ("room_code", None), ("nav", "Play"),
                      ("last_seen_seq", 0), ("pending_bid", {})]:
    if key not in st.session_state:
        st.session_state[key] = default

rooms = get_rooms()

st.title("🃏 28 — Irupathiyettu")
st.caption("The classic Kerala trick-taking card game, played online.")

# --- Login / register --------------------------------------------------------
if not st.session_state.username:
    tab1, tab2 = st.tabs(["Log in", "Register"])
    with tab1:
        u = st.text_input("Username", key="login_u")
        p = st.text_input("Password", type="password", key="login_p")
        if st.button("Log in", type="primary"):
            if auth.verify_user(u.strip(), p):
                st.session_state.username = u.strip()
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    with tab2:
        ru = st.text_input("Choose a username", key="reg_u")
        rd = st.text_input("Display name (optional)", key="reg_d")
        rp = st.text_input("Choose a password", type="password", key="reg_p")
        if st.button("Create account", type="primary"):
            ok, msg = auth.create_user(ru, rp, rd or ru)
            if ok:
                st.success(msg + " You can log in now.")
            else:
                st.error(msg)
    st.stop()

username = st.session_state.username
display_name = auth.get_display_name(username)

# --- Sidebar navigation -------------------------------------------------------
with st.sidebar:
    st.write(f"Logged in as **{display_name}**")
    st.session_state.nav = st.radio(
        "Menu", ["Play", "Profile", "Leaderboard"],
        index=["Play", "Profile", "Leaderboard"].index(st.session_state.nav),
    )
    if st.button("Log out"):
        st.session_state.username = None
        st.session_state.room_code = None
        st.rerun()

# --- Profile page --------------------------------------------------------
if st.session_state.nav == "Profile":
    st.header("Your profile")
    new_name = st.text_input("Display name", value=display_name)
    if st.button("Save name"):
        auth.set_display_name(username, new_name)
        st.success("Updated.")
        st.rerun()
    s = auth.get_stats(username)
    win_rate = (s["games_won"] / s["games_played"] * 100) if s["games_played"] else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Games played", s["games_played"])
    c2.metric("Games won", s["games_won"])
    c3.metric("Win rate", f"{win_rate:.0f}%")
    c4, c5, c6 = st.columns(3)
    c4.metric("Best win margin", s["best_margin"])
    c5.metric("Fastest win", f"{s['fastest_win_seconds']/60:.1f} min" if s["fastest_win_seconds"] else "—")
    c6.metric("Current streak", s["current_streak"])
    c7, c8 = st.columns(2)
    c7.metric("Best streak", s["best_streak"])
    bid_rate = (s["bids_won"] / s["bids_made"] * 100) if s["bids_made"] else 0
    c8.metric("Bid success rate", f"{bid_rate:.0f}% ({s['bids_won']}/{s['bids_made']})")
    st.caption(f"Total playtime: {s['total_playtime_seconds']/60:.1f} minutes")
    st.stop()

# --- Leaderboard page ------------------------------------------------------
if st.session_state.nav == "Leaderboard":
    st.header("🏆 Leaderboard")
    rows = auth.get_leaderboard()
    if not rows:
        st.info("No completed games yet — be the first!")
    else:
        st.table([
            {"Rank": i + 1, "Player": r[1], "Games": r[2], "Wins": r[3],
             "Win rate": f"{r[4]*100:.0f}%", "Best streak": r[5]}
            for i, r in enumerate(rows)
        ])
    st.stop()

# --- Play: join / create room -------------------------------------------------
if st.session_state.room_code is None:
    tab1, tab2 = st.tabs(["Join a room", "Create a room"])
    with tab1:
        code = st.text_input("Room code", max_chars=5).upper().strip()
        if st.button("Join", type="primary", disabled=not code):
            with _lock:
                if code not in rooms:
                    st.error("No room with that code.")
                else:
                    state = rooms[code]
                    seated = {s for s, p in state["players"].items() if p.get("username") == username}
                    if seated:
                        st.session_state.room_code = code
                        st.rerun()
                    else:
                        free_seats = [s for s in range(4) if s not in state["players"]]
                        if not free_seats:
                            st.error("Room is full.")
                        else:
                            seat = free_seats[0]
                            state["players"][seat] = {"username": username, "display_name": display_name, "bot": False}
                            st.session_state.room_code = code
                            st.rerun()
    with tab2:
        game_mode = st.selectbox("Mode", ["Classic", "Royal Pair"])
        deal_type = st.selectbox("Deal type", ["Half Deal (traditional)", "Full Deal"])
        target_score = st.number_input("Match points to win the game", min_value=1, max_value=20, value=6)
        if st.button("Create room", type="primary"):
            with _lock:
                code = new_room_code()
                while code in rooms:
                    code = new_room_code()
                state = new_room_state(
                    "royal_pair" if game_mode == "Royal Pair" else "classic",
                    "full" if deal_type == "Full Deal" else "half",
                    int(target_score),
                )
                state["room_code"] = code
                state["players"][0] = {"username": username, "display_name": display_name, "bot": False}
                rooms[code] = state
                st.session_state.room_code = code
                st.rerun()
    st.stop()

# --- In a room ---------------------------------------------------------------
code = st.session_state.room_code
if code not in rooms:
    st.error("This room no longer exists.")
    if st.button("Back to start"):
        st.session_state.room_code = None
        st.rerun()
    st.stop()

state = rooms[code]

if HAS_AUTOREFRESH:
    st_autorefresh(interval=2000, key="autorefresh")
else:
    st.button("🔄 Refresh")

my_seat = next((s for s, p in state["players"].items() if p.get("username") == username), None)

st.markdown(f"**Room code:** `{code}`  ·  mode: **{state['game_mode'].replace('_',' ').title()}**  ·  "
            f"deal: **{state['deal_type'].title()}**  ·  target: **{state['target_score']}**")

with _lock:
    run_bots(state)

# Play the most recent sound event this session hasn't seen yet
if state["events"] and state["events"][-1]["seq"] > st.session_state.last_seen_seq:
    last = state["events"][-1]
    st.markdown(sounds.audio_tag(last["type"], f"{code}_{last['seq']}"), unsafe_allow_html=True)
    st.session_state.last_seen_seq = last["seq"]

# --- Lobby ---------------------------------------------------------------
if state["phase"] == "lobby":
    st.subheader("Waiting room")
    cols = st.columns(4)
    for seat in range(4):
        with cols[seat]:
            p = state["players"].get(seat)
            team = "Team A" if seat in (0, 2) else "Team B"
            if p:
                tag = " 🤖" if p["bot"] else ""
                st.markdown(f"**Seat {seat+1}**{tag}\n\n{p['display_name']}\n\n*{team}*")
            else:
                st.markdown(f"**Seat {seat+1}**\n\n_empty_\n\n*{team}*")
                if st.button(f"Add bot to seat {seat+1}", key=f"bot_{seat}"):
                    with _lock:
                        state["players"][seat] = {"username": f"__bot_{seat}__", "display_name": f"Bot {seat+1}", "bot": True}
                    st.rerun()

    if len(state["players"]) == 4:
        if st.button("🚀 Start game", type="primary"):
            with _lock:
                start_new_round(state)
                run_bots(state)
            st.rerun()
    else:
        st.info("Need 4 seats filled (players or bots) to start.")
    st.stop()

if my_seat is None:
    st.warning("This room's game has already started and you're not seated in it.")
    st.stop()

my_team = team_of(my_seat)
st.markdown(f"You are **seat {my_seat+1}** ({display_name}) — **{TEAM_LABEL[my_team]}**")

score_cols = st.columns(2)
score_cols[0].metric("Team A match points", state["match_score"][0])
score_cols[1].metric("Team B match points", state["match_score"][1])

with st.expander("Recent activity", expanded=False):
    for line in state["log"]:
        st.write("• " + line)

# --- Bidding ---------------------------------------------------------------
if state["phase"] == "bidding":
    st.subheader("Bidding")
    st.write(f"Current bid: **{state['bid_value'] if state['bid_seat'] is not None else '—'}**"
             + (f" by seat {state['bid_seat']+1}" if state["bid_seat"] is not None else ""))
    st.write("Your hand:")
    st.write("  ".join(state["hands"][my_seat]))

    if state["turn"] == my_seat and my_seat in state["active_bidders"]:
        min_next = max(state["bid_value"] + 1, MIN_BID)
        pending = st.session_state.pending_bid.get(code)

        if pending is not None:
            st.warning(f"Confirm: bid **{pending}** points? High bids carry more risk.")
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Confirm bid", type="primary"):
                with _lock:
                    do_bid(state, my_seat, pending)
                    run_bots(state)
                st.session_state.pending_bid.pop(code, None)
                st.rerun()
            if cc2.button("❌ Cancel"):
                st.session_state.pending_bid.pop(code, None)
                st.rerun()
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                bid_choice = st.number_input("Your bid", min_value=min_next, max_value=MAX_BID, value=min_next, step=1)
            with c2:
                st.write("")
                st.write("")
                if st.button("Bid", type="primary"):
                    if bid_choice > BID_CONFIRM_THRESHOLD:
                        st.session_state.pending_bid[code] = int(bid_choice)
                        st.rerun()
                    else:
                        with _lock:
                            do_bid(state, my_seat, int(bid_choice))
                            run_bots(state)
                        st.rerun()
            if st.button("Pass"):
                with _lock:
                    do_pass(state, my_seat)
                    run_bots(state)
                st.rerun()
    else:
        st.info("Waiting for other players to bid...")
    st.stop()

# --- Choose trump ------------------------------------------------------------
if state["phase"] == "choose_trump":
    st.subheader("Trump selection")
    st.write(f"Seat {state['bid_seat']+1} won the bid at **{state['bid_value']}** and is choosing trump.")
    if state["bid_seat"] == my_seat:
        st.write("Your hand:")
        st.write("  ".join(state["hands"][my_seat]))
        suit = st.selectbox("Choose trump suit", SUITS)
        if st.button("Confirm trump", type="primary"):
            with _lock:
                choose_trump(state, suit)
                run_bots(state)
            st.rerun()
    else:
        st.info("Waiting for the bidder to choose trump...")
    st.stop()

# --- Playing -----------------------------------------------------------------
if state["phase"] == "playing":
    st.subheader(f"Playing — trump is {state['trump_suit']}  ·  bid {state['bid_value']} by seat {state['bid_seat']+1}")
    pts = state["round_points"]
    st.write(f"Tricks played: {state['tricks_played']}/8   ·   "
             f"Team A points: {pts[0]}   ·   Team B points: {pts[1]}")

    st.write("**Current trick:**")
    if state["current_trick"]:
        st.write("   ".join(f"seat {s+1}: {c}" for s, c in state["current_trick"]))
    else:
        st.write("_(none yet)_")

    st.write("**Your hand:**")
    led_suit = card_suit(state["current_trick"][0][1]) if state["current_trick"] else None
    moves = legal_moves(state["hands"][my_seat], led_suit) if state["turn"] == my_seat else []
    cols = st.columns(len(state["hands"][my_seat]) or 1)
    for i, c in enumerate(state["hands"][my_seat]):
        playable = c in moves
        with cols[i]:
            if state["turn"] == my_seat and playable:
                if st.button(c, key=f"card_{c}_{i}"):
                    with _lock:
                        play_card(state, my_seat, c)
                        run_bots(state)
                    st.rerun()
            else:
                st.write(c)
    if state["turn"] != my_seat:
        st.info(f"Waiting for seat {state['turn']+1} to play...")
    st.stop()

# --- Round over ----------------------------------------------------------
if state["phase"] == "round_over":
    st.subheader("Round over")
    st.success(state["round_summary"])
    if my_seat == 0 or state["players"][0]["bot"]:
        if st.button("Deal next round ▶️", type="primary"):
            with _lock:
                state["dealer"] = (state["dealer"] + 1) % 4
                start_new_round(state)
                run_bots(state)
            st.rerun()
    else:
        st.info("Waiting for seat 1 to start the next round...")
    st.stop()

# --- Game over -------------------------------------------------------------
if state["phase"] == "game_over":
    summary = state["game_over_summary"]
    st.subheader("🏁 Game over!")
    st.success(f"{summary['winner']} wins by {summary['margin']} match point(s)! "
               f"Final score: Team A {summary['score_a']} — Team B {summary['score_b']}")
    st.write(f"Game duration: **{summary['duration']/60:.1f} minutes**")

    st.write("### Updated stats")
    cols = st.columns(4)
    for i, seat in enumerate(range(4)):
        p = state["players"][seat]
        with cols[i]:
            if p.get("bot"):
                st.write(f"**{p['display_name']}** (bot)")
                continue
            s = auth.get_stats(p["username"])
            wr = (s["games_won"] / s["games_played"] * 100) if s["games_played"] else 0
            st.write(f"**{p['display_name']}**")
            st.caption(f"{s['games_won']}/{s['games_played']} wins ({wr:.0f}%) · streak {s['current_streak']}")

    if my_seat == 0 or state["players"][0]["bot"]:
        if st.button("Play again (same room) ▶️", type="primary"):
            with _lock:
                state["match_score"] = {0: 0, 1: 0}
                state["dealer"] = 0
                state["game_start_time"] = None
                state["stats_recorded"] = False
                state["phase"] = "lobby"
            st.rerun()
    if st.button("Leave room"):
        st.session_state.room_code = None
        st.rerun()
    st.stop()
