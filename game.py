"""
Pure game engine for 28 (Irupathiyettu). Deliberately has ZERO imports of
streamlit, auth, or ui — this module only knows about cards, seats, and
rules. It can be unit tested or reused by a completely different frontend
without dragging in Streamlit at all.

Persistence is decoupled too: instead of calling the database directly,
round/game outcomes are appended to state["pending_stats"] as plain dicts.
Whatever's driving the game (currently app.py) is responsible for reading
that queue and calling the storage layer, then clearing it.

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

Bot pacing: bot_step() performs AT MOST ONE bot action per call, by design —
callers should call it once per UI refresh tick so bots play card-by-card
at a visible pace instead of instantly resolving a whole hand.
"""

import random
import string
import time

SUITS = ["♠", "♥", "♦", "♣"]
RANK_ORDER = ["7", "8", "Q", "K", "10", "A", "9", "J"]  # low -> high, per suit
RANK_POINTS = {"J": 3, "9": 2, "A": 1, "10": 1, "K": 0, "Q": 0, "8": 0, "7": 0}
MIN_BID = 14
MAX_BID = 28
BID_CONFIRM_THRESHOLD = 19
TEAM_LABEL = {0: "Team A", 1: "Team B"}

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


def new_room_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


def new_room_state(game_mode, deal_type, target_score):
    return {
        "players": {},       # seat(int) -> {"username","display_name","bot","profile_pic"}
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
        "pending_stats": [],
    }


def log(state, msg):
    state["log"].append(msg)
    state["log"] = state["log"][-8:]


def emit(state, event_type):
    state["event_seq"] += 1
    state["events"].append({"seq": state["event_seq"], "type": event_type})
    state["events"] = state["events"][-20:]


def drain_pending_stats(state):
    items = state["pending_stats"]
    state["pending_stats"] = []
    return items


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
        state["pending_stats"].append({
            "type": "round", "username": p["username"],
            "was_bidder": seat == bidder_seat,
            "bid_made": made if seat == bidder_seat else None,
        })

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
            state["pending_stats"].append({
                "type": "game", "username": p["username"], "won": won,
                "margin": margin, "duration": duration,
            })
        state["pending_stats"].append({
            "type": "history", "room_code": state.get("room_code", ""),
            "team_a": team_a_users, "team_b": team_b_users,
            "score_a": state["match_score"][0], "score_b": state["match_score"][1],
            "winner": TEAM_LABEL[winner], "margin": margin,
            "mode": state["game_mode"], "duration": duration,
        })
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


def _bot_act(state, seat):
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


def bot_step(state):
    """Perform AT MOST ONE bot action, if it's currently a bot's turn.
    Deliberately does not loop through multiple bot turns in a row — the
    caller should invoke this once per UI refresh so bots play one card
    (or make one bid) at a time, at a human-visible pace, never fast-forwarding
    through a whole hand in one tick.

    Returns True if a bot acted, False otherwise (nothing to do right now).
    """
    if state["phase"] in ("lobby", "round_over", "game_over"):
        return False
    seat = state["turn"]
    player = state["players"].get(seat)
    if player and player.get("bot"):
        _bot_act(state, seat)
        return True
    return False
