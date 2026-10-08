#!/usr/bin/env python3
"""Generate noren/sign artwork for the hentaigana courseware (assets/*.png).

毛笔楷书体 = Yuji Syuku（SIL OFL 1.1，google/fonts）。字体缺失时自动下载到
tools/fonts/。运行：python3 tools/gen_signs.py（输出到 assets/）。
"""

import math
import random
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONT_DIR = Path(__file__).resolve().parent / "fonts"
BRUSH_TTF = FONT_DIR / "YujiSyuku-Regular.ttf"
BRUSH_URL = "https://github.com/google/fonts/raw/main/ofl/yujisyuku/YujiSyuku-Regular.ttf"
SANS = "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"

BG = (36, 53, 107)
BG_DARK = (28, 42, 90)
INK = (246, 248, 252)
GOLD = (201, 168, 106)


def ensure_font():
    if BRUSH_TTF.exists():
        return
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {BRUSH_URL}")
    urllib.request.urlretrieve(BRUSH_URL, BRUSH_TTF)


def brush(size):
    return ImageFont.truetype(str(BRUSH_TTF), size)


def texture(img, seed=7):
    rng = random.Random(seed)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    for x in range(0, w, 3):
        a = rng.randint(4, 14)
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, a), width=1)
    for y in range(0, h, 4):
        a = rng.randint(2, 8)
        d.line([(0, y), (w, y)], fill=(0, 0, 30, a), width=1)
    return img


def text_w(font, s):
    box = font.getbbox(s)
    return box[2] - box[0]


def draw_row(img, chars, size, y, gap=34, fill=INK, font=None, seed=0):
    f = font or brush(size)
    d = ImageDraw.Draw(img)
    total = sum(text_w(f, c) for c in chars) + gap * (len(chars) - 1)
    x = (img.width - total) / 2
    for c in chars:
        d.text((x, y), c, font=f, fill=fill, anchor="lm")
        x += text_w(f, c) + gap
    return img


def draw_vertical(img, chars, size, x, y_top, gap=6, fill=INK):
    f = brush(size)
    d = ImageDraw.Draw(img)
    y = y_top
    for c in chars:
        d.text((x, y), c, font=f, fill=fill, anchor="mm")
        y += size + gap
    return img


def draw_crest(img, cx, cy, r=56, color=INK, width=5):
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
    inner = r * 0.60
    d.polygon([
        (cx, cy - inner),
        (cx + inner * 0.52, cy - inner * 0.05),
        (cx, cy + inner * 0.28),
        (cx - inner * 0.52, cy - inner * 0.05),
    ], outline=color, width=width - 1)
    d.ellipse([cx - inner * 0.92, cy + inner * 0.18, cx - inner * 0.10, cy + inner], outline=color, width=width - 2)
    d.ellipse([cx + inner * 0.10, cy + inner * 0.18, cx + inner * 0.92, cy + inner], outline=color, width=width - 2)
    return img


def dakuten(img, cx, cy, color=INK, w=9, length=30, angle=-38):
    d = ImageDraw.Draw(img)
    for dx in (-16, 14):
        a = math.radians(angle)
        x0, y0 = cx + dx, cy
        x1 = x0 + length * math.cos(a + math.pi / 2 + 0.5)
        y1 = y0 + length * math.sin(a + math.pi / 2 + 0.5)
        d.line([(x0, y0), (x1, y1)], fill=color, width=w)
    return img


def banner(chars, size=196, small_left=None, crest=False, dakuten_on=None, name="sign.png", w=1200, h=420, gap=40):
    img = Image.new("RGB", (w, h), BG)
    texture(img)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=BG_DARK, width=10)
    if small_left:
        draw_vertical(img, small_left, 54, 118, 148, gap=10)
    draw_row(img, chars, size, h / 2, gap=gap)
    if crest:
        draw_crest(img, w - 128, h / 2)
    if dakuten_on is not None:
        f = brush(size)
        total = sum(text_w(f, c) for c in chars) + gap * (len(chars) - 1)
        x = (w - total) / 2
        idx, off = dakuten_on
        for i, c in enumerate(chars):
            if i == idx:
                cx = x + text_w(f, c) / 2 + off
                dakuten(img, cx + text_w(f, c) / 2 - 6, h / 2 - size * 0.38)
                break
            x += text_w(f, c) + gap
    img.save(ASSETS / name)
    print("wrote", ASSETS / name)


def letter_card(ch, name, kana=""):
    img = Image.new("RGB", (300, 300), BG)
    texture(img, seed=3)
    d = ImageDraw.Draw(img)
    d.rectangle([14, 14, 285, 285], outline=(255, 255, 255, 60), width=2)
    d.rectangle([22, 22, 277, 277], outline=GOLD, width=1)
    f = brush(198)
    d.text((150, 138), ch, font=f, fill=INK, anchor="mm")
    if kana:
        fk = ImageFont.truetype(SANS, 34)
        d.text((150, 245), kana, font=fk, fill=GOLD, anchor="mm")
    img.save(ASSETS / name)
    print("wrote", ASSETS / name)


def ki_vs_sei():
    w, h = 720, 460
    img = Image.new("RGB", (w, h), BG)
    texture(img, seed=11)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=BG_DARK, width=10)
    f = brush(210)
    d.text((200, 200), "幾", font=f, fill=INK, anchor="mm")
    d.text((520, 200), "生", font=f, fill=INK, anchor="mm")
    fs = ImageFont.truetype(SANS, 40)
    d.text((360, 195), "?", font=fs, fill=GOLD, anchor="mm")
    fcap = ImageFont.truetype(SANS, 30)
    d.text((200, 355), "幾（草書）", font=fcap, fill=INK, anchor="mm")
    d.text((520, 355), "生（に見える）", font=fcap, fill=GOLD, anchor="mm")
    img.save(ASSETS / "cmp_ki_sei.png")
    print("wrote", ASSETS / "cmp_ki_sei.png")


def main():
    ensure_font()
    ASSETS.mkdir(exist_ok=True)
    banner(["幾", "楚", "者"], small_left="御膳", crest=True, name="sign_kisoba.png")
    banner(["幾", "楚", "者"], name="sign_kisoba_plain.png")
    banner(["楚", "者"], size=210, dakuten_on=(1, 0), name="sign_soba.png")
    banner(["志", "る", "古"], size=200, name="sign_shiruko.png")
    banner(["あ", "可", "よ", "ろ", "し"], size=158, gap=24, name="sign_akayoroshi.png")
    for ch, nm, kana in [
        ("幾", "letter_ki.png", "き"),
        ("楚", "letter_so.png", "そ"),
        ("者", "letter_ba.png", "は／ば"),
        ("八", "letter_ha.png", "は（助詞）"),
        ("志", "letter_shi.png", "し（語頭）"),
        ("之", "letter_shi2.png", "し（語中）"),
        ("古", "letter_ko.png", "こ"),
        ("可", "letter_ka.png", "か"),
        ("介", "letter_ke.png", "け"),
        ("徒", "letter_tsu.png", "つ"),
        ("地", "letter_ji.png", "ぢ"),
        ("由", "letter_yu.png", "ゆ"),
        ("曽", "letter_so2.png", "そ（現行）"),
        ("所", "letter_so3.png", "そ（変体）"),
        ("波", "letter_ha2.png", "は（現行）"),
        ("加", "letter_ka2.png", "か（現行）"),
        ("己", "letter_ko2.png", "こ（現行）"),
        ("知", "letter_chi.png", "ち（現行）"),
        ("川", "letter_tsu2.png", "つ（現行）"),
        ("安", "letter_a.png", "あ（現行）"),
    ]:
        letter_card(ch, nm, kana)
    ki_vs_sei()


if __name__ == "__main__":
    main()
