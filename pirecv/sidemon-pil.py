#!/usr/bin/env python3
"""SideMon PIL receiver — premium dashboard for 480x320 SPI display"""
import socket, json, threading, time, os, sys, argparse, select, calendar
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone, timedelta

W, H = 480, 320
FD = "/usr/share/fonts/truetype"

CJK_FONTS = [
    f"{FD}/droid/DroidSansFallbackFull.ttf",
    f"{FD}/noto/NotoSansCJK-Regular.ttc",
]

def ff(name, size):
    paths = {
        "r": [f"{FD}/piboto/Piboto-Regular.ttf", f"{FD}/dejavu/DejaVuSans.ttf"],
        "b": [f"{FD}/piboto/Piboto-Bold.ttf", f"{FD}/dejavu/DejaVuSans-Bold.ttf"],
        "c":  CJK_FONTS + [f"{FD}/piboto/Piboto-Regular.ttf", f"{FD}/dejavu/DejaVuSans.ttf"],
        "cb": CJK_FONTS + [f"{FD}/piboto/Piboto-Bold.ttf", f"{FD}/dejavu/DejaVuSans-Bold.ttf"],
    }
    for p in paths.get(name, paths["r"]):
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: continue
    return ImageFont.load_default()

F = {}
for s in [9,10,11,12,13,14,16,18,20,22,24,26,28,30,32,36,40,48]:
    F["r"+str(s)] = ff("r", s)
    F["b"+str(s)] = ff("b", s)
    F["c"+str(s)] = ff("c", s)
    F["cb"+str(s)] = ff("cb", s)

# ══════════════════════════════════════════════════════════════════════
# Design System — Bold, vibrant, commercial-grade
# ══════════════════════════════════════════════════════════════════════

BG = (12, 14, 20)
PN = (20, 22, 30)
CARD = (26, 28, 38)
BORDER = (44, 46, 58)
DIVIDER = (38, 40, 52)

TX = (240, 242, 248)
TX2 = (168, 172, 188)
TX3 = (100, 104, 120)

# Bold page accent colors
AC = {
    "sys":   (0, 210, 140),
    "api":   (50, 160, 255),
    "clash": (255, 160, 50),
    "codex": (170, 100, 255),
    "wthr":  (255, 200, 50),
    "dt":    (80, 200, 255),
    "omlx":  (80, 220, 110),
}

PAGE_BG = {
    "sys":   (10, 18, 22),
    "api":   (10, 14, 26),
    "clash": (22, 16, 10),
    "codex": (18, 12, 26),
    "wthr":  (22, 20, 10),
    "dt":    (10, 18, 24),
    "omlx":  (10, 20, 14),
}

state = {}; lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════
# Drawing Primitives
# ══════════════════════════════════════════════════════════════════════

def rr(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def card(d, x, y, w, h, fill=None, radius=6):
    f = fill or CARD
    rr(d, (x, y, x+w, y+h), radius, f)

def arc_g(d, cx, cy, r, pct, fg, width=10):
    bg = BORDER
    d.ellipse((cx-r, cy-r, cx+r, cy+r), outline=bg, width=width)
    if pct > 0.005:
        s, e = 225, 225 - 360*min(pct, 1)
        if e < 0: e += 360
        d.arc((cx-r, cy-r, cx+r, cy+r), s, e, fill=fg, width=width)

def bar(d, x, y, w, h, pct, fg, radius=None):
    if radius is None: radius = h//2
    rr(d, (x, y, x+w, y+h), radius, BORDER)
    fw = max(4, int(w * min(pct, 1)))
    if fw > h:
        rr(d, (x, y, x+fw, y+h), radius, fg)

def pct_color(pct):
    """Color based on percentage: green -> yellow -> red."""
    if pct < 0.5:
        return (0, 210, 140)     # green
    elif pct < 0.75:
        return (255, 200, 50)    # yellow
    elif pct < 0.9:
        return (255, 140, 40)    # orange
    else:
        return (255, 70, 70)     # red

def ct(d, y, text, fill, fk):
    tw = d.textlength(text, font=F[fk])
    d.text(((W-tw)//2, y), text, fill=fill, font=F[fk])

def hdr(d, title, accent, bg_tint=None):
    hdr_bg = bg_tint or PN
    d.rectangle((0, 0, W, 38), fill=hdr_bg)
    d.rectangle((0, 0, 4, 38), fill=accent)
    ctext(d, (14, 8), title, accent, "b20")

def nav(d, cur, total, accent):
    d.rectangle((0, H-16, W, H), fill=PN)
    d.line((0, H-16, W, H-16), fill=DIVIDER, width=1)
    nums = "  ".join([str(i+1) for i in range(total)])
    tw = d.textlength(nums, font=F["r10"])
    sx = (W - tw) // 2
    x = sx
    for i in range(total):
        ch = str(i+1)
        cw = d.textlength(ch, font=F["r10"])
        col = accent if i == cur else TX3
        d.text((x, H-14), ch, fill=col, font=F["r11"])
        x += cw + d.textlength("  ", font=F["r10"])

def fmt_tk(n):
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1000: return f"{n/1000:.0f}K"
    return str(n)

def fmtb(n):
    if n >= 1<<30: return f"{n/(1<<30):.1f}GB"
    if n >= 1<<20: return f"{n/(1<<20):.1f}MB"
    if n >= 1<<10: return f"{n/(1<<10):.0f}KB"
    return f"{n:.0f}B"


# ── CJK-aware text rendering (per-character font selection) ──
def _is_cjk(cp):
    """True if codepoint is in Chinese/Japanese/Korean character ranges."""
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x20000 <= cp <= 0x2A6DF or 0xF900 <= cp <= 0xFAFF or
            0x2F800 <= cp <= 0x2FA1F or 0x3000 <= cp <= 0x303F or
            0xFF00 <= cp <= 0xFFEF or 0xFE30 <= cp <= 0xFE4F)

def _cjk_font(fk):
    """Return CJK font key for the given font key (e.g. 'b20' -> 'cb20')."""
    if fk.startswith('b'): return 'cb' + fk[1:]
    return 'c' + fk[1:]

def ctext(d, xy, text, fill, fk="r14"):
    """Draw text per-character — CJK uses Chinese font, ASCII uses Latin font."""
    s = str(text)
    x, y = xy
    for ch in s:
        is_cjk = _is_cjk(ord(ch))
        use_fk = _cjk_font(fk) if is_cjk else fk
        font = F.get(use_fk, F.get(fk, F["r14"]))
        d.text((x, y), ch, fill=fill, font=font)
        x += d.textlength(ch, font=font)

def ctextlen(text, fk="r14"):
    """Measure text width with per-character font selection."""
    s = str(text)
    total = 0
    for ch in s:
        is_cjk = _is_cjk(ord(ch))
        use_fk = _cjk_font(fk) if is_cjk else fk
        font = F.get(use_fk, F.get(fk, F["r14"]))
        total += d.textlength(ch, font=font) if 'd' in dir() else font.getlength(ch)
    return total

TOTAL_PAGES = 7

# ══════════════════════════════════════════════════════════════════════
# Page 1: System — big thick gauges (2x size, 2x thickness)
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    bg = PAGE_BG["sys"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["sys"]
    hdr(d, "系统状态", ac, bg)

    # Three big arc gauges — 2x radius, 2x width
    gy = 110
    positions = [82, 240, 398]
    gauges = [
        (s.get("cpu",0)/100,  (0, 210, 140),  "CPU",  f"{int(s.get('cpu',0))}%", 56, 22),
        (s.get("mem",0)/100,  (50, 160, 255),  "内存",  f"{int(s.get('mem',0))}%", 56, 22),
        (s.get("disk",0)/100, (255, 160, 50),  "磁盘", f"{int(s.get('disk',0))}%", 56, 22),
    ]
    for i, (pct, color, label, val, radius, w_) in enumerate(gauges):
        x = positions[i]
        p = min(pct, 1)
        arc_g(d, x, gy, radius, p, color, width=w_)
        # Value centered inside ring
        tw = d.textlength(val, font=F["b28"])
        d.text((x - tw//2, gy - 14), val, fill=TX, font=F["b28"])
        # Label below ring
        tw2 = d.textlength(label, font=F.get("r13", F["r12"]))
        ctext(d, (x - tw2//2, gy + radius + 8), label, color, "r13")

    # Bottom info row — 3 cards
    by = 220
    cw = (W - 30) // 3
    card(d, 10, by, cw, 52)
    ctext(d, (18, by+6), "运行", TX3, "r12")
    ctext(d, (18, by+24), s.get("uptime","?"), ac, "b22")

    card(d, 20+cw, by, cw, 52)
    ld = s.get("load",[0,0,0])
    ctext(d, (28+cw, by+6), "负载", TX3, "r12")
    ctext(d, (28+cw, by+24), f"{ld[0]:.1f}  {ld[1]:.1f}", TX2, "b22")

    card(d, 30+2*cw, by, cw, 52)
    hn = s.get("hostname","?")
    if len(hn) > 14: hn = hn[:12]+".."
    ctext(d, (38+2*cw, by+6), "主机", TX3, "r12")
    ctext(d, (38+2*cw, by+24), hn, TX2, "b18")

    nav(d, 0, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 2: API Usage
# ══════════════════════════════════════════════════════════════════════

def pg_apis(api):
    bg = PAGE_BG["api"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["api"]
    hdr(d, "API 用量", ac, bg)

    y0 = 44
    # DeepSeek — balance in CNY
    by = y0
    card(d, 10, by, W-20, 54)
    ctext(d, (20, by+6), "DeepSeek", AC["api"], "b18")
    ds_bal = api.get("ds_balance", "?")
    try: ds_val = float(ds_bal)
    except: ds_val = 0
    ds_pct = min(ds_val / 50.0, 1.0) if ds_val > 0 else 0
    bar(d, 20, by+28, W-180, 12, ds_pct, AC["api"], radius=6)
    ctext(d, (W-140, by+22), f"CNY {ds_val:.1f}", TX, "b18")

    # MiMo — token plan usage %
    by2 = y0 + 68
    card(d, 10, by2, W-20, 54)
    ctext(d, (20, by2+6), "MiMo", AC["codex"], "b18")
    mm_pct = 0
    try: mm_pct = float(api.get("mm_balance", 0)) / 100.0
    except: pass
    mm_pct = min(mm_pct, 1.0)
    bar(d, 20, by2+28, W-180, 12, mm_pct, AC["codex"], radius=6)
    ctext(d, (W-140, by2+20), f"{mm_pct*100:.1f}%", TX, "b20")
    # Show usage detail below label
    mm_detail = api.get("mm_currency", "")
    if mm_detail:
        d.text((80, by2+8), mm_detail, fill=TX3, font=F["r10"])

    y1 = 188
    cw = (W - 40) // 3
    items = [
        ("总量", fmt_tk(api.get("total_tokens",0)), TX),
        ("输出", fmt_tk(api.get("output_tokens",0)), AC["sys"]),
        ("缓存", f"{api.get('cache_hit_rate',0):.1f}%", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(items):
        x = 10 + i * (cw + 10)
        card(d, x, y1, cw, 60)
        ctext(d, (x+10, y1+8), lbl, TX3, "r12")
        ctext(d, (x+10, y1+28), val, col, "b24")

    nav(d, 1, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 3: Clash — full info
# ══════════════════════════════════════════════════════════════════════

def pg_clash(cl):
    bg = PAGE_BG["clash"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["clash"]
    hdr(d, "Clash 代理", ac, bg)

    # Status badge
    st = "在线" if cl.get("running") else "离线"
    st_col = AC["sys"] if cl.get("running") else (220, 80, 80)
    badge_w = d.textlength(st, font=F["b11"]) + 16
    rr(d, (W-14-badge_w, 8, W-14, 26), 4, st_col)
    ctext(d, (W-14-badge_w+8, 9), st, BG, "b11")

    # Node + Mode row — two cards side by side
    y0 = 40
    cw2 = (W - 30) // 2
    node = cl.get("current_node", "?")
    if len(node) > 18: node = node[:16] + ".."
    card(d, 10, y0, cw2, 46)
    ctext(d, (20, y0+4), "节点", TX3, "r12")
    ctext(d, (20, y0+20), node, TX, "b22")

    card(d, 20+cw2, y0, cw2, 46)
    mode = cl.get("mode", "Rule")
    ctext(d, (28+cw2, y0+4), "模式", TX3, "r12")
    ctext(d, (28+cw2, y0+20), mode, AC["codex"], "b22")

    # Traffic card with progress bar inside
    y1 = 94
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    card(d, 10, y1, W-20, 62)
    ctext(d, (20, y1+4), "流量", TX3, "r12")
    if tu and tt:
        ctext(d, (20, y1+20), f"{tu} / {tt}", TX, "b22")
        tp = 0
        try:
            parts = tu.split()
            if parts:
                num_s = ''.join(c for c in parts[0] if c.isdigit() or c=='.')
                num = float(num_s) if num_s else 0
                parts2 = tt.split()
                den_s = ''.join(c for c in (parts2[0] if parts2 else '0') if c.isdigit() or c=='.')
                den = float(den_s) if den_s else 0
                tp = num/den if den > 0 else 0
        except: tp = 0
        # Bar inside card, with safe margin
        bar(d, 20, y1+44, W-56, 10, min(tp, 1.0), ac, radius=5)
    else:
        d.text((20, y1+20), "未知", fill=TX3, font=F["r18"])

    # Expire + Version + Connections — 3 columns
    y2 = 164
    cw3 = (W - 40) // 3
    card(d, 10, y2, cw3, 46)
    ctext(d, (18, y2+4), "到期", TX3, "r12")
    exp = cl.get("expire_date", "") or "未知"
    if len(exp) > 12: exp = exp[:10] + ".."
    ctext(d, (18, y2+22), exp, AC["wthr"], "b18")

    card(d, 20+cw3, y2, cw3, 46)
    ctext(d, (28+cw3, y2+4), "版本", TX3, "r12")
    ver = cl.get("version", "")[:8] or "?"
    ctext(d, (28+cw3, y2+22), ver, TX2, "b18")

    card(d, 30+2*cw3, y2, cw3, 46)
    ctext(d, (38+2*cw3, y2+4), "连接", TX3, "r12")
    conns = str(cl.get("active_connections", 0))
    tc = d.textlength(conns, font=F["b20"])
    ctext(d, (30+2*cw3+(cw3-tc)//2, y2+18), conns, ac, "b22")

    # Update time footer
    y3 = 220
    upd = cl.get("update_time", "")
    d.rectangle((0, y3, W, y3+56), fill=bg)
    d.text((20, y3+6), f"更新: {upd}", fill=TX3, font=F["r10"])

    nav(d, 2, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 4: Codex — percentage gauges with color by pct
# ══════════════════════════════════════════════════════════════════════

def pg_codex(cx):
    bg = PAGE_BG["codex"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["codex"]
    hdr(d, "Codex 用量", ac, bg)

    # Two large percentage rings — 5H and 7D, labels below
    ring_y = 108
    radius = 68
    ring_width = 18
    rings = [
        (135, "pct_5h", "5小时"),
        (345, "pct_7d", "7天"),
    ]
    for cx_pos, pct_key, label in rings:
        pct = cx.get(pct_key, 0)
        p = min(pct / 100.0, 1.0) if pct > 0 else 0.0
        color = pct_color(p)
        arc_g(d, cx_pos, ring_y, radius, p, color, width=ring_width)
        # Percentage in center of ring
        pct_text = f"{int(pct)}%"
        tw = d.textlength(pct_text, font=F["b26"])
        ctext(d, (cx_pos - tw//2, ring_y - 14), pct_text, TX, "b28")
        # Label below ring
        lw = d.textlength(label, font=F["b16"])
        ctext(d, (cx_pos - lw//2, ring_y + radius + 14), label, ac, "b20")

    # Bottom info card: reset times with larger font
    by = 232
    card(d, 10, by, W-20, 48)
    ctext(d, (20, by+2), "5小时重置", TX3, "r12")
    ctext(d, (20, by+16), cx.get("reset_5h","?"), ac, "b22")
    ctext(d, (W//2+4, by+2), "7天重置", TX3, "r12")
    ctext(d, (W//2+4, by+16), cx.get("reset_7d","?"), AC["wthr"], "b22")

    nav(d, 3, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 5: Weather
# ══════════════════════════════════════════════════════════════════════

def pg_weather(w):
    bg = PAGE_BG["wthr"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["wthr"]
    hdr(d, "天气", ac, bg)

    hw = (W-30)//2

    temp_c = w.get("temp_c", 0)
    card(d, 10, 40, hw, 68)
    ctext(d, (20, 44), "温度", TX3, "r12")
    ctext(d, (20, 60), f"{int(temp_c)}°C", TX, "b32")
    d.text((20, 96), w.get("city","?"), fill=TX3, font=F["r10"])

    cond = w.get("condition", "?")
    if len(cond) > 16: cond = cond[:14]+".."
    card(d, 20+hw, 40, hw, 68)
    ctext(d, (30+hw, 44), "天气", TX3, "r12")
    ctext(d, (30+hw, 64), cond, TX, "b20")
    fl = w.get("feels_like_c", temp_c)
    ctext(d, (30+hw, 88), f"体感 {int(fl)}°C", TX3, "r12")

    y0 = 116
    card(d, 10, y0, hw, 38)
    ctext(d, (20, y0+4), "湿度", TX3, "r12")
    d.text((20, y0+18), f"{w.get('humidity',0)}%", fill=AC["api"], font=F["b16"])

    card(d, 20+hw, y0, hw, 38)
    ctext(d, (30+hw, y0+4), "风速", TX3, "r12")
    d.text((30+hw, y0+18), f"{w.get('wind_kph',0)} km/h", fill=TX2, font=F["b16"])

    y1 = 162
    forecasts = w.get("forecast", [])[:3]
    fw = (W-40)//3
    for i, fc in enumerate(forecasts):
        fx = 10 + i*(fw+5)
        card(d, fx, y1, fw, 54)
        day_en = fc.get("day","?")[:3]
        day_cn = {"Mon":"一","Tue":"二","Wed":"三","Thu":"四","Fri":"五","Sat":"六","Sun":"日"}.get(day_en, day_en)
        ctext(d, (fx+8, y1+4), day_cn, TX3, "r12")
        hi = fc.get("high_c","?")
        lo = fc.get("low_c","?")
        d.text((fx+8, y1+18), f"{hi}°", fill=AC["clash"], font=F["b16"])
        d.text((fx+56, y1+18), f"{lo}°", fill=AC["api"], font=F["b16"])
        c2 = fc.get("condition","?")
        if len(c2) > 12: c2 = c2[:10]+".."
        d.text((fx+8, y1+38), c2, fill=TX2, font=F["r9"])

    y2 = 224
    card(d, 10, y2, hw, 32)
    ctext(d, (20, y2+8), "日出落", TX3, "r12")
    d.text((56, y2+6), f"{w.get('sunrise','?')}", fill=ac, font=F["r12"])
    d.text((140, y2+6), f"{w.get('sunset','?')}", fill=ac, font=F["r12"])

    card(d, 20+hw, y2, hw, 32)
    ctext(d, (30+hw, y2+8), "紫外线", TX3, "r12")
    d.text((60+hw, y2+5), str(w.get("uv_index","?")), fill=AC["clash"], font=F["b16"])

    nav(d, 4, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 6: DateTime — colorful calendar
# ══════════════════════════════════════════════════════════════════════

def pg_datetime(dt_data):
    bg = PAGE_BG["dt"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["dt"]
    hdr(d, "日期时间", ac, bg)

    ts = dt_data.get("timestamp", 0)
    if ts > 0:
        utc_now = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_tz = timezone(timedelta(hours=8))
        now = utc_now.astimezone(local_tz)
    else:
        now = datetime.now()

    # Time card (left) — big clock
    card(d, 10, 40, 200, 72, fill=(20, 26, 36))
    time_str = now.strftime("%H:%M")
    tw = d.textlength(time_str, font=F["b40"])
    d.text((10+(200-tw)//2, 42), time_str, fill=(255, 255, 255), font=F["b40"])
    sec_str = now.strftime("%S")
    d.text((10+(200-d.textlength(sec_str,font=F["b16"]))//2, 88), sec_str, fill=(140, 180, 210), font=F["b16"])

    # Date card (right) — current date big and vivid
    card(d, 220, 40, 250, 72, fill=(20, 26, 36))
    date_str = now.strftime("%Y-%m-%d")
    d.text((232, 42), date_str, fill=(255, 220, 80), font=F["b22"])
    dow_en = now.strftime("%A")
    dow_cn = {"Monday":"周一","Tuesday":"周二","Wednesday":"周三","Thursday":"周四","Friday":"周五","Saturday":"周六","Sunday":"周日"}.get(dow_en, dow_en)
    ctext(d, (232, 68), dow_cn, ac, "b18")
    tz = now.strftime("UTC%z")
    d.text((232, 88), tz, fill=(80, 130, 180), font=F["r11"])

    # Calendar — colorful, larger fonts filling available space
    y0 = 130
    card(d, 10, y0, W-20, 168, fill=(16, 22, 32))
    month_str = now.strftime("%B %Y")
    tw = d.textlength(month_str, font=F["b14"])
    ctext(d, ((W-tw)//2, y0+4), month_str, ac, "b18")

    day_names = ["一", "二", "三", "四", "五", "六", "日"]
    day_colors = [
        (168, 172, 188), (168, 172, 188), (168, 172, 188),
        (168, 172, 188), (168, 172, 188), AC["api"], (255, 120, 80),
    ]
    col_w = (W-40) // 7
    # Sunday column bg
    su_x = 14 + 6 * col_w
    su_w = col_w - 2
    d.rectangle((su_x - 3, y0 + 20, su_x + su_w + 2, y0 + 174), fill=(40, 18, 18))
    for i, dn in enumerate(day_names):
        dx = 14 + i*col_w
        ctext(d, (dx, y0+22), dn, day_colors[i], "b13")

    cal = calendar.monthcalendar(now.year, now.month)
    today_day = now.day
    row_h = 24
    start_y = y0 + 40
    for wi, week in enumerate(cal):
        for di, day in enumerate(week):
            if day == 0: continue
            dx = 14 + di*col_w
            dy = start_y + wi * row_h
            if day == today_day:
                rr(d, (dx-3, dy-2, dx+col_w-4, dy+18), 4, ac)
                d.text((dx, dy), f"{day:2d}", fill=BG, font=F["b14"])
            else:
                if di == 6:
                    col = (255, 130, 100)
                elif di == 5:
                    col = (255, 170, 80)
                else:
                    col = (140, 200, 240)
                d.text((dx, dy), f"{day:2d}", fill=col, font=F["r13"])

    nav(d, 5, TOTAL_PAGES, ac)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 7: oMLX — no model list, clean layout
# ══════════════════════════════════════════════════════════════════════

def pg_omlx(om):
    bg = PAGE_BG["omlx"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["omlx"]
    hdr(d, "oMLX 仪表盘", ac, bg)

    st = "在线" if om.get("running") else "离线"
    st_col = AC["sys"] if om.get("running") else (220, 80, 80)
    badge_w = d.textlength(st, font=F["b11"]) + 16
    rr(d, (W-14-badge_w, 8, W-14, 26), 4, st_col)
    ctext(d, (W-14-badge_w+8, 9), st, BG, "b11")

    # Memory bar
    card(d, 10, 40, W-20, 48)
    mem_u = om.get("memory_used", 0)
    mem_c = om.get("memory_ceiling", 1)
    mem_p = mem_u/mem_c if mem_c > 0 else 0
    d.text((20, 44), "内存", fill=TX3, font=F["r10"])
    d.text((110, 44), f"{mem_u:.1f} / {mem_c:.1f} GB", fill=TX2, font=F["b14"])
    bar(d, 20, 66, W-40, 10, mem_p, ac, radius=5)

    # 4 stat boxes (2x2) — bigger
    y0 = 98
    hw = (W-30)//2
    stats = [
        ("模型", f"{om.get('loaded_count',0)} / {om.get('model_count',0)}", ac),
        ("请求", fmt_tk(om.get("total_requests",0)), TX),
        ("输入速度", f"{om.get('avg_prompt_speed',0):.1f} tk/s", AC["api"]),
        ("生成速度", f"{om.get('avg_gen_speed',0):.1f} tk/s", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(stats):
        x = 10 + (i%2)*(hw+10)
        y = y0 + (i//2)*52
        card(d, x, y, hw, 46)
        ctext(d, (x+10, y+6), lbl, TX3, "r12")
        ctext(d, (x+10, y+24), val, col, "b22")

    # Cache efficiency
    y1 = 208
    ce = om.get("cache_efficiency", 0)*100
    card(d, 10, y1, W-20, 52)
    ctext(d, (20, y1+6), "缓存效率", TX3, "r12")
    d.text((20, y1+26), f"{ce:.1f}%", fill=ac, font=F["b28"])
    bar(d, 150, y1+30, W-190, 12, ce/100, ac, radius=6)

    nav(d, 6, TOTAL_PAGES, ac)
    return img


# ══════════════════════════════════════════════════════════════════════
# Infrastructure
# ══════════════════════════════════════════════════════════════════════

RENDERERS = {
    "system": pg_system, "ccswitch": pg_apis, "clash": pg_clash,
    "codex": pg_codex, "weather": pg_weather, "datetime": pg_datetime,
    "omlx": pg_omlx,
}
DEFAULT_ORDER = ["system", "ccswitch", "clash", "codex", "weather", "datetime", "omlx"]
active_order = list(DEFAULT_ORDER)

def normalize_page_order(pages):
    order, seen = [], set()
    for p in pages or []:
        if p in RENDERERS and p not in seen:
            order.append(p); seen.add(p)
    return order or list(DEFAULT_ORDER)

def get_local_ips():
    ips = []
    try:
        import struct, fcntl
        for ifn in ['wlan0', 'eth0', 'en0']:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                ip = socket.inet_ntoa(fcntl.ioctl(
                    s.fileno(), 0x8915, struct.pack('256s', ifn[:15].encode()))[20:24])
                ips.append(ip)
            except: pass
            finally: s.close()
    except: pass
    return ips

def waiting_ip_text(ips=None):
    if ips is None: ips = get_local_ips()
    return f"IP: {', '.join(ips)}" if ips else "等待IP..."

def write_fb(path, img):
    img = img.rotate(180, expand=False)
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "BGRA"))

def show_waiting(fb_dev):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 34), fill=PN)
    d.rectangle((0, 0, 4, 34), fill=AC["sys"])
    ctext(d, (14, 7), "SIDEMON", AC["sys"], "b20")
    ct(d, H//2-40, "等待中...", TX3, "b22")
    ct(d, H//2, waiting_ip_text(), TX, "b22")
    ct(d, H//2+24, "Mac → Pi", TX3, "r12")
    write_fb(fb_dev, img)

def waiting_updater(fb_dev):
    while True:
        with lock:
            has_data = any(key in state for key in RENDERERS)
        if not has_data:
            try: show_waiting(fb_dev)
            except Exception as e: print(f"Waiting: {e}", file=sys.stderr)
        time.sleep(3)

def handle_client(conn):
    buf = b""; idle = 0
    while idle < 10:
        try:
            r, _, _ = select.select([conn], [], [], 1.0)
            if not r: idle += 1; continue
            data = conn.recv(65536)
            if not data: break
            idle = 0; buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip(): continue
                try:
                    payload = json.loads(line.decode("utf-8"))
                    with lock:
                        global active_order
                        ctl = payload.get("_control", {})
                        if isinstance(ctl, dict) and "pages" in ctl:
                            active_order = normalize_page_order(ctl.get("pages"))
                        for key in RENDERERS:
                            if key in payload and payload[key] is not None:
                                state[key] = payload[key]
                except Exception as e:
                    print(f"Parse: {e}", file=sys.stderr)
        except: break
    conn.close()

DISCOVERY_PORT = 9878

def discovery_broadcaster(tcp_port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    msg = json.dumps({"type": "sidemon", "port": tcp_port}).encode("utf-8")
    while True:
        try: s.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
        except: pass
        time.sleep(5)

def page_cycler(fb_dev, cycle_secs):
    page = 0
    while True:
        with lock:
            cur = dict(state); order = list(active_order)
        if not order: order = list(DEFAULT_ORDER)
        if page >= len(order): page = 0
        key = order[page]
        if key in cur:
            try:
                img = RENDERERS[key](cur[key])
                write_fb(fb_dev, img)
            except Exception as e:
                print(f"Render({key}): {e}", file=sys.stderr)
        page = (page + 1) % len(order)
        time.sleep(cycle_secs)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", "-p", type=int, default=9877)
    p.add_argument("--fb", "-f", default="/dev/fb0")
    p.add_argument("--cycle", "-c", type=int, default=15)
    args = p.parse_args()
    show_waiting(args.fb)
    threading.Thread(target=discovery_broadcaster, args=(args.port,), daemon=True).start()
    threading.Thread(target=waiting_updater, args=(args.fb,), daemon=True).start()
    threading.Thread(target=page_cycler, args=(args.fb, args.cycle), daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(5)
    print(f"SideMon PIL :{args.port} -> {args.fb}  {args.cycle}s x 7", file=sys.stderr)
    while True:
        conn, addr = srv.accept()
        print(f"Connected: {addr[0]}", file=sys.stderr)
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
