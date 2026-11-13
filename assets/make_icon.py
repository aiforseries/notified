"""Generates assets/notified.ico and assets/notified.png (run once; outputs are committed)."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent / "notified" / "assets"
S = 1024  # draw large, downsample for smooth edges


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make() -> Image.Image:
    top, bottom = (20, 184, 166), (15, 76, 92)  # teal -> deep teal-navy
    grad = Image.new("RGB", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx, cy = S * 0.36, S * 0.66  # radar origin, lower-left
    white = (255, 255, 255, 255)
    soft = (255, 255, 255, 150)
    for r, w, col in ((560, 58, soft), (390, 64, white), (220, 70, white)):
        d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=0, fill=col, width=w)
    dot = 70
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=white)
    # "new update" ping, top-right
    px, py, pr = S * 0.79, S * 0.21, 66
    d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(251, 191, 36, 255))
    return img


if __name__ == "__main__":
    big = make()
    big.resize((256, 256), Image.LANCZOS).save(HERE / "notified.png")
    big.resize((256, 256), Image.LANCZOS).save(
        HERE / "notified.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok")
