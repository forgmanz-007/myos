#!/usr/bin/env python3
"""Draws the MyOS10 title-bar theme (KWin's Aurorae engine): flat white title bars with
wide minimize / maximize / close buttons on the right, like Windows 10.

    python3 make-theme.py OUTPUT_FOLDER

The SVGs are generated so that every part has exactly the size Aurorae expects
(see MyOS10rc): the shadow around the window comes from the "Padding" values, the
1 px border from "Border", the 30 px title from "TitleHeight"."""
import sys
from pathlib import Path

PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 12, 12, 8, 18   # shadow around the window
BORDER = 1                                                   # the thin line around it
TITLE = 30                                                   # title bar height (and buttons)
BUTTON_W = 46                                                # minimize / maximize / close width
TOP = PAD_TOP + BORDER + TITLE                               # height of the top parts
LEFT, RIGHT, BOTTOM = PAD_LEFT + BORDER, PAD_RIGHT + BORDER, BORDER + PAD_BOTTOM
W = H = 20                                                   # stretched parts: any size

LOOKS = {
    # name: (border opacity, shadow opacity at the window: top, sides, bottom)
    "": (0.30, 0.10, 0.20, 0.30),
    "-inactive": (0.18, 0.06, 0.12, 0.18),
}


def rc() -> str:
    return f"""[General]
ActiveTextColor=0,0,0,255
InactiveTextColor=153,153,153,255
TitleAlignment=Left
TitleVerticalAlignment=Center
Animation=100
UseTextShadow=false
HaloActive=false
HaloInactive=false

[Layout]
BorderLeft={BORDER}
BorderRight={BORDER}
BorderBottom={BORDER}
TitleEdgeTop={BORDER}
TitleEdgeBottom=0
TitleEdgeLeft={BORDER}
TitleEdgeRight={BORDER}
TitleEdgeTopMaximized=0
TitleEdgeBottomMaximized=0
TitleEdgeLeftMaximized=0
TitleEdgeRightMaximized=0
TitleBorderLeft=2
TitleBorderRight=4
TitleHeight={TITLE}
ButtonWidth={BUTTON_W}
ButtonWidthMinimize={BUTTON_W}
ButtonWidthMaximizeRestore={BUTTON_W}
ButtonWidthClose={BUTTON_W}
ButtonWidthMenu=32
ButtonWidthAppMenu=32
ButtonWidthAlldesktops=32
ButtonWidthKeepabove=32
ButtonWidthKeepbelow=32
ButtonWidthShade=32
ButtonWidthHelp=32
ButtonHeight={TITLE}
ButtonSpacing=0
ButtonMarginTop=0
ExplicitButtonSpacer=10
PaddingLeft={PAD_LEFT}
PaddingRight={PAD_RIGHT}
PaddingTop={PAD_TOP}
PaddingBottom={PAD_BOTTOM}
"""


class Svg:
    def __init__(self):
        self.defs, self.body, self.n = [], [], 0

    def gradient(self, kind: str, alpha: float, **geo) -> str:
        """A shadow gradient: full `alpha` at the window, fading to nothing outside."""
        self.n += 1
        gid = f"g{self.n}"
        stops = (f'<stop offset="0" stop-color="#000" stop-opacity="{alpha:.3f}"/>'
                 f'<stop offset="0.45" stop-color="#000" stop-opacity="{alpha * 0.45:.3f}"/>'
                 f'<stop offset="1" stop-color="#000" stop-opacity="0"/>')
        attrs = " ".join(f'{k}="{v}"' for k, v in geo.items())
        tag = "linearGradient" if kind == "linear" else "radialGradient"
        self.defs.append(f'<{tag} id="{gid}" gradientUnits="objectBoundingBox" {attrs}>{stops}</{tag}>')
        return f"url(#{gid})"

    def out(self, width: int, height: int) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}">\n<defs>{"".join(self.defs)}</defs>\n'
                + "\n".join(self.body) + "\n</svg>\n")


def rect(x, y, w, h, fill, opacity=1.0) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" fill-opacity="{opacity:.3f}"/>'


def decoration() -> str:
    s = Svg()
    xs = (0, LEFT + 10, LEFT + 10 + W + 10)                  # columns: left, middle, right
    for row, (suffix, (border, a_top, a_side, a_bottom)) in enumerate(LOOKS.items()):
        oy = row * (TOP + H + BOTTOM + 40)
        ys = (oy, oy + TOP + 10, oy + TOP + 10 + H + 10)       # rows: top, middle, bottom
        p = f"decoration{suffix}"
        corner_top = (a_top + a_side) / 2
        corner_bottom = (a_side + a_bottom) / 2
        x0, x1, x2 = xs
        y0, y1, y2 = ys
        parts = {
            "topleft": (x0, y0, LEFT, TOP, [
                rect(x0, y0, PAD_LEFT, PAD_TOP, s.gradient("radial", corner_top, cx=1, cy=1, r=1, fx=1, fy=1)),
                rect(x0, y0 + PAD_TOP, PAD_LEFT, TOP - PAD_TOP, s.gradient("linear", a_side, x1=1, y1=0, x2=0, y2=0)),
                rect(x0 + PAD_LEFT, y0 + PAD_TOP, BORDER, TOP - PAD_TOP, "#000", border)]),
            "top": (x1, y0, W, TOP, [
                rect(x1, y0, W, PAD_TOP, s.gradient("linear", a_top, x1=0, y1=1, x2=0, y2=0)),
                rect(x1, y0 + PAD_TOP, W, BORDER, "#000", border),
                rect(x1, y0 + PAD_TOP + BORDER, W, TITLE, "#ffffff")]),
            "topright": (x2, y0, RIGHT, TOP, [
                rect(x2 + BORDER, y0, PAD_RIGHT, PAD_TOP, s.gradient("radial", corner_top, cx=0, cy=1, r=1, fx=0, fy=1)),
                rect(x2 + BORDER, y0 + PAD_TOP, PAD_RIGHT, TOP - PAD_TOP, s.gradient("linear", a_side, x1=0, y1=0, x2=1, y2=0)),
                rect(x2, y0 + PAD_TOP, BORDER, TOP - PAD_TOP, "#000", border)]),
            "left": (x0, y1, LEFT, H, [
                rect(x0, y1, PAD_LEFT, H, s.gradient("linear", a_side, x1=1, y1=0, x2=0, y2=0)),
                rect(x0 + PAD_LEFT, y1, BORDER, H, "#000", border)]),
            "center": (x1, y1, W, H, [rect(x1, y1, W, H, "#ffffff")]),
            "right": (x2, y1, RIGHT, H, [
                rect(x2, y1, BORDER, H, "#000", border),
                rect(x2 + BORDER, y1, PAD_RIGHT, H, s.gradient("linear", a_side, x1=0, y1=0, x2=1, y2=0))]),
            "bottomleft": (x0, y2, LEFT, BOTTOM, [
                rect(x0, y2, PAD_LEFT, BORDER, s.gradient("linear", a_side, x1=1, y1=0, x2=0, y2=0)),
                rect(x0 + PAD_LEFT, y2, BORDER, BORDER, "#000", border),
                rect(x0, y2 + BORDER, PAD_LEFT + BORDER, PAD_BOTTOM,
                     s.gradient("radial", corner_bottom, cx=PAD_LEFT / (PAD_LEFT + BORDER), cy=0,
                                r=1, fx=PAD_LEFT / (PAD_LEFT + BORDER), fy=0))]),
            "bottom": (x1, y2, W, BOTTOM, [
                rect(x1, y2, W, BORDER, "#000", border),
                rect(x1, y2 + BORDER, W, PAD_BOTTOM, s.gradient("linear", a_bottom, x1=0, y1=0, x2=0, y2=1))]),
            "bottomright": (x2, y2, RIGHT, BOTTOM, [
                rect(x2, y2, BORDER, BORDER, "#000", border),
                rect(x2 + BORDER, y2, PAD_RIGHT, BORDER, s.gradient("linear", a_side, x1=0, y1=0, x2=1, y2=0)),
                rect(x2, y2 + BORDER, BORDER + PAD_RIGHT, PAD_BOTTOM,
                     s.gradient("radial", corner_bottom, cx=BORDER / (BORDER + PAD_RIGHT), cy=0,
                                r=1, fx=BORDER / (BORDER + PAD_RIGHT), fy=0))]),
        }
        for name, (x, y, w, h, content) in parts.items():
            # the invisible rect fixes the part's size exactly
            s.body.append(f'<g id="{p}-{name}">{rect(x, y, w, h, "#000", 0)}{"".join(content)}</g>')
    # Maximized: no border, no shadow, only the white title bar.
    my = 2 * (TOP + H + BOTTOM + 40)
    for i, suffix in enumerate(("", "-inactive")):
        s.body.append(f'<g id="decoration-maximized{suffix}-center">{rect(i * (W + 10), my, W, TITLE, "#ffffff")}</g>')
    return s.out(xs[2] + RIGHT + 10, my + TITLE + 10)


GLYPHS = {
    "minimize": lambda c: f'<rect x="18" y="15" width="10" height="1" fill="{c}"/>',
    "maximize": lambda c: f'<rect x="18.5" y="10.5" width="9" height="9" fill="none" stroke="{c}" stroke-width="1"/>',
    "restore": lambda c: (f'<rect x="18.5" y="12.5" width="7" height="7" fill="none" stroke="{c}" stroke-width="1"/>'
                          f'<path d="M20.5 12.5V10.5H27.5V17.5H25.5" fill="none" stroke="{c}" stroke-width="1"/>'),
    "close": lambda c: (f'<path d="M18 10L28 20M28 10L18 20" fill="none" stroke="{c}" stroke-width="1"/>'),
}

# state: (background, background opacity, glyph colour); the close button turns red.
STATES = {
    "active": ("#000", 0.0, "#000000"),
    "hover": ("#000", 0.10, "#000000"),
    "pressed": ("#000", 0.20, "#000000"),
    "deactivated": ("#000", 0.0, "#c4c4c4"),
    "inactive": ("#000", 0.0, "#999999"),
    "hover-inactive": ("#000", 0.10, "#000000"),
    "pressed-inactive": ("#000", 0.20, "#000000"),
    "deactivated-inactive": ("#000", 0.0, "#dddddd"),
}
CLOSE = {
    "hover": ("#e81123", 1.0, "#ffffff"),
    "pressed": ("#f1707a", 1.0, "#ffffff"),
    "hover-inactive": ("#e81123", 1.0, "#ffffff"),
    "pressed-inactive": ("#f1707a", 1.0, "#ffffff"),
}


def button(kind: str) -> str:
    s = Svg()
    for i, (state, look) in enumerate(STATES.items()):
        bg, opacity, glyph = CLOSE.get(state, look) if kind == "close" else look
        y = i * (TITLE + 10)
        s.body.append(f'<g id="{state}-center" transform="translate(0,{y})">'
                      f'{rect(0, 0, BUTTON_W, TITLE, bg, opacity)}{GLYPHS[kind](glyph)}</g>')
    return s.out(BUTTON_W, len(STATES) * (TITLE + 10))


METADATA = """[Desktop Entry]
Name=MyOS 10
Comment=White title bars with wide minimize, maximize and close buttons, like Windows 10
X-KDE-PluginInfo-Author=MyOS
X-KDE-PluginInfo-Name=MyOS10
X-KDE-PluginInfo-Version=1.0
X-KDE-PluginInfo-License=GPL-2.0+
X-KDE-PluginInfo-EnabledByDefault=true
"""


def write(folder: Path) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    files = {"metadata.desktop": METADATA, "MyOS10rc": rc(), "decoration.svg": decoration()}
    for kind in GLYPHS:
        files[f"{kind}.svg"] = button(kind)
    for name, text in files.items():
        (folder / name).write_text(text)
    return sorted(files)


if __name__ == "__main__":
    print(" ".join(write(Path(sys.argv[1]))))
