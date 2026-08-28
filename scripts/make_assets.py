"""生成项目视觉资产:exe 图标 assets/icon.ico 与宣传横幅 docs/banner.png。

仅构建期使用(Pillow),运行时不依赖。
用法:python scripts/make_assets.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"

DARK = (15, 17, 23)        # #0F1117
CARD = (23, 26, 35)        # #171A23
LINE = (38, 43, 56)        # #262B38
INDIGO = (99, 102, 241)
VIOLET = (139, 92, 246)
TEXT = (230, 233, 242)
MUT = (138, 147, 168)
GREEN = (52, 211, 153)
AMBER = (245, 158, 11)
ROSE = (244, 63, 94)


def font(candidates, size):
    for name in candidates:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


F_ZH = ["msyhbd.ttc", "msyh.ttc"]
F_ZH_R = ["msyh.ttc"]
F_EN_B = ["segoeuib.ttf", "seguisb.ttf", "segoeui.ttf"]
F_MONO = ["consolab.ttf", "consola.ttf"]


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------- 图标

def make_icon():
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底板:深色圆角方块 + 对角渐变(左上靛蓝 -> 右下近黑)
    base = Image.new("RGBA", (S, S))
    bd = ImageDraw.Draw(base)
    for y in range(S):
        for_step = y / S
        row = lerp(lerp((40, 44, 78), (18, 20, 30), y / S), (18, 20, 30), 0.0)
        bd.line([(0, y), (S, y)], fill=row)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=255)
    img.paste(base, (0, 0), mask)

    # 4×4 热力图格子
    cells = [0.15, 0.35, 0.65, 0.30,
             0.40, 0.95, 0.55, 0.20,
             0.25, 0.70, 1.00, 0.45,
             0.10, 0.30, 0.60, 0.25]
    pad, gap = 30, 10
    cell = (S - pad * 2 - gap * 3) // 4
    for i, a in enumerate(cells):
        r, c = divmod(i, 4)
        x0 = pad + c * (cell + gap)
        y0 = pad + r * (cell + gap)
        color = lerp((52, 56, 88), VIOLET if a > 0.8 else INDIGO, min(1, a * 1.2))
        alpha = int(90 + 165 * a)
        d.rounded_rectangle((x0, y0, x0 + cell, y0 + cell), radius=12,
                            fill=color + (alpha,))
    # 右下角一个小小的时间指针,点出"时间"主题
    cx, cy, r = S - 46, S - 46, 20
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(15, 17, 23, 230),
              outline=TEXT + (255,), width=4)
    d.line((cx, cy, cx, cy - 12), fill=TEXT + (255,), width=4)
    d.line((cx, cy, cx + 8, cy + 3), fill=INDIGO + (255,), width=4)

    ASSETS.mkdir(exist_ok=True)
    img.save(ASSETS / "icon.ico",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    img.save(ASSETS / "icon.png")
    print("icon.ico / icon.png ->", ASSETS)


# ---------------------------------------------------------------- 横幅

def gradient_bg(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bottom, y / h))
    return img


def heat_pattern(x0, y0, cw, ch, gap, cols=24, rows=7, seed=7):
    """画一块仿真实作息的热力图,返回 Image。"""
    rng = random.Random(seed)
    w = cols * cw + (cols - 1) * gap
    h = rows * ch + (rows - 1) * gap
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    for r in range(rows):
        for c in range(cols):
            base = 0.04
            if 9 <= c <= 12:
                base = 0.55 + 0.4 * rng.random()
            elif 14 <= c <= 18:
                base = 0.65 + 0.35 * rng.random()
            elif 20 <= c <= 23:
                base = 0.35 + 0.45 * rng.random()
            elif 1 <= c <= 7:
                base = 0.03
            else:
                base = 0.15 + 0.3 * rng.random()
            if rng.random() < 0.08:
                base *= 0.25
            a = int(255 * min(1, base))
            color = VIOLET if base > 0.85 else INDIGO
            td.rounded_rectangle(
                (c * (cw + gap), r * (ch + gap),
                 c * (cw + gap) + cw, r * (ch + gap) + ch),
                radius=5, fill=color + (a,) if base > 0.06 else (46, 51, 70, 70))
    return tile


def make_banner():
    W, H = 1280, 640
    img = gradient_bg(W, H, (18, 20, 32), DARK)
    d = ImageDraw.Draw(img, "RGBA")

    # 细网格点阵,制造质感
    for gx in range(40, W, 44):
        for gy in range(40, H, 44):
            d.ellipse((gx - 1, gy - 1, gx + 1, gy + 1), fill=(255, 255, 255, 7))

    # 右上角一片靛蓝光晕(高斯模糊出柔和过渡)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((820, -220, 1500, 420), fill=INDIGO + (46,))
    gd.ellipse((1020, 60, 1420, 460), fill=VIOLET + (30,))
    glow = glow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"])
                       .GaussianBlur(70))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    # ---- 左侧文案
    x, y = 84, 96
    badge_text = "WINDOWS · 纯本地 · 零依赖 · 单文件"
    f_badge = font(F_ZH_R, 21)
    f_zh_sub = font(F_ZH, 46)
    f_zh_slogan = font(F_ZH_R, 29)
    f_zh_small = font(F_ZH_R, 20)
    f_en = font(F_EN_B, 92)
    f_mono = font(F_MONO, 22)

    tw = d.textlength(badge_text, font=f_badge)
    d.rounded_rectangle((x, y, x + tw + 44, y + 42), radius=21,
                        fill=(30, 34, 52, 255), outline=(99, 102, 241, 160), width=2)
    d.text((x + 22, y + 8), badge_text, font=f_badge, fill=MUT)
    y += 78

    # 渐变主标题:先把文字画成蒙版,再用渐变填充
    title = "ScreenTime"
    mask = Image.new("L", (700, 150), 0)
    ImageDraw.Draw(mask).text((0, 0), title, font=f_en, fill=255)
    grad = Image.new("RGB", mask.size)
    gdr = ImageDraw.Draw(grad)
    for gy in range(mask.size[1]):
        gdr.line([(0, gy), (mask.size[0], gy)],
                 fill=lerp((235, 238, 248), INDIGO, gy / mask.size[1]))
    img.paste(grad, (x - 4, y), mask)
    y += 158

    d.text((x, y), "屏幕时间被动审计器", font=f_zh_sub, fill=TEXT)
    y += 78
    d.text((x, y), "你的一天,尽在一张热力图。", font=f_zh_slogan, fill=MUT)
    y += 74

    chips = [((99, 102, 241), "专注热力图"), ((139, 92, 246), "分类统计"),
             ((52, 211, 153), "专注分"), ((244, 63, 94), "数据不出本机")]
    fx = x
    for color, label in chips:
        label_w = d.textlength(label, font=f_zh_small)
        d.rounded_rectangle((fx, y, fx + label_w + 52, y + 38), radius=19,
                            fill=(24, 28, 44, 255), outline=LINE + (255,), width=1)
        d.ellipse((fx + 14, y + 13, fx + 26, y + 25), fill=color + (255,))
        d.text((fx + 34, y + 6), label, font=f_zh_small, fill=(198, 205, 222))
        fx += label_w + 64

    d.text((x, H - 64), "github.com/uahz/screen-auditor", font=f_mono, fill=(100, 116, 139))

    # ---- 右侧报告卡片模拟
    card_w, card_h = 470, 470
    cx, cy = 740, 96
    d.rounded_rectangle((cx, cy, cx + card_w, cy + card_h), radius=26,
                        fill=CARD + (255,), outline=LINE + (255,), width=2)
    d.rounded_rectangle((cx + 26, cy + 26, cx + 44, cy + 44), radius=5,
                        fill=INDIGO + (255,))
    d.rounded_rectangle((cx + 32, cy + 32, cx + 38, cy + 44), radius=2,
                        fill=(240, 242, 250, 255))
    d.text((cx + 56, cy + 22), "专注热力图", font=f_zh_small, fill=TEXT)
    tile = heat_pattern(0, 0, 13, 17, 5, cols=24, rows=7)
    img.paste(tile, (cx + 26, cy + 62), tile)

    bars = [("工作", 0.92, INDIGO), ("浏览", 0.74, VIOLET),
            ("沟通", 0.38, GREEN), ("娱乐", 0.22, ROSE)]
    by = cy + 62 + 7 * 17 + 6 * 5 + 34
    for label, wfrac, color in bars:
        d.text((cx + 26, by - 2), label, font=f_zh_small, fill=MUT)
        d.rounded_rectangle((cx + 84, by + 2, cx + card_w - 30, by + 14),
                            radius=6, fill=(32, 36, 47, 255))
        fillw = int((card_w - 114) * wfrac)
        if fillw > 12:
            d.rounded_rectangle((cx + 84, by + 2, cx + 84 + fillw, by + 14),
                                radius=6, fill=color + (255,))
        by += 30

    DOCS.mkdir(exist_ok=True)
    img.save(DOCS / "banner.png")
    print("banner.png ->", DOCS)


if __name__ == "__main__":
    make_icon()
    make_banner()
