# 28 — Irupathiyettu — Deployment Guide (FastAPI + HTML/CSS/JS)

## Architecture

```
backend/
  game.py      pure game engine — zero framework dependencies
  auth.py      Postgres accounts, sessions, invite gate, profile pics, stats
  sounds.py    synthesized .wav sound effects, written to disk at startup
  config.py    MASTER_INVITE_CODE and a couple of other constants
  main.py      FastAPI app: REST endpoints + WebSocket-per-room + bot pacing
  requirements.txt
frontend/
  index.html   all views (auth, lobby, table, profile, leaderboard)
  style.css    full visual theme
  app.js       all client logic — fetch calls, WebSocket handling, rendering
render.yaml    one-click Render deployment blueprint
```

The backend serves the frontend directly (FastAPI's StaticFiles), so this
is **one deployable service**, not two. No Node.js build step anywhere —
the frontend is plain files.

## 1. Set your invite code

Open `backend/config.py` and change `MASTER_INVITE_CODE`. Anyone without
this code can't register. Change it any time and redeploy — existing
accounts aren't affected.

## 2. Set up free Postgres (Neon) — same as before

1. Go to https://neon.tech and sign up (free, no credit card).
2. Create a project, copy the connection string:
   `postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`

If you already have a Neon project from the earlier Streamlit version, you
can reuse the same one — the table schema is compatible (this backend adds
`profile_pic` to `users` automatically on startup if it's missing).

## 3. Push this code to GitHub

Push the whole `kerala28-web` folder (both `backend/` and `frontend/`,
plus `render.yaml`) to a repo.

## 4. Deploy on Render

1. Go to https://render.com, sign up (free, no credit card).
2. New → Blueprint → point it at your repo. Render will read `render.yaml`
   automatically and set up the web service.
   (If you'd rather configure manually instead of using the blueprint:
   New → Web Service → build command `pip install -r backend/requirements.txt`,
   start command `uvicorn main:app --host 0.0.0.0 --port $PORT --app-dir backend`.)
3. In the service's **Environment** tab, add:
   ```
   DATABASE_URL = postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require
   ```
4. Deploy. Render gives you a `https://<something>.onrender.com` URL —
   that's your shareable link.

## Notes on Render's free tier

- The free web service **sleeps after 15 minutes of inactivity** and takes
  ~30-50 seconds to wake up on the next request. This is normal and free —
  just a heads up so the first load after a quiet period isn't mistaken
  for a bug.
- WebSockets work fine on Render's free tier; no special configuration
  needed beyond the above.
- Sessions (login tokens) are stored in memory, so a server restart/sleep
  cycle logs everyone out — they just log back in, and all their account
  data/stats are untouched (that lives in Neon, not the server's memory).

## Local development

```
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql://...your Neon or local Postgres URL..."
uvicorn main:app --reload
```
Then open http://127.0.0.1:8000 — the backend serves the frontend directly,
no separate dev server needed.

## What changed from the Streamlit version

- Real-time WebSocket push instead of polling — bids and cards land
  instantly instead of on a ~2s refresh cycle.
- The UI is genuinely isolated: `frontend/` is plain HTML/CSS/JS, no
  Python touches it. Restyle freely without touching game logic.
- `game.py` is unchanged — same engine, same rules, same tests.
- Bot pacing is a real server-side delay (`config.BOT_DELAY_SECONDS`),
  not a client-polling artifact.
