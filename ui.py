"""
Visual design system for 28.

Design direction: kasavu (Kerala's gold-bordered ivory cloth) + the deep
green of the backwaters, rather than a generic dark-casino-felt theme.
Cream/ivory surrounds the app chrome (header, lobby, login); the actual
play area sits inside a deep-teal "table" panel framed by a gold double
rule — a nod to the kasavu saree border — which is also reused as the
section-divider motif throughout.

Cards are real HTML/CSS, not text: a white face with a hairline gold edge,
corner ranks, and a large centered suit glyph in ink or maroon. Since
Streamlit has no native card widget, this is built from scratch here.
"""

import streamlit as st

RED_SUITS = {"♥", "♦"}

BASE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap');

:root {
  --cream: #FAF6EC;
  --cream-2: #F1EAD8;
  --teal-900: #08302A;
  --teal-800: #0C4238;
  --teal-700: #10584A;
  --gold: #C9A227;
  --gold-light: #E7CB6B;
  --maroon: #9B2242;
  --ink: #22221E;
  --ink-soft: #5B594E;
}

html, body, .stApp {
  background: var(--cream) !important;
  color: var(--ink);
  font-family: 'Inter', sans-serif;
}

.stApp header { background: transparent !important; }
#MainMenu, footer { visibility: hidden; }

h1, h2, h3, h4 {
  font-family: 'Fraunces', serif !important;
  color: var(--teal-900) !important;
  font-weight: 600 !important;
  letter-spacing: -0.01em;
}

p, li, span, label, .stMarkdown, .stCaption {
  color: var(--ink) !important;
}

/* ---- Kasavu gold double-rule divider, the signature motif ---- */
.kasavu-rule {
  height: 6px;
  margin: 0.35rem 0 1.1rem 0;
  background: linear-gradient(90deg, transparent, var(--gold) 8%, var(--gold) 92%, transparent);
  position: relative;
}
.kasavu-rule::after {
  content: "";
  position: absolute;
  left: 8%; right: 8%; top: 9px;
  height: 2px;
  background: var(--gold);
  opacity: 0.55;
}

/* ---- The felt table panel that wraps active gameplay ---- */
.table-panel {
  background: radial-gradient(ellipse at top, var(--teal-800) 0%, var(--teal-900) 100%);
  border: 3px solid var(--gold);
  outline: 1px solid rgba(201,162,39,0.4);
  outline-offset: 4px;
  border-radius: 18px;
  padding: 22px 22px 18px 22px;
  margin: 8px 0 20px 0;
  box-shadow: 0 8px 24px rgba(8,48,42,0.25);
}
.table-panel h1, .table-panel h2, .table-panel h3, .table-panel h4,
.table-panel p, .table-panel li, .table-panel span, .table-panel label,
.table-panel .stMarkdown, .table-panel .stCaption {
  color: var(--cream) !important;
}
.table-panel h1, .table-panel h2, .table-panel h3 { color: var(--gold-light) !important; }

/* ---- Cream content panels (lobby, login, profile) ---- */
.cream-panel {
  background: var(--cream-2);
  border: 1px solid rgba(201,162,39,0.5);
  border-radius: 14px;
  padding: 18px 20px;
  margin-bottom: 16px;
}

/* ---- Badges / pills ---- */
.badge-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 4px 0; }
.badge {
  display: inline-block;
  font-family: 'Inter', sans-serif;
  font-size: 0.78rem;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(201,162,39,0.16);
  border: 1px solid var(--gold);
  color: var(--teal-900);
}
.table-panel .badge { color: var(--gold-light); background: rgba(201,162,39,0.12); }

/* ---- Seat strip ---- */
.seat-strip { display: flex; gap: 10px; margin: 4px 0 16px 0; flex-wrap: wrap; }
.seat-chip {
  flex: 1 1 120px;
  min-width: 120px;
  border-radius: 12px;
  padding: 8px 12px;
  border: 1.5px solid rgba(201,162,39,0.5);
  background: rgba(255,255,255,0.06);
  text-align: center;
}
.seat-chip.turn { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(201,162,39,0.35); }
.seat-chip.me { background: rgba(201,162,39,0.22); }
.seat-chip .seat-name { font-weight: 700; font-size: 0.92rem; color: var(--cream) !important; }
.seat-chip .seat-meta { font-size: 0.72rem; color: var(--gold-light) !important; }

/* ---- Playing cards ---- */
.card-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; margin: 6px 0 10px 0; }
.pcard {
  width: 66px; height: 94px;
  background: linear-gradient(160deg, #FFFDF8, #F3ECDA);
  border: 1px solid var(--gold);
  border-radius: 9px;
  box-shadow: 0 3px 7px rgba(8,48,42,0.35);
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: 'Fraunces', serif;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.pcard.dim { opacity: 0.38; filter: grayscale(35%); }
.pcard-corner {
  position: absolute;
  display: flex; flex-direction: column; align-items: center; line-height: 1;
  font-weight: 700; font-size: 13px;
}
.pcard-corner-tl { top: 5px; left: 6px; }
.pcard-corner-br { bottom: 5px; right: 6px; transform: rotate(180deg); }
.pcard-suit-mini { font-size: 11px; margin-top: -1px; }
.pcard-suit-center { font-size: 27px; }
.pcard.red { color: var(--maroon) !important; }
.pcard.black { color: var(--ink) !important; }
.pcard-label {
  text-align: center; font-size: 0.68rem; color: var(--gold-light) !important;
  margin-top: 2px; font-weight: 600;
}
.pcard-back {
  width: 66px; height: 94px;
  border-radius: 9px;
  background: repeating-linear-gradient(45deg, #0C4238, #0C4238 7px, #135445 7px, #135445 14px);
  border: 1px solid var(--gold);
  box-shadow: 0 3px 7px rgba(8,48,42,0.35);
  position: relative;
}
.pcard-back::after {
  content: ""; position: absolute; inset: 7px;
  border: 1.5px solid rgba(201,162,39,0.55); border-radius: 5px;
}
.trick-slot { display: flex; flex-direction: column; align-items: center; gap: 4px; }

/* ---- Buttons ---- */
.stButton > button {
  border-radius: 999px !important;
  border: 1.5px solid var(--gold) !important;
  background: linear-gradient(180deg, var(--teal-700), var(--teal-800)) !important;
  color: var(--cream) !important;
  font-weight: 600 !important;
  padding: 0.45rem 1.3rem !important;
  transition: all 0.15s ease !important;
  box-shadow: 0 2px 5px rgba(8,48,42,0.3) !important;
}
.stButton > button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 5px 10px rgba(8,48,42,0.35) !important;
}
.stButton > button:disabled {
  opacity: 0.35 !important;
  border-color: rgba(201,162,39,0.4) !important;
}
button[kind="primary"], .stButton > button[kind="primary"] {
  background: linear-gradient(180deg, var(--gold-light), var(--gold)) !important;
  color: var(--teal-900) !important;
  border-color: var(--teal-900) !important;
}

/* ---- Metrics as glass cards ---- */
[data-testid="stMetric"] {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(201,162,39,0.5);
  border-radius: 12px;
  padding: 10px 14px;
}
[data-testid="stMetricLabel"] { color: var(--gold-light) !important; }
[data-testid="stMetricValue"] { color: var(--cream) !important; font-family: 'Fraunces', serif !important; }

/* ---- Avatars & seat ring (mirrors a table view, "you" always at bottom) ---- */
.avatar {
  width: 46px; height: 46px; border-radius: 50%;
  border: 2px solid var(--gold);
  object-fit: cover;
  box-shadow: 0 2px 6px rgba(0,0,0,0.4);
}
.avatar-fallback {
  width: 46px; height: 46px; border-radius: 50%;
  border: 2px solid var(--gold);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Fraunces', serif; font-weight: 700; font-size: 1.1rem;
  color: var(--teal-900) !important;
  box-shadow: 0 2px 6px rgba(0,0,0,0.4);
}
.seat-ring {
  display: grid;
  grid-template-columns: 1fr 2fr 1fr;
  grid-template-areas: ". top ." "left mid right" ". bottom .";
  gap: 10px;
  margin-bottom: 14px;
}
.seat-pos { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.seat-pos.pos-top { grid-area: top; }
.seat-pos.pos-left { grid-area: left; }
.seat-pos.pos-right { grid-area: right; }
.seat-pos.pos-bottom { grid-area: bottom; }
.seat-pos.pos-mid { grid-area: mid; display: flex; align-items: center; justify-content: center; }
.seat-pos-inner {
  display: flex; align-items: center; gap: 8px;
  background: rgba(255,255,255,0.06);
  border: 1.5px solid rgba(201,162,39,0.5);
  border-radius: 999px;
  padding: 5px 14px 5px 5px;
}
.seat-pos-inner.turn { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(201,162,39,0.35); }
.seat-pos-inner.me { background: rgba(201,162,39,0.22); }
.seat-pos-name { font-weight: 700; font-size: 0.85rem; color: var(--cream) !important; }
.seat-pos-meta { font-size: 0.68rem; color: var(--gold-light) !important; }

/* ---- Bid grid panel (mirrors a floating "Your Bid" card) ---- */
.bid-panel-header {
  text-align: center; font-family: 'Fraunces', serif; font-size: 1.3rem;
  color: var(--gold-light) !important; margin-bottom: 2px;
}
.bid-panel-sub { text-align: center; font-size: 0.85rem; color: var(--cream) !important; margin-bottom: 8px; }
.stake-row { display: flex; justify-content: center; gap: 8px; margin-bottom: 8px; }
.stake-pill { font-size: 0.72rem; font-weight: 700; padding: 3px 10px; border-radius: 999px; }
.stake-pill.win { background: rgba(46,139,87,0.25); border: 1px solid #3f9d67; color: #bdeccf !important; }
.stake-pill.lose { background: rgba(155,34,66,0.22); border: 1px solid var(--maroon); color: #f3c3d0 !important; }

/* ---- Inputs ---- */
.stTextInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] {
  border-radius: 10px !important;
}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
  background: var(--teal-900) !important;
}
section[data-testid="stSidebar"] * { color: var(--cream) !important; }
</style>
"""


def inject():
    st.markdown(BASE_CSS, unsafe_allow_html=True)


def kasavu_rule():
    st.markdown('<div class="kasavu-rule"></div>', unsafe_allow_html=True)


def badges(*items):
    inner = "".join(f'<span class="badge">{i}</span>' for i in items)
    st.markdown(f'<div class="badge-row">{inner}</div>', unsafe_allow_html=True)


def card_html(card, dim=False, with_label=False):
    rank, suit = card[:-1], card[-1]
    color = "red" if suit in RED_SUITS else "black"
    dim_cls = " dim" if dim else ""
    face = (
        f'<div class="pcard {color}{dim_cls}">'
        f'<div class="pcard-corner pcard-corner-tl"><span>{rank}</span>'
        f'<span class="pcard-suit-mini">{suit}</span></div>'
        f'<div class="pcard-suit-center">{suit}</div>'
        f'<div class="pcard-corner pcard-corner-br"><span>{rank}</span>'
        f'<span class="pcard-suit-mini">{suit}</span></div>'
        f'</div>'
    )
    if with_label:
        face += f'<div class="pcard-label">{rank}{suit}</div>'
    return face


def card_back_html():
    return '<div class="pcard-back"></div>'


def render_card_row(cards, dim=False):
    inner = "".join(card_html(c, dim=dim) for c in cards)
    st.markdown(f'<div class="card-row">{inner}</div>', unsafe_allow_html=True)


def render_trick(trick, seat_labels):
    """trick: list of (seat, card) in play order."""
    slots = []
    for seat, card in trick:
        slots.append(
            f'<div class="trick-slot">{card_html(card)}'
            f'<div class="pcard-label">{seat_labels.get(seat, f"Seat {seat+1}")}</div></div>'
        )
    st.markdown(f'<div class="card-row">{"".join(slots)}</div>', unsafe_allow_html=True)


def avatar_html(display_name, pic_uri=None, size=46):
    if pic_uri:
        return f'<img class="avatar" style="width:{size}px;height:{size}px" src="{pic_uri}">'
    initial = (display_name or "?").strip()[:1].upper()
    palette = ["#C9A227", "#0F5C46", "#9B2242", "#E7CB6B", "#10584A"]
    color = palette[sum(ord(c) for c in display_name) % len(palette)] if display_name else palette[0]
    return (f'<div class="avatar-fallback" style="width:{size}px;height:{size}px;'
            f'background:{color}">{initial}</div>')


def seat_ring(players, my_seat, current_turn, dealer, team_label_fn, profile_pics=None):
    """Lay out all 4 seats around a table, with `my_seat` always shown at
    the bottom (matching a typical card-table perspective) and the other
    three rotating clockwise into right / top / left."""
    profile_pics = profile_pics or {}
    positions = {
        "bottom": my_seat,
        "right": (my_seat + 1) % 4,
        "top": (my_seat + 2) % 4,
        "left": (my_seat + 3) % 4,
    }
    cells = []
    for pos, seat in positions.items():
        p = players.get(seat)
        name = p["display_name"] if p else "Empty"
        pic = profile_pics.get(seat)
        classes = "seat-pos-inner"
        if seat == current_turn:
            classes += " turn"
        if seat == my_seat:
            classes += " me"
        bot_tag = " 🤖" if (p and p.get("bot")) else ""
        dealer_tag = " · dealer" if seat == dealer else ""
        meta = f"{team_label_fn(seat)}{dealer_tag}"
        cells.append(
            f'<div class="seat-pos pos-{pos}"><div class="{classes}">'
            f'{avatar_html(name, pic)}'
            f'<div><div class="seat-pos-name">{name}{bot_tag}</div>'
            f'<div class="seat-pos-meta">{meta}</div></div></div></div>'
        )
    st.markdown(f'<div class="seat-ring">{"".join(cells)}</div>', unsafe_allow_html=True)


def stake_pills():
    st.markdown(
        '<div class="stake-row"><span class="stake-pill win">Win +1</span>'
        '<span class="stake-pill lose">Lose −1</span></div>',
        unsafe_allow_html=True,
    )
