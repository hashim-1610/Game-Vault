"""
28 (Irupathiyettu) — Kerala card game, online multiplayer.

This file is intentionally "thin": it only wires Streamlit widgets to the
pure game engine (game.py), the presentation layer (ui.py), and the
persistence layer (auth.py). No game rules and no HTML live here — if
you're looking for scoring/dealing/trump logic, see game.py; if you're
looking for colors/layout/fonts, see ui.py.

Bot pacing: bots play one action per refresh tick (game.bot_step), so a
hand against 3 bots is never fast-forwarded — you can watch every bid and
every card land, same as a hand against humans.
"""

import threading
import time

import streamlit as st

import auth
import config
import game
import sounds
import ui

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

TEAM_LABEL = game.TEAM_LABEL
_lock = threading.Lock()


@st.cache_resource
def get_rooms():
    return {}


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(page_title="28 — Kerala Card Game", page_icon="🃏", layout="centered")
ui.inject()

for key, default in [("username", None), ("room_code", None), ("nav", "Play"),
                      ("last_seen_seq", 0), ("pending_bid", {})]:
    if key not in st.session_state:
        st.session_state[key] = default

rooms = get_rooms()

st.title("🃏 28 — Irupathiyettu")
st.caption("The classic Kerala trick-taking card game, played online.")
ui.kasavu_rule()

# --- Login / register --------------------------------------------------------
if not st.session_state.username:
    st.markdown('<div class="cream-panel">', unsafe_allow_html=True)
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
        st.caption("Registration requires an invite code — ask whoever's hosting this game for it.")
        ru = st.text_input("Choose a username", key="reg_u")
        rd = st.text_input("Display name (optional)", key="reg_d")
        rp = st.text_input("Choose a password", type="password", key="reg_p")
        rp2 = st.text_input("Confirm password", type="password", key="reg_p2")
        rinv = st.text_input("Invite code", key="reg_inv")
        rpic = st.file_uploader("Profile picture (optional)", type=["png", "jpg", "jpeg"], key="reg_pic")
        if st.button("Create account", type="primary"):
            pic_bytes = rpic.getvalue() if rpic else None
            if pic_bytes and len(pic_bytes) > config.PROFILE_PIC_MAX_UPLOAD_MB * 1024 * 1024:
                st.error(f"Image too large — keep it under {config.PROFILE_PIC_MAX_UPLOAD_MB} MB.")
            else:
                ok, msg = auth.create_user(ru, rp, rp2, rd or ru, rinv, pic_bytes)
                if ok:
                    st.success(msg + " You can log in now.")
                else:
                    st.error(msg)
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

username = st.session_state.username
display_name = auth.get_display_name(username)
my_pic = auth.get_profile_pic(username)

# --- Sidebar navigation -------------------------------------------------------
with st.sidebar:
    st.markdown(ui.avatar_html(display_name, my_pic, size=52), unsafe_allow_html=True)
    st.write(f"**{display_name}**")
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
    ui.kasavu_rule()
    st.markdown('<div class="cream-panel">', unsafe_allow_html=True)
    st.markdown(ui.avatar_html(display_name, my_pic, size=80), unsafe_allow_html=True)
    new_name = st.text_input("Display name", value=display_name)
    new_pic = st.file_uploader("Update profile picture", type=["png", "jpg", "jpeg"], key="profile_pic_update")
    c1, c2 = st.columns(2)
    if c1.button("Save name"):
        auth.set_display_name(username, new_name)
        st.success("Updated.")
        st.rerun()
    if c2.button("Save picture", disabled=new_pic is None):
        if new_pic and auth.set_profile_pic(username, new_pic.getvalue()):
            st.success("Updated.")
            st.rerun()
        elif new_pic:
            st.error("Couldn't process that image — try a different file.")

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
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Leaderboard page ------------------------------------------------------
if st.session_state.nav == "Leaderboard":
    st.header("🏆 Leaderboard")
    ui.kasavu_rule()
    st.markdown('<div class="cream-panel">', unsafe_allow_html=True)
    rows = auth.get_leaderboard()
    if not rows:
        st.info("No completed games yet — be the first!")
    else:
        for i, r in enumerate(rows):
            uname, dname, played, wins, win_rate, streak, pic = r
            cols = st.columns([0.6, 3, 1.5, 1.5, 1.5, 1.5])
            cols[0].markdown(f"**#{i+1}**")
            cols[1].markdown(ui.avatar_html(dname, pic, size=32) + f" &nbsp; **{dname}**", unsafe_allow_html=True)
            cols[2].write(f"{played} games")
            cols[3].write(f"{wins} wins")
            cols[4].write(f"{win_rate*100:.0f}%")
            cols[5].write(f"streak {streak}")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Play: join / create room -------------------------------------------------
if st.session_state.room_code is None:
    st.markdown('<div class="cream-panel">', unsafe_allow_html=True)
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
                code = game.new_room_code()
                while code in rooms:
                    code = game.new_room_code()
                state = game.new_room_state(
                    "royal_pair" if game_mode == "Royal Pair" else "classic",
                    "full" if deal_type == "Full Deal" else "half",
                    int(target_score),
                )
                state["room_code"] = code
                state["players"][0] = {"username": username, "display_name": display_name, "bot": False}
                rooms[code] = state
                st.session_state.room_code = code
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
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
    st_autorefresh(interval=1800, key="autorefresh")
else:
    st.button("🔄 Refresh")

my_seat = next((s for s, p in state["players"].items() if p.get("username") == username), None)

st.subheader(f"Room `{code}`")
ui.badges(
    state["game_mode"].replace("_", " ").title(),
    f"{state['deal_type'].title()} Deal",
    f"Target {state['target_score']}",
)

with _lock:
    # ONE bot action per refresh tick — never fast-forward a whole hand.
    acted = game.bot_step(state)
    if acted:
        auth.apply_pending_stats(game.drain_pending_stats(state))

# Play the most recent sound event this session hasn't seen yet
if state["events"] and state["events"][-1]["seq"] > st.session_state.last_seen_seq:
    last = state["events"][-1]
    st.markdown(sounds.audio_tag(last["type"], f"{code}_{last['seq']}"), unsafe_allow_html=True)
    st.session_state.last_seen_seq = last["seq"]

# --- Lobby ---------------------------------------------------------------
if state["phase"] == "lobby":
    st.markdown('<div class="cream-panel">', unsafe_allow_html=True)
    st.markdown("#### Waiting room")
    cols = st.columns(4)
    for seat in range(4):
        with cols[seat]:
            p = state["players"].get(seat)
            team = "Team A" if seat in (0, 2) else "Team B"
            if p:
                tag = " 🤖" if p["bot"] else ""
                marker = " ⭐" if p.get("username") == username else ""
                pic = None if p["bot"] else auth.get_profile_pic(p["username"])
                st.markdown(ui.avatar_html(p["display_name"], pic, size=52), unsafe_allow_html=True)
                st.markdown(f"**Seat {seat+1}**{tag}{marker}\n\n{p['display_name']}\n\n*{team}*")
            else:
                st.markdown(f"**Seat {seat+1}**\n\n_empty_\n\n*{team}*")
                if st.button("Add bot", key=f"bot_{seat}"):
                    with _lock:
                        state["players"][seat] = {"username": f"__bot_{seat}__", "display_name": f"Bot {seat+1}", "bot": True}
                    st.rerun()

    if len(state["players"]) == 4:
        if st.button("🚀 Start game", type="primary"):
            with _lock:
                game.start_new_round(state)
            st.rerun()
    else:
        st.info("Need 4 seats filled (players or bots) to start.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

if my_seat is None:
    st.warning("This room's game has already started and you're not seated in it.")
    st.stop()

my_team = game.team_of(my_seat)
profile_pics = {
    s: (None if p.get("bot") else auth.get_profile_pic(p["username"]))
    for s, p in state["players"].items()
}
ui.seat_ring(state["players"], my_seat, state["turn"], state["dealer"],
             lambda s: TEAM_LABEL[game.team_of(s)], profile_pics)

score_cols = st.columns(2)
score_cols[0].metric("Team A match points", state["match_score"][0])
score_cols[1].metric("Team B match points", state["match_score"][1])

with st.expander("Recent activity", expanded=False):
    for line in state["log"]:
        st.write("• " + line)

# --- Bidding ---------------------------------------------------------------
if state["phase"] == "bidding":
    st.markdown('<div class="table-panel">', unsafe_allow_html=True)
    st.markdown("#### Bidding")
    st.markdown(f"Current bid: **{state['bid_value'] if state['bid_seat'] is not None else '—'}**"
                + (f" by seat {state['bid_seat']+1}" if state["bid_seat"] is not None else ""))
    st.markdown("**Your hand**")
    ui.render_card_row(state["hands"][my_seat])

    if state["turn"] == my_seat and my_seat in state["active_bidders"]:
        min_next = max(state["bid_value"] + 1, game.MIN_BID)
        pending = st.session_state.pending_bid.get(code)

        if pending is not None:
            st.warning(f"Confirm: bid **{pending}** points? High bids carry more risk.")
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Confirm bid", type="primary"):
                with _lock:
                    game.do_bid(state, my_seat, pending)
                st.session_state.pending_bid.pop(code, None)
                st.rerun()
            if cc2.button("❌ Cancel"):
                st.session_state.pending_bid.pop(code, None)
                st.rerun()
        else:
            st.markdown('<div class="bid-panel-header">Your Bid</div>', unsafe_allow_html=True)
            ui.stake_pills()
            values = list(range(min_next, game.MAX_BID + 1))
            for row_start in range(0, len(values), 7):
                row_vals = values[row_start:row_start + 7]
                row_cols = st.columns(len(row_vals))
                for rc, val in zip(row_cols, row_vals):
                    if rc.button(str(val), key=f"bidval_{val}"):
                        if val > game.BID_CONFIRM_THRESHOLD:
                            st.session_state.pending_bid[code] = val
                        else:
                            with _lock:
                                game.do_bid(state, my_seat, val)
                        st.rerun()
            if st.button("Pass", key="pass_btn"):
                with _lock:
                    game.do_pass(state, my_seat)
                st.rerun()
    else:
        st.info("Waiting for other players to bid...")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Choose trump ------------------------------------------------------------
if state["phase"] == "choose_trump":
    st.markdown('<div class="table-panel">', unsafe_allow_html=True)
    st.markdown("#### Trump selection")
    st.markdown(f"Seat {state['bid_seat']+1} won the bid at **{state['bid_value']}** and is choosing trump.")
    if state["bid_seat"] == my_seat:
        st.markdown("**Your hand**")
        ui.render_card_row(state["hands"][my_seat])
        suit = st.selectbox("Choose trump suit", game.SUITS)
        if st.button("Confirm trump", type="primary"):
            with _lock:
                game.choose_trump(state, suit)
            st.rerun()
    else:
        st.info("Waiting for the bidder to choose trump...")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Playing -----------------------------------------------------------------
if state["phase"] == "playing":
    st.markdown('<div class="table-panel">', unsafe_allow_html=True)
    st.markdown("#### Playing")
    ui.badges(f"Trump {state['trump_suit']}", f"Bid {state['bid_value']} · seat {state['bid_seat']+1}",
              f"Trick {state['tricks_played']+1}/8")
    pts = state["round_points"]
    tc1, tc2 = st.columns(2)
    tc1.metric("Team A points", pts[0])
    tc2.metric("Team B points", pts[1])

    st.markdown("**Current trick**")
    if state["current_trick"]:
        seat_labels = {s: state["players"][s]["display_name"] for s in state["players"]}
        ui.render_trick(state["current_trick"], seat_labels)
    else:
        st.caption("No cards played yet this trick.")

    st.markdown("**Your hand**")
    led_suit = game.card_suit(state["current_trick"][0][1]) if state["current_trick"] else None
    moves = game.legal_moves(state["hands"][my_seat], led_suit) if state["turn"] == my_seat else []
    hand = state["hands"][my_seat]
    cols = st.columns(len(hand) or 1)
    for i, c in enumerate(hand):
        playable = state["turn"] == my_seat and c in moves
        with cols[i]:
            st.markdown(ui.card_html(c, dim=not playable), unsafe_allow_html=True)
            if st.button("Play", key=f"card_{c}_{i}", disabled=not playable):
                with _lock:
                    game.play_card(state, my_seat, c)
                st.rerun()
    if state["turn"] != my_seat:
        st.info(f"Waiting for seat {state['turn']+1} to play...")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Round over ----------------------------------------------------------
if state["phase"] == "round_over":
    st.markdown('<div class="table-panel">', unsafe_allow_html=True)
    st.markdown("#### Round over")
    st.success(state["round_summary"])
    if my_seat == 0 or state["players"][0]["bot"]:
        if st.button("Deal next round ▶️", type="primary"):
            with _lock:
                state["dealer"] = (state["dealer"] + 1) % 4
                game.start_new_round(state)
            st.rerun()
    else:
        st.info("Waiting for seat 1 to start the next round...")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# --- Game over -------------------------------------------------------------
if state["phase"] == "game_over":
    summary = state["game_over_summary"]
    st.markdown('<div class="table-panel">', unsafe_allow_html=True)
    st.markdown("#### 🏁 Game over!")
    st.success(f"{summary['winner']} wins by {summary['margin']} match point(s)! "
               f"Final score: Team A {summary['score_a']} — Team B {summary['score_b']}")
    st.markdown(f"Game duration: **{summary['duration']/60:.1f} minutes**")

    st.markdown("**Updated stats**")
    cols = st.columns(4)
    for i, seat in enumerate(range(4)):
        p = state["players"][seat]
        with cols[i]:
            if p.get("bot"):
                st.markdown(f"**{p['display_name']}** (bot)")
                continue
            s = auth.get_stats(p["username"])
            wr = (s["games_won"] / s["games_played"] * 100) if s["games_played"] else 0
            st.markdown(f"**{p['display_name']}**")
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
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()
