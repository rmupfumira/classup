"""Render the ClassUp brand mark into the full favicon/icon set.

Pillow doesn't natively render SVG, so this script redraws the logo
in pixel space using the same geometry as logo.svg. Keeping both
files in sync means the raster set matches the SVG favicon pixel-
for-pixel at every size.

Produces:
  favicon-16.png, favicon-32.png                             (web)
  favicon.ico                                                (IE / legacy)
  apple-touch-icon-180.png                                   (iOS home-screen)
  icon-192.png, icon-512.png                                 (Android / PWA)
  icon-192-maskable.png, icon-512-maskable.png               (Android maskable)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICONS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "icons"

NAVY = "#1B3A6B"
GOLD = "#C9A92A"
WHITE = "#FFFFFF"


def _draw_logo(size: int, *, pad_ratio: float = 0.0) -> Image.Image:
    """Return a size x size RGBA image of the brand mark.

    ``pad_ratio`` reserves a transparent margin around the logo — used
    for the Android maskable icon, which expects the safe zone (where
    the mark must stay fully visible after the OS applies a round mask)
    to sit at ~80% of the canvas.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Scale factor from the SVG's 40-unit viewBox to the logical draw
    # space, reduced by the padding when we want a margin.
    draw_size = size * (1 - 2 * pad_ratio)
    offset = size * pad_ratio
    s = draw_size / 40.0

    def pt(x: float, y: float) -> tuple[float, float]:
        return (offset + x * s, offset + y * s)

    # Rounded square background
    draw.rounded_rectangle(
        [pt(0, 0), pt(40, 40)],
        radius=10 * s,
        fill=NAVY,
    )

    # Gold arc — approximates the SVG path "M25 11 a 11 11 0 1 0 0 18"
    # (from (25, 11) counter-clockwise the LONG way to (25, 29) with
    # radius 11). Geometry:
    #   chord midpoint     = (25, 20)
    #   half-chord         = 9
    #   centre-to-mid dist = sqrt(11^2 - 9^2) ≈ 6.32
    # For the C that opens right (what the brand uses) the centre sits
    # LEFT of the chord at (25 - 6.32, 20).
    cx, cy = pt(25 - 6.32, 20)
    r = 11 * s
    arc_w = max(2, int(round(3.5 * s)))
    # PIL uses degrees from 0° (east) going CLOCKWISE in screen space.
    # (25, 11) sits NE of centre at ~305°; (25, 29) sits SE at ~55°.
    # Drawing clockwise from 55° → 305° traces the LEFT side — exactly
    # the C-shape we want.
    draw.arc(
        [(cx - r, cy - r), (cx + r, cy + r)],
        start=55,
        end=305,
        fill=GOLD,
        width=arc_w,
    )

    # Up-arrow: shaft from (25, 25) up to (25, 15), head to (21, 19)
    # and (29, 19).
    shaft_w = max(2, int(round(2.5 * s)))
    draw.line([pt(25, 25), pt(25, 15)], fill=WHITE, width=shaft_w)
    draw.line([pt(25, 15), pt(21, 19)], fill=WHITE, width=shaft_w)
    draw.line([pt(25, 15), pt(29, 19)], fill=WHITE, width=shaft_w)

    return img


def main() -> None:
    specs: list[tuple[str, int, float]] = [
        ("favicon-16.png", 16, 0.0),
        ("favicon-32.png", 32, 0.0),
        ("apple-touch-icon-180.png", 180, 0.0),
        ("icon-192.png", 192, 0.0),
        ("icon-512.png", 512, 0.0),
        # Maskable: 10% transparent padding so the mark stays inside
        # the 80% safe zone after Android applies a rounded mask.
        ("icon-192-maskable.png", 192, 0.1),
        ("icon-512-maskable.png", 512, 0.1),
    ]
    for name, size, pad in specs:
        img = _draw_logo(size, pad_ratio=pad)
        img.save(ICONS_DIR / name, optimize=True)
        print(f"wrote {name}")

    # Multi-size .ico for legacy browsers that insist on /favicon.ico
    ico_base = _draw_logo(64)
    ico_base.save(
        ICONS_DIR / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    print("wrote favicon.ico")


if __name__ == "__main__":
    main()
