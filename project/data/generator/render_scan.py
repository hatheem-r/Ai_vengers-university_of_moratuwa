"""Scanned / photographed look with Pillow only (no poppler or OpenCV needed).

The document content is drawn on an A4-sized canvas at ~110 dpi, then degraded:
slight rotation, grey paper, blur, speckle noise, a rubber stamp and a handwritten note."""
from __future__ import annotations

import random
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 910, 1287  # A4 at ~110 dpi


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _table(d, x, y, head, rows, width, fs=15):
    n = len(head)
    cw = width / n
    f = font(fs)
    fb = font(fs)
    h = fs + 12
    for j, t in enumerate(head):
        d.rectangle([x + j * cw, y, x + (j + 1) * cw, y + h * 2], outline=40)
        for k, part in enumerate(_wrap(str(t), int(cw / (fs * 0.62)))[:2]):
            d.text((x + j * cw + 4, y + 3 + k * (fs + 2)), part, fill=20, font=fb)
    y += h * 2
    for r in rows:
        lines = [_wrap(str(v), int(cw / (fs * 0.62))) for v in r]
        rh = max(len(l) for l in lines) * (fs + 3) + 10
        for j, l in enumerate(lines):
            d.rectangle([x + j * cw, y, x + (j + 1) * cw, y + rh], outline=40)
            for k, part in enumerate(l):
                d.text((x + j * cw + 4, y + 5 + k * (fs + 3)), part, fill=25, font=f)
        y += rh
    return y


def _wrap(s, n):
    n = max(n, 4)
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    out.append(cur)
    return out


def draw_content(c):
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    y = 50
    d.text((60, y), c["from"][0], fill=10, font=font(24))
    d.text((W - 330, y), c["title"], fill=10, font=font(26))
    y += 34
    for a in c["from"][1]:
        d.text((60, y), a, fill=40, font=font(14))
        y += 18
    y += 14
    d.line([60, y, W - 60, y], fill=60, width=2)
    y += 14
    yy = y
    d.text((60, y), f"{c['to'][0]}:", fill=20, font=font(15))
    d.text((60, y + 20), c["to"][1], fill=20, font=font(15))
    for k, a in enumerate(c["to"][2]):
        d.text((60, y + 40 + 18 * k), a, fill=40, font=font(14))
    for k, (a, b) in enumerate(c["fields"]):
        d.text((500, yy + 20 * k), f"{a}:", fill=20, font=font(15))
        d.text((680, yy + 20 * k), str(b), fill=20, font=font(15))
    y = max(y + 40 + 18 * len(c["to"][2]), yy + 20 * len(c["fields"])) + 24
    if c.get("rush"):
        d.text((60, y), "URGENT - RUSH ORDER. Please confirm capacity within 24 hours.", fill=0, font=font(16))
        y += 30
    head, rows = c["table"]
    y = _table(d, 60, y, head, rows, W - 120) + 16
    for k, v in c["totals"]:
        d.text((W - 470, y), f"{k}:  {v}", fill=15, font=font(15))
        y += 22
    y += 10
    for n in c["notes"]:
        for part in _wrap(n, 95):
            d.text((60, y), part, fill=40, font=font(13))
            y += 17
    if c.get("sign"):
        d.line([60, y + 60, 300, y + 60], fill=60)
        d.text((60, y + 66), c["sign"], fill=60, font=font(12))
    return img


def degrade(img, rng: random.Random, stamp="RECEIVED", note=None):
    rgb = Image.merge("RGB", [img] * 3)
    d = ImageDraw.Draw(rgb, "RGBA")
    # stamp
    sx, sy = rng.randint(520, 640), rng.randint(900, 1050)
    d.ellipse([sx, sy, sx + 220, sy + 110], outline=(170, 30, 40, 170), width=5)
    d.text((sx + 40, sy + 30), stamp, fill=(170, 30, 40, 190), font=font(26))
    d.text((sx + 52, sy + 64), "Stores dept.", fill=(170, 30, 40, 170), font=font(14))
    if note:  # handwritten-style note: blue, slightly sheared text
        layer = Image.new("RGBA", (520, 60), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((5, 10), note, fill=(25, 45, 150, 230), font=font(22))
        layer = layer.transform(layer.size, Image.AFFINE, (1, 0.25, -10, 0, 1, 0), resample=Image.BICUBIC)
        rgb.paste(layer, (rng.randint(70, 260), rng.randint(1150, 1210)), layer)
    # paper tone, rotation, blur, noise
    tone = rng.randint(222, 238)
    bg = Image.new("RGB", rgb.size, (tone, tone, tone - 6))
    rgb = Image.blend(rgb, bg, 0.12)
    rgb = rgb.rotate(rng.uniform(-2.8, 2.8), resample=Image.BICUBIC, expand=False, fillcolor=(tone - 30,) * 3)
    rgb = rgb.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 0.9)))
    noise = Image.effect_noise(rgb.size, rng.uniform(14, 24)).convert("RGB")
    rgb = Image.blend(rgb, noise, 0.07)
    return rgb


def scan_pdf(content, path, rng, note=None):
    img = degrade(draw_content(content), rng, note=note)
    fixed = datetime(2026, 11, 21, 9, 0, 0)
    img.save(path, "PDF", resolution=110.0, creationDate=fixed.timetuple(), modDate=fixed.timetuple(), producer="scanner")


def packing_log_photo(ctx, rows, path, title_date):
    """A hand-filled paper log, photographed (JPG)."""
    img = Image.new("L", (W, int(H * 0.62)), 250)
    d = ImageDraw.Draw(img)
    d.text((60, 40), ctx.company()["name"], fill=20, font=font(22))
    d.text((60, 72), f"DAILY PACKING LOG      Date: {title_date}", fill=20, font=font(18))
    head = ["Line", "Product", "Batch No.", "Order ref", "Packed", "Rejects", "Pending", "Remarks"]
    _table(d, 40, 120, head, rows, W - 80, fs=15)
    rgb = degrade(img, ctx.rng, stamp="CHECKED", note=None)
    rgb.save(path, "JPEG", quality=72)
