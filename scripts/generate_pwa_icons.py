"""Generate the full PWA icon set + Apple splash screens for ClassUp.

Branding: navy ``#1B3A6B`` background with gold ``#C9962A`` letterforms ``CU``.
Idempotent — safe to re-run; overwrites existing files.

Outputs to ``app/static/img/icons/``:

- favicon-16.png, favicon-32.png, favicon.ico
- icon-192.png, icon-512.png (regular)
- icon-192-maskable.png, icon-512-maskable.png (with safe area padding)
- apple-touch-icon-180.png (iOS home screen)
- apple-splash-2048-2732.png ... (full set of iOS splash screens)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Brand
PRIMARY = (27, 58, 107)      # #1B3A6B navy
PRIMARY_DARK = (12, 25, 44)  # #0C192C
ACCENT = (201, 150, 42)      # #C9962A gold
ACCENT_LIGHT = (232, 184, 75)  # #E8B84B
WHITE = (255, 255, 255)

# Sizes
ICONS = [
    ("favicon-16.png", 16),
    ("favicon-32.png", 32),
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("apple-touch-icon-180.png", 180),
]
MASKABLE = [
    ("icon-192-maskable.png", 192),
    ("icon-512-maskable.png", 512),
]
# (width, height) — covers iPhone SE up to iPad Pro 12.9
SPLASH = [
    (640, 1136),    # iPhone SE
    (750, 1334),    # iPhone 8
    (828, 1792),    # iPhone XR / 11
    (1125, 2436),   # iPhone X / 11 Pro
    (1170, 2532),   # iPhone 12/13/14
    (1179, 2556),   # iPhone 14 Pro
    (1242, 2208),   # iPhone 8 Plus
    (1242, 2688),   # iPhone XS Max / 11 Pro Max
    (1284, 2778),   # iPhone 12/13 Pro Max
    (1290, 2796),   # iPhone 14 Pro Max
    (1488, 2266),   # iPad Mini
    (1536, 2048),   # iPad
    (1620, 2160),   # iPad Air
    (1640, 2360),   # iPad Air 5
    (1668, 2224),   # iPad Pro 10.5
    (1668, 2388),   # iPad Pro 11
    (2048, 2732),   # iPad Pro 12.9
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Try common system fonts; fall back to default."""
    for name in (
        "C:/Windows/Fonts/seguisb.ttf",  # Segoe UI Semibold
        "C:/Windows/Fonts/segoeuib.ttf",  # Segoe UI Bold
        "C:/Windows/Fonts/arialbd.ttf",   # Arial Bold
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _radial_bg(size: int, *, padding: int = 0) -> Image.Image:
    """Navy square with subtle radial highlight in the top-left and rounded
    corners. ``padding`` applied for maskable safe area."""
    img = Image.new("RGBA", (size, size), PRIMARY)
    draw = ImageDraw.Draw(img)

    # Subtle highlight overlay
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    glow_radius = size // 2
    for r in range(glow_radius, 0, -2):
        alpha = int(40 * (r / glow_radius))
        od.ellipse(
            (size // 4 - r, size // 4 - r, size // 4 + r, size // 4 + r),
            fill=(255, 255, 255, alpha),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=size / 16))
    img = Image.alpha_composite(img, overlay)

    # Rounded corners (skip for tiny favicons; mask gets jaggy < 32 px)
    if size >= 32:
        radius = max(size // 6, 4)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (padding, padding, size - padding, size - padding),
            radius=radius - padding if padding else radius,
            fill=255,
        )
        rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded.paste(img, (0, 0), mask)
        img = rounded

    return img


def _draw_logo(img: Image.Image, *, padding_pct: float = 0.0) -> Image.Image:
    """Draw the gold ``CU`` monogram centered on the navy background."""
    size = img.width
    draw = ImageDraw.Draw(img)

    # Inner bounds (used by maskable for the safe area)
    inset = int(size * padding_pct)
    inner = size - 2 * inset

    # Pick a font size that fills ~60% of the inner box
    text = "CU"
    target = int(inner * 0.62)
    font = _font(target)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]

    # Soft drop shadow
    if size >= 64:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.text((x + size * 0.01, y + size * 0.015), text, font=font,
                fill=(0, 0, 0, 100))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size / 100))
        img = Image.alpha_composite(img, shadow)
        draw = ImageDraw.Draw(img)

    draw.text((x, y), text, font=font, fill=ACCENT)
    return img


def _draw_minimal_logo(size: int) -> Image.Image:
    """For tiny favicons (16/32) — skip the shadow + glow, just contrast."""
    img = Image.new("RGBA", (size, size), PRIMARY)
    draw = ImageDraw.Draw(img)
    text = "C"
    font = _font(int(size * 0.78))
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (size - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (size - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=ACCENT_LIGHT)
    return img


def make_icon(name: str, size: int, *, maskable: bool = False) -> Image.Image:
    """Build one icon."""
    if size <= 32:
        return _draw_minimal_logo(size)
    # Maskable safe-area: keep the crucial content within the central 80%
    padding_pct = 0.10 if maskable else 0.0
    bg = _radial_bg(size)
    return _draw_logo(bg, padding_pct=padding_pct)


def make_splash(width: int, height: int) -> Image.Image:
    """Apple splash: dark navy with centered logo, scaled to ~30% of width."""
    img = Image.new("RGBA", (width, height), PRIMARY_DARK)
    logo_size = int(min(width, height) * 0.32)
    logo = _radial_bg(logo_size)
    logo = _draw_logo(logo)
    img.alpha_composite(logo, ((width - logo_size) // 2, (height - logo_size) // 2))

    # App name underneath
    draw = ImageDraw.Draw(img)
    name_font = _font(int(min(width, height) * 0.045))
    name = "ClassUp"
    bbox = draw.textbbox((0, 0), name, font=name_font)
    x = (width - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (height + logo_size) / 2 + int(min(width, height) * 0.04)
    draw.text((x, y), name, font=name_font, fill=ACCENT_LIGHT)
    return img


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "icons"
    out.mkdir(parents=True, exist_ok=True)

    for name, size in ICONS:
        path = out / name
        make_icon(name, size).save(path, format="PNG", optimize=True)
        print(f"  {path.relative_to(out.parent.parent.parent.parent)}  ({size}x{size})")

    for name, size in MASKABLE:
        path = out / name
        make_icon(name, size, maskable=True).save(path, format="PNG", optimize=True)
        print(f"  {path.relative_to(out.parent.parent.parent.parent)}  ({size}x{size}, maskable)")

    # ICO bundles 16, 32, 48
    ico_sizes = [16, 32, 48]
    ico_imgs = [make_icon(f"favicon-{s}.png", s) for s in ico_sizes]
    ico_path = out / "favicon.ico"
    ico_imgs[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_imgs[1:],
    )
    print(f"  {ico_path.relative_to(out.parent.parent.parent.parent)}  (ICO bundle 16/32/48)")

    print("\nApple splash screens:")
    for w, h in SPLASH:
        path = out / f"apple-splash-{w}-{h}.png"
        make_splash(w, h).save(path, format="PNG", optimize=True)
        print(f"  {path.relative_to(out.parent.parent.parent.parent)}  ({w}x{h})")

    print("\nDone. All icons + splash screens written.")


if __name__ == "__main__":
    main()
