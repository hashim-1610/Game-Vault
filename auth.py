"""User accounts, profiles, stats and leaderboard, backed by hosted Postgres
(e.g. a free Neon or Supabase project) so data survives app redeploys —
unlike a local SQLite file, which is wiped whenever the host restarts.

Connection string is read from Streamlit secrets (st.secrets["DATABASE_URL"])
first, falling back to the DATABASE_URL environment variable for local dev.
Never hardcode credentials in this file.
"""

import hashlib
import os
import threading

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

try:
    import streamlit as st
    _HAS_ST = True
except ImportError:
    _HAS_ST = False

_pool = None
_pool_lock = threading.Lock()


def _get_database_url():
    if _HAS_ST:
        try:
            if "DATABASE_URL" in st.secrets:
                return st.secrets["DATABASE_URL"]
        except Exception:
            pass
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No DATABASE_URL found. Set it in .streamlit/secrets.toml (for "
            "Streamlit Cloud) or as an environment variable (for local dev)."
        )
    return url


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 5, dsn=_get_database_url())
    return _pool


class _Conn:
    """Context manager: borrow a pooled connection, commit/rollback, return it."""
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
            created_at DOUBLE PRECISION NOT NULL
        )""")
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


def create_user(username, password, display_name):
    import time
    username = username.strip()
    if not username or not password:
        return False, "Username and password required."
    salt = os.urandom(16).hex()
    ph = _hash(password, salt)
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, salt, display_name, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (username, ph, salt, display_name.strip() or username, time.time()),
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


def set_display_name(username, display_name):
    with _conn() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET display_name=%s WHERE username=%s",
                    (display_name.strip() or username, username))


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
    import time
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
                   s.best_streak
            FROM stats s JOIN users u ON u.username = s.username
            WHERE s.games_played > 0
            ORDER BY s.games_won DESC, win_rate DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return rows


init_db()
