#!/usr/bin/env python3
"""Draws the pictures of the "MyOS 10" Plasma style: the taskbar, its buttons, pop-ups and
tooltips, flat and square like Windows 10's dark taskbar.

    python3 make-style.py STYLE_FOLDER      (writes the .svg files into it)

Plasma draws each of these as a frame cut into nine parts (corners, edges, middle). Every
part below is an SVG group whose invisible first rectangle fixes the part's exact size.
"hint-*-margin" parts set the space between the frame and what is inside it."""
import sys
from pathlib import Path

TASKBAR = "#101010"        # taskbar (Windows 10 dark)
POPUP = "#1f1f1f"          # Start menu, calendar and other pop-ups
TOOLTIP = "#2b2b2b"
LINE = "#76b9ed"           # the line under open programs (light accent blue)
ZERO = "0.00000001"        # a size Plasma reads as 0 (a real 0 would hide the part)


def rect(x, y, w, h, fill="#000", opacity=1.0) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" '
            f'fill-opacity="{opacity:g}"/>')


class Frame:
    """One frame (one prefix) at a spot of the drawing."""

    def __init__(self, prefix: str, x: int, y: int, left: int, top: int, right: int, bottom: int,
                 middle: int = 10):
        self.p = f"{prefix}-" if prefix else ""
        self.x, self.y = x, y
        self.l, self.t, self.r, self.b, self.m = left, top, right, bottom, middle
        self.parts = []

    def size(self):
        return self.l + self.m + self.r + 20, self.t + self.m + self.b + 20

    def cells(self):
        """name -> (x, y, w, h); the parts are 10 px apart so they never touch."""
        xs = (self.x, self.x + self.l + 10, self.x + self.l + 10 + self.m + 10)
        ys = (self.y, self.y + self.t + 10, self.y + self.t + 10 + self.m + 10)
        ws, hs = (self.l, self.m, self.r), (self.t, self.m, self.b)
        names = (("topleft", "top", "topright"), ("left", "center", "right"),
                 ("bottomleft", "bottom", "bottomright"))
        return {names[j][i]: (xs[i], ys[j], ws[i], hs[j]) for j in range(3) for i in range(3)}

    def draw(self, paint) -> str:
        """paint(name, x, y, w, h) -> SVG of what is visible in that part."""
        out = []
        for name, (x, y, w, h) in self.cells().items():
            if w == 0 or h == 0:
                continue
            out.append(f'<g id="{self.p}{name}">{rect(x, y, w, h, "#000", 0)}{paint(name, x, y, w, h)}</g>')
        return "".join(out)

    def hints(self, left, top, right, bottom) -> str:
        """The space between the frame's outer edge and its content."""
        x, y = self.x, self.y + self.t + self.m + self.b + 30
        out = []
        for side, value in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
            v = value if value else ZERO
            w, h = (v, 4) if side in ("left", "right") else (4, v)
            out.append(f'<rect id="{self.p}hint-{side}-margin" x="{x}" y="{y}" width="{w}" height="{h}" fill="#f0f"/>')
            x += 10
        return "".join(out)


def svg(width, height, body) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">\n{body}\n</svg>\n')


def flat(color: str, opacity: float, border: str = "", border_opacity: float = 0.0, edge: int = 1,
         margins=(0, 0, 0, 0)) -> str:
    """A plain square frame: one colour, optionally with a 1 px border line."""
    f = Frame("", 0, 0, edge, edge, edge, edge)

    def paint(name, x, y, w, h):
        out = rect(x, y, w, h, color, opacity)
        if border and name != "center":
            out += rect(x, y, w, h, border, border_opacity)
        return out
    w, h = f.size()
    return svg(w + 60, h + 20, f.draw(paint) + f.hints(*margins))


# ── taskbar buttons ──────────────────────────────────────────────────────────
# (background colour, opacity, line colour or None, line full width?)
TASK_LOOKS = {
    "normal": (None, 0, LINE, False),            # open program: short line under it
    "minimized": (None, 0, LINE, False),
    "hover": ("#ffffff", 0.10, LINE, True),     # pointer over an open program
    "launcher-hover": ("#ffffff", 0.10, None, False),   # pointer over a pinned, closed one
    "focus": ("#ffffff", 0.16, LINE, True),     # the program in front
    "focus-hover": ("#ffffff", 0.22, LINE, True),
    "attention": ("#ff8c00", 0.55, "#ff8c00", True),    # a program asking for attention
    "progress": ("#06b025", 0.45, "#06b025", True),     # download or copy progress
}
INSET, LINE_H = 5, 2        # the short line leaves 5 px free left and right


def tasks() -> str:
    body, y = [], 0
    for prefix, (bg, opacity, line, full) in TASK_LOOKS.items():
        f = Frame(prefix, 0, y, INSET, 1, INSET, LINE_H)

        def paint(name, x, yy, w, h, bg=bg, opacity=opacity, line=line, full=full):
            out = rect(x, yy, w, h, bg, opacity) if bg else ""
            if line and name.startswith("bottom") and (full or name == "bottom"):
                out += rect(x, yy, w, h, line, 1)
            return out
        body.append(f.draw(paint))
        # Icons: 24 px in a 40 px taskbar, buttons 48 px wide (like Windows 10).
        body.append(f.hints(4, 8, 4, 8))
        y += f.size()[1] + 50
    return svg(120, y, "".join(body))


def write(folder: Path) -> list:
    files = {
        # without blur (no desktop effects)
        "widgets/panel-background.svg": flat(TASKBAR, 1),
        # with blur behind, and the fully opaque one Plasma uses next to maximized windows
        "translucent/widgets/panel-background.svg": flat(TASKBAR, 0.85),
        "solid/widgets/panel-background.svg": flat(TASKBAR, 1),
        "dialogs/background.svg": flat(POPUP, 1, "#ffffff", 0.08, margins=(4, 4, 4, 4)),
        "translucent/dialogs/background.svg": flat(POPUP, 0.90, "#ffffff", 0.08, margins=(4, 4, 4, 4)),
        "widgets/tooltip.svg": flat(TOOLTIP, 1, "#ffffff", 0.18, margins=(6, 6, 6, 6)),
        "translucent/widgets/tooltip.svg": flat(TOOLTIP, 0.95, "#ffffff", 0.18, margins=(6, 6, 6, 6)),
        "widgets/tasks.svg": tasks(),
    }
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sorted(files)


if __name__ == "__main__":
    print("\n".join(write(Path(sys.argv[1]))))
