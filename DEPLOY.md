# 28 — Kerala Card Game — Deployment Guide

## 1. Set up free Postgres (Neon)

1. Go to https://neon.tech and sign up (free, no credit card).
2. Create a new project (any name/region).
3. On the project dashboard, copy the **connection string** — it looks like:
   `postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`
4. Keep that string handy — you'll paste it in step 3 below.

(Supabase's free Postgres works the same way if you prefer it — just grab
its connection string instead.)

## 2. Set your invite code

Open `config.py` and change `MASTER_INVITE_CODE` to something only you and
your friends know. Anyone without this code can't register an account.
You can change it again at any time by editing this file and redeploying —
it doesn't affect existing accounts.

## 3. Push this code to GitHub

Create a new **public or private** repo containing:
```
app.py
game.py
ui.py
auth.py
sounds.py
config.py
requirements.txt
.gitignore
```
Do **not** commit `.streamlit/secrets.toml` (the `.gitignore` already excludes it) —
only commit `.streamlit/secrets.toml.example`.

## 4. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io, sign in with GitHub.
2. Click **New app**, pick your repo, branch `main`, main file `app.py`.
3. Before/after deploying, open **Settings → Secrets** for the app and paste:
   ```
   DATABASE_URL = "postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require"
   ```
   (your real Neon connection string from step 1)
4. Save. The app redeploys automatically and gives you a public
   `https://<something>.streamlit.app` link — that's your shareable link.

## 5. Verify persistence

Register an account, play a game (or forfeit-test with bots), check your
Profile/Leaderboard. Redeploy the app (or just wait — Streamlit Cloud apps
sleep and restart on inactivity) and log back in: your account, stats, and
the leaderboard will still be there, because they live in Neon's Postgres,
not on the app server's local disk.

## Local development

```
pip install -r requirements.txt
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit secrets.toml with a real DATABASE_URL (Neon, or a local Postgres)
streamlit run app.py
```

## Notes

- Neon's free tier (0.5 GB storage, generous compute hours) is comfortably
  enough for a game like this — thousands of players' stats fit in a few MB.
- The connection pool in `auth.py` keeps to 1–5 connections, well under any
  free-tier connection limit.
- If you ever need to reset the leaderboard, just run the SQL
  `TRUNCATE stats, game_history;` from Neon's SQL editor — accounts (the
  `users` table) stay intact.
