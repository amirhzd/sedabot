"""Tray and window icon for Sedabot. Drawn locally so the app ships no binary assets."""

from __future__ import annotations

from pathlib import Path

ICON_SIZES = (16, 32, 48, 64, 256)
_BLUE = (31, 111, 235, 255)
_INK = (255, 255, 255, 255)
# Isolated ص, the first letter of صدا. Traced from a Kufi sad so the eye,
# the tooth, and the left tail stay one letter at 16 px.
_SAD_ASPECT = 313 / 476
_SAD_OUTLINE = (
    (75.58, 0.0), (82.74, 0.32), (88.42, 2.56), (93.05, 6.73), (96.63, 13.14),
    (98.53, 19.55), (99.79, 30.13), (99.79, 67.63), (54.11, 67.63), (52.63, 78.53),
    (48.63, 89.1), (41.68, 96.47), (33.47, 99.68), (21.47, 100.0), (15.79, 98.4),
    (9.68, 93.91), (5.05, 87.18), (1.68, 77.88), (0.21, 67.95), (0.63, 51.28),
    (1.89, 42.95), (4.63, 32.69), (16.0, 32.69), (13.47, 51.6), (13.05, 64.74),
    (14.32, 73.4), (16.63, 78.85), (19.58, 82.37), (22.74, 83.97), (32.42, 83.33),
    (35.79, 81.09), (38.11, 77.88), (40.21, 71.79), (40.84, 67.31), (41.05, 1.28),
    (53.89, 1.28), (54.11, 24.68), (58.32, 14.42), (62.74, 7.69), (68.42, 2.56),
)
_SAD_COUNTER = (
    (72.42, 16.35), (77.47, 16.35), (80.63, 17.63), (83.16, 19.87), (85.05, 23.4),
    (86.53, 30.45), (86.74, 51.28), (54.32, 51.6), (54.11, 41.35), (58.32, 30.77),
    (64.0, 21.79), (68.42, 17.95),
)


def build_image(size: int = 64):
    """Blue rounded square with a bold isolated ص."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, round(size * 0.06))
    radius = max(2, round(size * 0.22))
    draw.rounded_rectangle(
        (margin, margin, size - 1 - margin, size - 1 - margin),
        radius=radius,
        fill=_BLUE,
    )
    _draw_sad(draw, size)
    return image


def _draw_sad(draw, size: int) -> None:
    width = 74.0
    height = width * _SAD_ASPECT
    left = (100.0 - width) / 2.0
    top = (100.0 - height) / 2.0

    def place(points):
        return [
            (size * (left + width * x / 100.0) / 100.0, size * (top + height * y / 100.0) / 100.0)
            for x, y in points
        ]

    draw.polygon(place(_SAD_OUTLINE), fill=_INK)
    draw.polygon(place(_SAD_COUNTER), fill=_BLUE)


def ensure_icon(path: Path) -> Path | None:
    """Write a multi-size .ico if Pillow is installed. Return the path when it exists."""
    path = Path(path)
    try:
        from PIL import Image
    except ImportError:
        return path if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [build_image(size) for size in ICON_SIZES]
    images[-1].save(
        path,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=images[:-1],
    )
    return path
