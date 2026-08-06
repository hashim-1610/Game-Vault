"""User accounts, sessions, invite-gated registration, profile pictures,
stats and leaderboard, backed by hosted Postgres (e.g. a free Neon project)
so data survives redeploys.

DATABASE_URL is read from the environment — set it in Render's dashboard
(Environment tab) for deployment, or export it locally for dev.

Sessions are a lightweight in-memory token store (not JWT, not DB-backed):
simple, sufficient for a small private game, but tokens don't survive a
server restart — users just log in again. Account data itself is always
safe in Postgres regardless.

Registration is gated by an invite code (see config.MASTER_INVITE_CODE) so
only people you've shared the code with can create an account.
"""

import base64
import hashlib
import io
import os
import secrets
import threading
import time

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

import config

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

_pool = None
_pool_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Session tokens (in-memory)
# ---------------------------------------------------------------------------
_sessions = {}  # token -> {"username": str, "created": float}
_sessions_lock = threading.Lock()
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


def create_session(username):
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {"username": username, "created": time.time()}
    return token


def get_username_for_token(token):
    if not token:
        return None
    with _sessions_lock:
        entry = _sessions.get(token)
        if not entry:
            return None
        if time.time() - entry["created"] > SESSION_TTL_SECONDS:
            del _sessions[token]
            return None
        return entry["username"]


def delete_session(token):
    with _sessions_lock:
        _sessions.pop(token, None)


# ---------------------------------------------------------------------------
# Postgres connection pool
# ---------------------------------------------------------------------------
def _get_database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    return url


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 10, dsn=_get_database_url())
    return _pool


class _Conn:
    def __enter__(self):
        self.conn = _get_pool().getconn()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        _get_pool().putconn(self.conn)


def _conn():
    return _Conn()


def init_db():
    with _conn() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            display_name TEXT NOT NULL,
            profile_pic TEXT,
            created_at DOUBLE PRECISION NOT NULL
        )""")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_pic TEXT")
        cur.execute("""CREATE TABLE IF NOT EXISTS stats (
            username TEXT PRIMARY KEY REFERENCES users(username),
            games_played INTEGER DEFAULT 0,
            games_won INTEGER DEFAULT 0,
            rounds_played INTEGER DEFAULT 0,
            bids_made INTEGER DEFAULT 0,
            bids_won INTEGER DEFAULT 0,
            best_margin INTEGER DEFAULT 0,
            fastest_win_seconds DOUBLE PRECISION,
            total_playtime_seconds DOUBLE PRECISION DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS game_history (
            id SERIAL PRIMARY KEY,
            room_code TEXT,
            ended_at DOUBLE PRECISION,
            duration_seconds DOUBLE PRECISION,
            team_a TEXT,
            team_b TEXT,
            team_a_score INTEGER,
            team_b_score INTEGER,
            winner TEXT,
            margin INTEGER,
            mode TEXT
        )""")


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def _process_profile_pic(image_bytes):
    if not _HAS_PIL or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        side = min(img.size)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((config.PROFILE_PIC_MAX_DIM, config.PROFILE_PIC_MAX_DIM))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def create_user(username, password, confirm_password, display_name, invite_code, profile_pic_bytes=None):
    """Returns (ok: bool, message: str)."""
    username = (username or "").strip()
    if not username or not password:
        return False, "Username and password are required."
    if password != confirm_password:
        return False, "Passwords don't match."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    if invite_code != config.MASTER_INVITE_CODE:
        return False, "Invalid invite code."

    salt = os.urandom(16).hex()
    ph = _hash(password, salt)
    pic_uri = _process_profile_pic(profile_pic_bytes)

    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, salt, display_name, profile_pic, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (username, ph, salt, (display_name or "").strip() or username, pic_uri, time.time()),
            )
            cur.execute("INSERT INTO stats (username) VALUES (%s) ON CONFLICT (username) DO NOTHING", (username,))
        return True, "Account created."
    except psycopg2.IntegrityError:
        return False, "That username is already taken."


def verify_user(username, password):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT password_hash, salt FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    if not row:
        return False
    ph, salt = row
    return _hash(password, salt) == ph


def get_display_name(username):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT display_name FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    return row[0] if row else username


def get_profile_pic(username):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT profile_pic FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_user_public(username):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT display_name, profile_pic FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    if not row:
        return None
    return {"username": username, "display_name": row[0], "profile_pic": row[1]}


def set_display_name(username, display_name):
    with _conn() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET display_name=%s WHERE username=%s",
                    (display_name.strip() or username, username))


def set_profile_pic(username, image_bytes):
    pic_uri = _process_profile_pic(image_bytes)
    if not pic_uri:
        return False
    with _conn() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET profile_pic=%s WHERE username=%s", (pic_uri, username))
    return True


def get_stats(username):
    with _conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO stats (username) VALUES (%s) ON CONFLICT (username) DO NOTHING", (username,))
        cur.execute("""SELECT games_played, games_won, rounds_played, bids_made, bids_won,
                               best_margin, fastest_win_seconds, total_playtime_seconds,
                               current_streak, best_streak
                        FROM stats WHERE username=%s""", (username,))
        row = cur.fetchone()
    keys = ["games_played", "games_won", "rounds_played", "bids_made", "bids_won",
            "best_margin", "fastest_win_seconds", "total_playtime_seconds",
            "current_streak", "best_streak"]
    return dict(zip(keys, row))


def record_round(username, was_bidder, bid_made):
    with _conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO stats (username) VALUES (%s) ON CONFLICT (username) DO NOTHING", (username,))
        cur.execute("UPDATE stats SET rounds_played = rounds_played + 1 WHERE username=%s", (username,))
        if was_bidder:
            cur.execute("UPDATE stats SET bids_made = bids_made + 1 WHERE username=%s", (username,))
            if bid_made:
                cur.execute("UPDATE stats SET bids_won = bids_won + 1 WHERE username=%s", (username,))


def record_game(username, won, margin, duration_seconds):
    with _conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO stats (username) VALUES (%s) ON CONFLICT (username) DO NOTHING", (username,))
        cur.execute("UPDATE stats SET games_played = games_played + 1, "
                    "total_playtime_seconds = total_playtime_seconds + %s WHERE username=%s",
                    (duration_seconds, username))
        if won:
            cur.execute("""UPDATE stats SET games_won = games_won + 1,
                                current_streak = current_streak + 1,
                                best_margin = GREATEST(best_margin, %s),
                                fastest_win_seconds = LEAST(COALESCE(fastest_win_seconds, %s), %s)
                          WHERE username=%s""", (margin, duration_seconds, duration_seconds, username))
            cur.execute("UPDATE stats SET best_streak = GREATEST(best_streak, current_streak) WHERE username=%s",
                        (username,))
        else:
            cur.execute("UPDATE stats SET current_streak = 0 WHERE username=%s", (username,))


def save_game_history(room_code, team_a, team_b, score_a, score_b, winner, margin, mode, duration_seconds):
    with _conn() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO game_history
            (room_code, ended_at, duration_seconds, team_a, team_b, team_a_score, team_b_score, winner, margin, mode)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (room_code, time.time(), duration_seconds, ",".join(team_a), ",".join(team_b),
             score_a, score_b, winner, margin, mode))


def get_leaderboard(limit=20):
    with _conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT s.username, u.display_name, s.games_played, s.games_won,
                   CASE WHEN s.games_played > 0 THEN s.games_won::float / s.games_played ELSE 0 END as win_rate,
                   s.best_streak, u.profile_pic
            FROM stats s JOIN users u ON u.username = s.username
            WHERE s.games_played > 0
            ORDER BY s.games_won DESC, win_rate DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    keys = ["username", "display_name", "games_played", "games_won", "win_rate", "best_streak", "profile_pic"]
    return [dict(zip(keys, r)) for r in rows]


def apply_pending_stats(items):
    """Consume a list of dicts produced by game.drain_pending_stats() and
    write them to storage. Keeps game.py fully decoupled from this module."""
    for item in items:
        if item["type"] == "round":
            record_round(item["username"], item["was_bidder"], item["bid_made"])
        elif item["type"] == "game":
            record_game(item["username"], item["won"], item["margin"], item["duration"])
        elif item["type"] == "history":
            save_game_history(
                item["room_code"], item["team_a"], item["team_b"],
                item["score_a"], item["score_b"], item["winner"],
                item["margin"], item["mode"], item["duration"],
            )
