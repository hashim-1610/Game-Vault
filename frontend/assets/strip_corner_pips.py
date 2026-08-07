"""Remove the small suit pip the deck draws in each card's two corners.

Game Vault draws its own rank letter there instead (see cardHtml() in
app.js) — the suit is already unmistakable from the card's face, so the
corner pip is redundant and just crowds the letter.

Identification is purely by size: every corner pip is ~18x20 user units,
while the smallest piece of real card art is ~38 wide. Re-runnable — it
reports how many paths it strips per symbol and refuses to write if any
card doesn't yield exactly 2.
"""
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITE = os.path.join(HERE, "cards-v2.svg")
MAX_PIP_SIZE = 25.0   # corner pips are ~18x20; next smallest art is ~38 wide


def path_size(d):
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", d)]
    if len(nums) < 4:
        return None
    xs, ys = nums[0::2], nums[1::2]
    return max(xs) - min(xs), max(ys) - min(ys)


def main():
    text = open(SPRITE, encoding="utf-8").read()
    symbols = re.findall(r'<symbol id="(\d+_\d+)"', text)
    print(f"symbols found: {len(symbols)}")

    out = []
    cursor = 0
    counts = {}
    for m in re.finditer(r'(<symbol id="(\d+_\d+)"[^>]*>)(.*?)(</symbol>)', text, re.S):
        sid, body = m.group(2), m.group(3)
        removed = 0

        def drop(pm):
            nonlocal removed
            size = path_size(pm.group(1))
            if size and size[0] < MAX_PIP_SIZE and size[1] < MAX_PIP_SIZE:
                removed += 1
                return ""
            return pm.group(0)

        new_body = re.sub(r'<path d="([^"]+)"[^>]*></path>', drop, body)
        counts[sid] = removed
        out.append(text[cursor:m.start()])
        out.append(m.group(1) + new_body + m.group(4))
        cursor = m.end()
    out.append(text[cursor:])

    bad = {k: v for k, v in counts.items() if v != 2}
    if bad:
        print("REFUSING TO WRITE — these symbols did not yield exactly 2 pips:")
        for k, v in sorted(bad.items()):
            print(f"   {k}: removed {v}")
        return 1

    new_text = "".join(out)
    open(SPRITE, "w", encoding="utf-8").write(new_text)
    print(f"stripped 2 corner pips from all {len(counts)} symbols")
    print(f"size: {len(text)} -> {len(new_text)} bytes")
    return 0


sys.exit(main())
