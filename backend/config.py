"""
App-wide configuration. Nothing here talks to Streamlit, Postgres, or the
game engine — it's just constants.

MASTER_INVITE_CODE gates registration so strangers can't self-sign-up.
Change this string any time; existing accounts are unaffected, it's only
checked at registration.
"""

MASTER_INVITE_CODE = "KERALA28-INVITE"  # <-- change this whenever you like

PROFILE_PIC_MAX_DIM = 128       # px, square thumbnail
PROFILE_PIC_MAX_UPLOAD_MB = 5   # reject uploads bigger than this
BOT_DELAY_MIN_SECONDS = 2.0     # bots pause a random interval in this range between actions —
BOT_DELAY_MAX_SECONDS = 3.0     # varied pacing reads less like a rushed, robotic cadence
TRICK_DISPLAY_SECONDS = 2.2     # how long a completed 4-card trick stays on screen before it's swept away
