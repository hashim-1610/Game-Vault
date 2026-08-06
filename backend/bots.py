"""
Bot personas: playful, fictional nicknames flavored by everyday Kerala
culture (chai, autorickshaws, monsoons, sadya, toddy...) — not references
to any real person. Paired with a deterministic "avatar seed" so the
frontend can render a distinct colorful avatar per bot instead of a plain
initial, without needing actual photos.
"""

import random

BOT_NAMES = [
    "Chaya Kudi Rajan",
    "Autorickshaw Anil",
    "Mundu Manoj",
    "Sadya Suresh",
    "Umbrella Ummer",
    "Filter Coffee Philip",
    "Onam Omana",
    "Kuttanadan Kuttan",
    "Nadan Pattu Nazeer",
    "Toddy Thomachan",
    "Payyans Pavithran",
    "Fish Curry Francis",
    "Elaneer Eldho",
    "Bus Stand Baiju",
    "Power Cut Prabhu",
    "Monsoon Mathew",
]

AVATAR_STYLE_COUNT = 10  # frontend has 10 gradient+emoji styles to cycle through


def random_bot(exclude_names=()):
    """Returns (display_name, avatar_seed). Avoids repeating a name already
    taken in the same room when possible."""
    pool = [n for n in BOT_NAMES if n not in exclude_names] or BOT_NAMES
    name = random.choice(pool)
    seed = random.randint(0, AVATAR_STYLE_COUNT - 1)
    return name, seed
