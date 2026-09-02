"""Compose the two promo one-pagers (PNG long images + A4 PDFs).

Inputs: element screenshots in _raw/, mascot, QR codes.
Outputs: 01-调查员使用指南.png/.pdf, 02-业务人员使用指南.png/.pdf
"""

import pathlib

import qrcode
from PIL import Image, ImageDraw, ImageFont


ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "_raw"
FRONT_STATIC = pathlib.Path(r"D:\code_codex\labor-survey-ai\backend\static")

W = 1080
BRAND = "#03499F"
BRAND_2 = "#0250AC"
BRAND_SOFT = "#E8F0FA"
INK = "#0F172A"
INK_2 = "#475569"
INK_3 = "#64748B"
BG = "#F7F8FA"
LINE = "#E2E8F0"
OK = "#16A34A"
WARN = "#D97706"
WHITE = "#FFFFFF"

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=fnt) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def paste_rounded(base: Image.Image, img: Image.Image, xy: tuple[int, int], radius: int = 16) -> None:
    base.paste(rounded(img, radius), xy, rounded(img, radius))


def draw_card(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, fill: str = WHITE) -> None:
    d.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=fill, outline=LINE, width=2)


def qr_image(url: str, size: int = 300) -> Image.Image:
    qr = qrcode.QRCode(box_size=12, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    im = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return im.resize((size, size), Image.LANCZOS)


def new_canvas() -> Image.Image:
    return Image.new("RGB", (W, 6000), BG)


def header(img: Image.Image, title: str, subtitle: str) -> int:
    d = ImageDraw.Draw(img)
    h = 260
    d.rounded_rectangle([0, 0, W, h], radius=0, fill=BRAND)
    d.rectangle([0, h - 6, W, h], fill=BRAND_2)

    mascot = Image.open(FRONT_STATIC / "mascot.png").convert("RGBA").resize((190, 190), Image.LANCZOS)
    img.paste(mascot, (W - 240, 42), mascot)

    over = font(30, True)
    d.text((70, 46), "劳动力调查 AI 助手", font=over, fill="#BFD9F7")
    t = font(64, True)
    d.text((70, 96), title, font=t, fill=WHITE)
    sub = font(34)
    d.text((70, 186), subtitle, font=sub, fill="#D6E6FA")
    return h


def section_title(img: Image.Image, y: int, text: str) -> int:
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([70, y, 78, y + 34], radius=6, fill=BRAND)
    fnt = font(40, True)
    d.text((100, y), text, font=fnt, fill=INK)
    return y + 62


def step_num(d: ImageDraw.ImageDraw, x: int, y: int, n: int) -> None:
    r = 34
    d.ellipse([x, y, x + r * 2, y + r * 2], fill=BRAND)
    fnt = font(34, True)
    t = str(n)
    tw = d.textlength(t, font=fnt)
    d.text((x + r - tw / 2, y + 8), t, font=fnt, fill=WHITE)


def footer(img: Image.Image, y: int, url: str, note: str) -> int:
    d = ImageDraw.Draw(img)
    d.rectangle([0, y, W, y + 200], fill=WHITE)
    d.line([70, y, W - 70, y], fill=LINE, width=2)
    fnt = font(26)
    d.text((70, y + 30), "访问地址：" + url, font=fnt, fill=BRAND)
    for i, line in enumerate(wrap(d, note, fnt, W - 140)):
        d.text((70, y + 72 + i * 38), line, font=fnt, fill=INK_3)
    return y + 200


def build_enumerator() -> Image.Image:
    img = new_canvas()
    y = header(img, "调查员使用指南", "填报遇难题，问它最快")

    # --- 三步 ---
    y = section_title(img, y + 36, "三步上手")
    login_card = Image.open(RAW / "login-card.png").resize((430, 400), Image.LANCZOS)
    qa = Image.open(RAW / "chat-qa-clip.png")
    qa_w = 560
    qa_h = round(qa.size[1] * qa_w / qa.size[0])
    qa = qa.resize((qa_w, qa_h), Image.LANCZOS)

    # Step 1: 打开
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 190)
    d = ImageDraw.Draw(img)
    step_num(d, 100, y + 78, 1)
    t = font(38, True)
    d.text((170, y + 40), "打开页面", font=t, fill=INK)
    body = font(30)
    for i, line in enumerate(wrap(d, "微信扫码，或浏览器输入：\nlaborforceai.xyz", body, W - 380)):
        d.text((170, y + 96 + i * 42), line, font=body, fill=INK_2)
    qr = qr_image("https://laborforceai.xyz", 130)
    img.paste(qr, (W - 220, y + 28))
    y += 190 + 28

    # Step 2: 登录
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 560)
    d = ImageDraw.Draw(img)
    step_num(d, 100, y + 28, 2)
    t = font(38, True)
    d.text((170, y - 6), "手机号登录", font=t, fill=INK)
    body = font(30)
    for i, line in enumerate(wrap(d, "输入自己的手机号登录。若提示未授权，联系本区县业务管理员开通。", body, W - 300)):
        d.text((170, y + 52 + i * 42), line, font=body, fill=INK_2)
    paste_rounded(img, login_card, (W // 2 - 215, y + 140), radius=18)
    y += 560 + 28

    # Step 3: 提问
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, qa_h + 170)
    d = ImageDraw.Draw(img)
    step_num(d, 100, y + 28, 3)
    t = font(38, True)
    d.text((170, y - 6), "直接提问", font=t, fill=INK)
    body = font(30)
    for i, line in enumerate(wrap(d, "输入问题并发送，答完可点 👍/👎 反馈，帮助系统越答越准。", body, W - 300)):
        d.text((170, y + 52 + i * 42), line, font=body, fill=INK_2)
    paste_rounded(img, qa, (W // 2 - qa_w // 2, y + 140), radius=18)
    y += qa_h + 170 + 30

    # --- 底部提示 ---
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 130, fill=BRAND_SOFT)
    d = ImageDraw.Draw(img)
    fnt = font(30)
    lines = wrap(d, "提示：系统只提供填报指导，不收集居民个人信息；拿不准的制度问题，以《劳动力调查制度》原文为准。", fnt, W - 200)
    for i, line in enumerate(lines):
        d.text((100, y + 22 + i * 42), line, font=fnt, fill=BRAND)
    y += 130 + 24

    y = footer(img, y, "https://laborforceai.xyz", "内部资料 · 请勿外传 ｜ v1.0 · 2026-08")
    return img.crop((0, 0, W, y))


def build_business() -> Image.Image:
    img = new_canvas()
    y = header(img, "业务人员使用指南", "管好本区县账号，看着大家用起来")

    # --- 前台（简版） ---
    y = section_title(img, y + 36, "一、调查员前台怎么用（你也要会）")
    login_card = Image.open(RAW / "login-card.png").resize((300, 280), Image.LANCZOS)
    qa = Image.open(RAW / "chat-qa-clip.png")
    qa_w = 380
    qa_h = round(qa.size[1] * qa_w / qa.size[0])
    qa = qa.resize((qa_w, qa_h), Image.LANCZOS)
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, max(qa_h, 280) + 90)
    d = ImageDraw.Draw(img)
    body = font(28)
    text = "扫码/打开 laborforceai.xyz → 手机号登录 → 输入问题。"
    for i, line in enumerate(wrap(d, text, body, W - 200)):
        d.text((100, y + 22 + i * 40), line, font=body, fill=INK_2)
    paste_rounded(img, login_card, (100, y + 92), radius=14)
    paste_rounded(img, qa, (460, y + 92), radius=14)
    y += max(qa_h, 280) + 90 + 34

    # --- 后台（重点） ---
    y = section_title(img, y, "二、后台管理（重点）")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([70, y, W - 70, y + 120], radius=18, fill=BRAND_SOFT)
    fnt = font(30)
    lines = wrap(d, "后台入口：laborforceai.xyz/dashboard（或扫底部二维码）\n区县业务员登录后，直达「白名单管理」。", fnt, W - 200)
    d.text((100, y + 18), lines[0], font=fnt, fill=BRAND)
    d.text((100, y + 66), lines[1], font=fnt, fill=BRAND)
    y += 120 + 26

    # 白名单
    wh = Image.open(RAW / "whitelist-header.png").resize((W - 140, 52), Image.LANCZOS)
    tb = Image.open(RAW / "whitelist-toolbar.png").resize((W - 140, 30), Image.LANCZOS)
    table = Image.open(RAW / "whitelist-table.png").resize((W - 140, 157), Image.LANCZOS)
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 52 + 30 + 157 + 180)
    d = ImageDraw.Draw(img)
    t = font(36, True)
    d.text((100, y + 16), "白名单管理", font=t, fill=INK)
    yy = y + 66
    paste_rounded(img, wh, (100, yy), radius=10)
    paste_rounded(img, tb, (100, yy + 52), radius=8)
    paste_rounded(img, table, (100, yy + 82), radius=8)
    body = font(28)
    for i, line in enumerate(wrap(d, "调查员登不上 = 白名单里没有他。点「+ 新增条目」把手机号加进来；「导出」留档；离职用「批量停用」。", body, W - 240)):
        d.text((100, yy + 252 + i * 40), line, font=body, fill=INK_2)
    y += 52 + 30 + 157 + 130 + 30

    # 看板
    dash = Image.open(RAW / "dashboard-top.png").crop((0, 0, 1440, 480)).resize((W - 140, 313), Image.LANCZOS)
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 313 + 160)
    d = ImageDraw.Draw(img)
    t = font(36, True)
    d.text((100, y + 16), "数据看板", font=t, fill=INK)
    paste_rounded(img, dash, (100, y + 66), radius=10)
    body = font(28)
    for i, line in enumerate(wrap(d, "看本区县使用情况：谁在用、问了多少、反馈采纳率，及时发现问题。", body, W - 240)):
        d.text((100, y + 395 + i * 40), line, font=body, fill=INK_2)
    y += 313 + 160 + 30

    # 账号开通
    draw_card(ImageDraw.Draw(img), 70, y, W - 140, 130, fill="#FCF3E3")
    d = ImageDraw.Draw(img)
    fnt = font(30)
    lines = wrap(d, "账号开通：省队已统一导入现有职工信息，区县业务员直接用手机号登录即可。本区县调查员账号由你在后台自行管理。", fnt, W - 200)
    for i, line in enumerate(lines):
        d.text((100, y + 20 + i * 42), line, font=fnt, fill=WARN)
    y += 130 + 24

    # 底部二维码（后台）
    d = ImageDraw.Draw(img)
    qr = qr_image("https://laborforceai.xyz/dashboard", 190)
    img.paste(qr, (W - 280, y + 8))
    y = footer(img, y, "后台入口：https://laborforceai.xyz/dashboard", "内部资料 · 请勿外传 ｜ 不收集居民个人信息 ｜ v1.0 · 2026-08")
    return img.crop((0, 0, W, y))


def save_pdf(png: pathlib.Path, pdf: pathlib.Path) -> None:
    img = Image.open(png)
    page_w, page_h = 2480, 3508  # A4 @300dpi
    ratio = min(page_w / img.size[0], page_h / img.size[1])
    nw = int(img.size[0] * ratio * 0.94)
    nh = int(img.size[1] * ratio * 0.94)
    canvas = Image.new("RGB", (page_w, page_h), WHITE)
    thumb = img.resize((nw, nh), Image.LANCZOS)
    canvas.paste(thumb, ((page_w - nw) // 2, (page_h - nh) // 2))
    canvas.save(pdf, "PDF", resolution=300)


def main() -> None:
    e = build_enumerator()
    e.save(ROOT / "01-调查员使用指南.png")
    save_pdf(ROOT / "01-调查员使用指南.png", ROOT / "01-调查员使用指南.pdf")

    b = build_business()
    b.save(ROOT / "02-业务人员使用指南.png")
    save_pdf(ROOT / "02-业务人员使用指南.png", ROOT / "02-业务人员使用指南.pdf")

    print("enumerator:", e.size)
    print("business:", b.size)
    print("saved PNG + PDF")


if __name__ == "__main__":
    main()
