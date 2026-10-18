"""Render captured console output as a VS Code-like terminal PNG.

Usage: term_render.py spec.json out.png
spec: {"prompt": "PS ...>", "blocks": [{"cmd": "...", "file": "path" | "text": "..."}], "cols": 150}
"""
import json
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

BG, FG, CMD, ARG, PROMPT = "#1E1E1E", "#CCCCCC", "#F9F1A5", "#CCCCCC", "#CCCCCC"
WARN, ERR = "#E5C07B", "#F48771"
FONT = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
PAD, LH = 12, 19


def main(spec=None, out=None) -> None:
    spec = spec or json.load(open(sys.argv[1], encoding="utf-8"))
    out = out or sys.argv[2]
    cols = spec.get("cols", 150)
    lines: list[list[tuple[str, str]]] = []
    for b in spec["blocks"]:
        if "cmd" in b:
            exe, _, rest = b["cmd"].partition(" ")
            lines.append([(spec["prompt"] + " ", PROMPT), (exe, CMD), (" " + rest if rest else "", ARG)])
        text = b.get("text")
        if "file" in b:
            text = open(b["file"], encoding="utf-8").read()
        if text:
            for raw in text.rstrip("\n").split("\n"):
                color = WARN if " WARNING " in raw else ERR if "Error" in raw[:20] else FG
                for part in textwrap.wrap(raw, cols, drop_whitespace=False) or [""]:
                    lines.append([(part, color)])
    lines.append([(spec["prompt"] + " ", PROMPT), ("█", FG)])
    width = max(sum(FONT.getlength(t) for t, _ in ln) for ln in lines)
    img = Image.new("RGB", (int(width) + 2 * PAD, len(lines) * LH + 2 * PAD), BG)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        x = PAD
        for t, c in ln:
            d.text((x, PAD + i * LH), t, font=FONT, fill=c)
            x += FONT.getlength(t)
    img.save(out)
    print(out, img.size)


if __name__ == "__main__":
    main()
