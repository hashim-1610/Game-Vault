"""
FastAPI backend for 28 (Irupathiyettu).

This is the orchestration layer only — same role app.py played for the
Streamlit version. Game rules live entirely in game.py (unchanged, still
zero Streamlit/FastAPI coupling). Persistence lives in auth.py. This file
just wires HTTP/WebSocket requests to those two modules and pushes state
to connected clients in real time.

Real-time model: each room has one WebSocket per connected player. Any
action (bid, pass, play a card, etc.) mutates the room's state under an
asyncio.Lock, then the new state is broadcast to every connection in that
room — each player gets their OWN hand and a filtered view (opponents'
hands are never sent over the wire, only their card counts).

Bot pacing: bot_loop() below performs one bot action, waits a randomized
interval (config.BOT_DELAY_MIN/MAX_SECONDS), checks again, and stops as
soon as it's not a bot's turn. This is a real server-side delay, not a
client polling trick — bots always play one card/bid at a time, visibly,
never a whole hand at once.
"""

import asyncio
import os
import random
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, UploadFile, Form, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

import auth
import bots
import config
import game
import sounds

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
GENERATED_SOUNDS_DIR = os.path.join(BASE_DIR, "generated_sounds")

app = FastAPI(title="28 — Kerala Card Game")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory room store (single-process, same model the Streamlit version used)
# ---------------------------------------------------------------------------
ROOMS = {}                 # room_code -> state dict (from game.new_room_state)
ROOM_LOCKS = {}            # room_code -> asyncio.Lock
BOT_TASKS = {}             # room_code -> asyncio.Task


def get_room_lock(room_code):
    lock = ROOM_LOCKS.get(room_code)
    if lock is None:
        lock = asyncio.Lock()
        ROOM_LOCKS[room_code] = lock
    return lock


def seat_of_username(state, username):
    for s, p in state["players"].items():
        if p.get("username") == username:
            return s
    return None


def can_control_pacing(state, username):
    """Mirrors the original Streamlit permission: seat 0 controls
    'deal next round' / 'play again', unless seat 0 is a bot, in which
    case any seated human can."""
    seat0 = state["players"].get(0)
    if seat0 and seat0.get("bot"):
        return seat_of_username(state, username) is not None
    my_seat = seat_of_username(state, username)
    return my_seat == 0


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.by_room = {}  # room_code -> {username: WebSocket}

    async def connect(self, room_code, username, ws):
        await ws.accept()
        self.by_room.setdefault(room_code, {})[username] = ws

    def disconnect(self, room_code, username):
        conns = self.by_room.get(room_code)
        if conns and conns.get(username):
            conns.pop(username, None)

    async def broadcast(self, room_code, state):
        conns = self.by_room.get(room_code, {})
        for username, ws in list(conns.items()):
            seat = seat_of_username(state, username)
            payload = serialize_state(state, seat)
            payload["can_control_pacing"] = can_control_pacing(state, username)
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(room_code, username)


manager = ConnectionManager()


HAND_SUIT_ORDER = ["♠", "♣", "♥", "♦"]  # left-to-right display order in the hand row


def sorted_hand(hand):
    """Group by suit, ascending rank within each suit — much easier to scan
    than raw deal order. Display order is independent of game.SUITS (which
    drives trump-selection order elsewhere)."""
    return sorted(hand, key=lambda c: (HAND_SUIT_ORDER.index(game.card_suit(c)), game.rank_value(game.card_rank(c))))


def serialize_state(state, my_seat):
    hands = state["hands"]
    hand_counts = {s: len(hands.get(s, [])) for s in range(4)}
    my_hand = sorted_hand(hands.get(my_seat, [])) if my_seat is not None else []
    led_suit = game.card_suit(state["current_trick"][0][1]) if state["current_trick"] else None
    legal = []
    if my_seat is not None and state["phase"] == "playing" and state["turn"] == my_seat:
        legal = game.legal_moves(my_hand, led_suit)

    players_public = {}
    for s in range(4):
        p = state["players"].get(s)
        if p:
            players_public[str(s)] = {
                "display_name": p["display_name"],
                "bot": bool(p.get("bot", False)),
                "stand_in": bool(p.get("stand_in", False)),
                "profile_pic": p.get("profile_pic"),
                "avatar_seed": p.get("avatar_seed"),
                "is_me": (s == my_seat),
            }
        else:
            players_public[str(s)] = None

    return {
        "type": "state",
        "room_code": state.get("room_code"),
        "phase": state["phase"],
        "my_seat": my_seat,
        "dealer": state["dealer"],
        "turn": state["turn"],
        "players": players_public,
        "hand": my_hand,
        "hand_counts": {str(k): v for k, v in hand_counts.items()},
        "bid_value": state["bid_value"],
        "bid_seat": state["bid_seat"],
        "min_bid": game.MIN_BID,
        "max_bid": game.MAX_BID,
        "bid_confirm_threshold": game.BID_CONFIRM_THRESHOLD,
        "active_bidders": sorted(state["active_bidders"]),
        "trump_suit": state["trump_suit"],
        "current_trick": [[s, c] for s, c in state["current_trick"]],
        "trick_winner": state.get("trick_winner"),
        "trick_pending_clear": state.get("trick_pending_clear", False),
        "tricks_played": state["tricks_played"],
        "round_points": state["round_points"],
        "match_score": state["match_score"],
        "target_score": state["target_score"],
        "game_mode": state["game_mode"],
        "deal_type": state["deal_type"],
        "round_summary": state["round_summary"],
        "round_bidder_team": state.get("round_bidder_team"),
        "round_bid_made": state.get("round_bid_made"),
        "game_over_summary": state["game_over_summary"],
        "game_winner_team": state.get("game_winner_team"),
        "log": state["log"][-8:],
        "legal_moves": legal,
        "last_event": state["events"][-1]["type"] if state["events"] else None,
        "last_event_seq": state["events"][-1]["seq"] if state["events"] else 0,
    }


# ---------------------------------------------------------------------------
# Bot pacing loop — one action at a time, real delay between each
# ---------------------------------------------------------------------------
async def bot_loop(room_code):
    while True:
        async with get_room_lock(room_code):
            state = ROOMS.get(room_code)
            if not state:
                return
            seat = state.get("turn")
            player = state["players"].get(seat) if seat is not None else None
            is_bot_turn = bool(player and player.get("bot")) and state["phase"] not in (
                "lobby", "round_over", "game_over",
            )
        if not is_bot_turn:
            return

        await asyncio.sleep(random.uniform(config.BOT_DELAY_MIN_SECONDS, config.BOT_DELAY_MAX_SECONDS))

        async with get_room_lock(room_code):
            state = ROOMS.get(room_code)
            if not state:
                return
            try:
                acted = game.bot_step(state)
            except Exception as e:
                # A crash here used to kill the whole loop silently, which
                # from the player's side is indistinguishable from "the game
                # froze". Log it and bail out of this tick instead.
                print(f"[bot_step error] room={room_code}: {type(e).__name__}: {e}")
                return
            items = game.drain_pending_stats(state) if acted else []
            trick_pending = state.get("trick_pending_clear", False)

        if items:
            await run_in_threadpool(auth.apply_pending_stats, items)
        if not acted:
            return
        await manager.broadcast(room_code, state)

        if trick_pending:
            # The bot just played the 4th card: resolve_trick set turn=None
            # and is waiting on a timed clear. Nothing else schedules that
            # for bot-completed tricks (handle_action only covers the human
            # case), so without this the table sits on a full trick forever.
            schedule_task(finish_trick_after_delay(room_code))
            return


# asyncio only holds a weak reference to running tasks, so a fire-and-forget
# create_task() can be garbage collected mid-flight. Keep a strong reference
# until it finishes.
_BACKGROUND_TASKS = set()


def schedule_task(coro):
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def ensure_bot_loop(room_code):
    t = BOT_TASKS.get(room_code)
    if t is None or t.done():
        BOT_TASKS[room_code] = asyncio.create_task(bot_loop(room_code))


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def get_current_username(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.split(" ", 1)[1]
    username = await run_in_threadpool(auth.get_username_for_token, token)
    if not username:
        raise HTTPException(401, "Invalid or expired session")
    return username


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await run_in_threadpool(auth.init_db)
    await run_in_threadpool(sounds.write_all, GENERATED_SOUNDS_DIR)


# ---------------------------------------------------------------------------
# Auth REST endpoints
# ---------------------------------------------------------------------------
class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LoginBody):
    ok = await run_in_threadpool(auth.verify_user, body.username.strip(), body.password)
    if not ok:
        raise HTTPException(401, "Incorrect username or password.")
    username = body.username.strip()
    token = await run_in_threadpool(auth.create_session, username)
    user = await run_in_threadpool(auth.get_user_public, username)
    return {"token": token, **user}


@app.post("/api/logout")
async def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        await run_in_threadpool(auth.delete_session, authorization.split(" ", 1)[1])
    return {"ok": True}


@app.post("/api/register")
async def register(
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    display_name: str = Form(""),
    invite_code: str = Form(...),
    profile_pic: UploadFile = File(None),
):
    pic_bytes = None
    if profile_pic is not None and profile_pic.filename:
        pic_bytes = await profile_pic.read()
        if len(pic_bytes) > config.PROFILE_PIC_MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(400, f"Image too large — keep it under {config.PROFILE_PIC_MAX_UPLOAD_MB} MB.")
    ok, msg = await run_in_threadpool(
        auth.create_user, username, password, confirm_password, display_name, invite_code, pic_bytes,
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@app.get("/api/me")
async def get_me(username: str = Depends(get_current_username)):
    user = await run_in_threadpool(auth.get_user_public, username)
    stats = await run_in_threadpool(auth.get_stats, username)
    return {"user": user, "stats": stats}


@app.patch("/api/profile")
async def update_profile(display_name: str = Form(...), username: str = Depends(get_current_username)):
    await run_in_threadpool(auth.set_display_name, username, display_name)
    return {"ok": True}


@app.post("/api/profile/picture")
async def update_picture(profile_pic: UploadFile = File(...), username: str = Depends(get_current_username)):
    pic_bytes = await profile_pic.read()
    if len(pic_bytes) > config.PROFILE_PIC_MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Image too large — keep it under {config.PROFILE_PIC_MAX_UPLOAD_MB} MB.")
    ok = await run_in_threadpool(auth.set_profile_pic, username, pic_bytes)
    if not ok:
        raise HTTPException(400, "Couldn't process that image.")
    return {"ok": True}


@app.get("/api/leaderboard")
async def leaderboard(username: str = Depends(get_current_username)):
    rows = await run_in_threadpool(auth.get_leaderboard)
    return {"rows": rows}


@app.get("/api/config")
async def get_config():
    return {
        "min_bid": game.MIN_BID,
        "max_bid": game.MAX_BID,
        "bid_confirm_threshold": game.BID_CONFIRM_THRESHOLD,
        "suits": game.SUITS,
    }


# ---------------------------------------------------------------------------
# Room REST endpoints
# ---------------------------------------------------------------------------
class CreateRoomBody(BaseModel):
    game_mode: str = "classic"
    deal_type: str = "half"
    target_score: int = 6


@app.post("/api/rooms")
async def create_room(body: CreateRoomBody, username: str = Depends(get_current_username)):
    user = await run_in_threadpool(auth.get_user_public, username)
    code = game.new_room_code()
    while code in ROOMS:
        code = game.new_room_code()
    state = game.new_room_state(body.game_mode, body.deal_type, body.target_score)
    state["room_code"] = code
    state["players"][0] = {
        "username": username, "display_name": user["display_name"],
        "profile_pic": user["profile_pic"], "bot": False,
    }
    ROOMS[code] = state
    return {"code": code}


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, username: str = Depends(get_current_username)):
    state = ROOMS.get(code)
    if not state:
        raise HTTPException(404, "No room with that code.")
    async with get_room_lock(code):
        existing = seat_of_username(state, username)
        if existing is not None:
            return {"seat": existing}
        free_seats = [s for s in range(4) if s not in state["players"]]
        if not free_seats:
            raise HTTPException(409, "Room is full.")
        user = await run_in_threadpool(auth.get_user_public, username)
        seat = free_seats[0]
        state["players"][seat] = {
            "username": username, "display_name": user["display_name"],
            "profile_pic": user["profile_pic"], "bot": False,
        }
    # Everyone already sitting at this table needs to see the new arrival —
    # without this their lobby keeps showing the seat as empty until some
    # unrelated action happens to trigger the next broadcast.
    await manager.broadcast(code, state)
    return {"seat": seat}


@app.get("/api/rooms/{code}")
async def room_info(code: str, username: str = Depends(get_current_username)):
    state = ROOMS.get(code)
    if not state:
        raise HTTPException(404, "No room with that code.")
    return {
        "code": code, "phase": state["phase"], "game_mode": state["game_mode"],
        "deal_type": state["deal_type"], "target_score": state["target_score"],
        "players": {str(s): (p and {"display_name": p["display_name"], "bot": p.get("bot", False)})
                    for s, p in state["players"].items()},
    }


# ---------------------------------------------------------------------------
# WebSocket — one connection per player per room
# ---------------------------------------------------------------------------
@app.websocket("/ws/rooms/{code}")
async def room_ws(websocket: WebSocket, code: str, token: str = None):
    username = await run_in_threadpool(auth.get_username_for_token, token)
    if not username:
        await websocket.close(code=4401)
        return
    state = ROOMS.get(code)
    if not state:
        await websocket.close(code=4404)
        return
    if seat_of_username(state, username) is None:
        await websocket.close(code=4403)
        return

    # If this seat was auto-taken-over by a bot after a disconnect, hand it
    # back now that the original player has reconnected.
    reclaimed = False
    async with get_room_lock(code):
        state = ROOMS.get(code)
        my_seat = seat_of_username(state, username) if state else None
        player = state["players"].get(my_seat) if state and my_seat is not None else None
        if player and player.get("stand_in"):
            player["bot"] = False
            player["stand_in"] = False
            game.log(state, f"{player['display_name']} reconnected — back in seat {my_seat + 1}.")
            reclaimed = True

    await manager.connect(code, username, websocket)
    my_seat = seat_of_username(state, username)
    payload = serialize_state(state, my_seat)
    payload["can_control_pacing"] = can_control_pacing(state, username)
    await websocket.send_json(payload)
    # Push the room to everyone else too, so a newly-seated (or returning)
    # player shows up on the other clients right away rather than whenever
    # the next unrelated action happens to broadcast.
    await manager.broadcast(code, state)

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                # Malformed message from this client — log and keep the
                # connection alive rather than dropping them over one bad frame.
                print(f"[ws receive error] user={username}: {type(e).__name__}: {e}")
                continue
            try:
                await handle_action(code, username, msg)
            except Exception as e:
                # A bug in one action must never silently kill the socket —
                # that's what "the game got stuck" looks like from the
                # player's side. Log it, keep playing.
                print(f"[handle_action error] action={msg.get('action')} user={username}: {type(e).__name__}: {e}")
    finally:
        manager.disconnect(code, username)
        # Mid-game disconnects hand the seat to a bot so the table isn't
        # stuck waiting on someone who's gone — reconnecting (above) hands
        # it straight back.
        stand_in_taken = False
        async with get_room_lock(code):
            state = ROOMS.get(code)
            seat = seat_of_username(state, username) if state else None
            player = state["players"].get(seat) if state and seat is not None else None
            if player and not player.get("bot") and state["phase"] != "lobby":
                player["bot"] = True
                player["stand_in"] = True
                game.log(state, f"{player['display_name']} disconnected — a bot is taking over seat {seat + 1}.")
                stand_in_taken = True
        if stand_in_taken:
            await manager.broadcast(code, state)
            ensure_bot_loop(code)


async def finish_trick_after_delay(code):
    """Runs config.TRICK_DISPLAY_SECONDS after a trick completes, so players
    get a real look at the 4th card before it's swept off the table."""
    await asyncio.sleep(config.TRICK_DISPLAY_SECONDS)
    items = []
    async with get_room_lock(code):
        state = ROOMS.get(code)
        if not state:
            return
        game.clear_trick(state)
        items = game.drain_pending_stats(state)
    if items:
        await run_in_threadpool(auth.apply_pending_stats, items)
    await manager.broadcast(code, state)
    if any(p.get("bot") for p in state["players"].values()):
        ensure_bot_loop(code)


async def handle_action(code, username, msg):
    action = msg.get("action")
    trick_pending = False
    async with get_room_lock(code):
        state = ROOMS.get(code)
        if not state:
            return
        my_seat = seat_of_username(state, username)
        items = []

        if action == "add_bot":
            seat = msg.get("seat")
            if state["phase"] == "lobby" and seat in range(4) and seat not in state["players"]:
                existing_names = {p["display_name"] for p in state["players"].values() if p.get("bot")}
                bot_name, avatar_seed = bots.random_bot(exclude_names=existing_names)
                state["players"][seat] = {
                    "username": f"__bot_{seat}__", "display_name": bot_name,
                    "profile_pic": None, "bot": True, "avatar_seed": avatar_seed,
                }

        elif action == "start_game":
            if state["phase"] == "lobby" and len(state["players"]) == 4:
                game.start_new_round(state)

        elif action == "bid":
            value = msg.get("value")
            if (state["phase"] == "bidding" and state["turn"] == my_seat
                    and my_seat in state["active_bidders"] and isinstance(value, int)
                    and game.MIN_BID <= value <= game.MAX_BID and value > state["bid_value"]):
                game.do_bid(state, my_seat, value)

        elif action == "pass":
            if state["phase"] == "bidding" and state["turn"] == my_seat and my_seat in state["active_bidders"]:
                game.do_pass(state, my_seat)

        elif action == "choose_trump":
            suit = msg.get("suit")
            if state["phase"] == "choose_trump" and state["bid_seat"] == my_seat and suit in game.SUITS:
                game.choose_trump(state, suit)

        elif action == "play_card":
            card = msg.get("card")
            if state["phase"] == "playing" and state["turn"] == my_seat:
                game.play_card(state, my_seat, card)
                trick_pending = state.get("trick_pending_clear", False)

        elif action == "next_round":
            if state["phase"] == "round_over" and can_control_pacing(state, username):
                state["dealer"] = (state["dealer"] + 1) % 4
                game.start_new_round(state)

        elif action == "play_again":
            if state["phase"] == "game_over" and can_control_pacing(state, username):
                state["match_score"] = {0: 0, 1: 0}
                state["dealer"] = 0
                state["game_start_time"] = None
                state["stats_recorded"] = False
                state["phase"] = "lobby"

        items = game.drain_pending_stats(state)

    if items:
        await run_in_threadpool(auth.apply_pending_stats, items)
    await manager.broadcast(code, state)
    if trick_pending:
        # Turn is None while the completed trick is on display, so the bot
        # loop has nothing to do until finish_trick_after_delay reopens it.
        schedule_task(finish_trick_after_delay(code))
    elif any(p.get("bot") for p in state["players"].values()):
        ensure_bot_loop(code)


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
os.makedirs(GENERATED_SOUNDS_DIR, exist_ok=True)
app.mount("/sounds", StaticFiles(directory=GENERATED_SOUNDS_DIR), name="sounds")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
