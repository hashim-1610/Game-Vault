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
BOT_DELAY_SECONDS = 1.1         # pause between bot actions, so they never fast-forward
TRICK_DISPLAY_SECONDS = 1.5     # how long a completed 4-card trick stays on screen before it's swept away
