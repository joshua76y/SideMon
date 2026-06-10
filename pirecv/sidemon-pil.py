#!/usr/bin/env python3
"""SideMon PIL receiver — premium dashboard for 480x320 SPI display"""
import socket, json, threading, time, os, sys, argparse, select, calendar
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone, timedelta

W, H = 480, 320
FD = "/usr/share/fonts/truetype"

def ff(name, size):
    paths = {
        "r": [f"{FD}/piboto/Piboto-Regular.ttf", f"{FD}/dejavu/DejaVuSans.ttf"],
        "b": [f"{FD}/piboto/Piboto-Bold.ttf", f"{FD}/dejavu/DejaVuSans-Bold.ttf"],
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
    d.rectangle((0, 0, W, 34), fill=hdr_bg)
    d.rectangle((0, 0, 4, 34), fill=accent)
    d.text((14, 7), title, fill=accent, font=F["b18"])

def nav(d, cur, total, accent):
    d.rectangle((0, H-16, W, H), fill=PN)
    d.line((0, H-16, W, H-16), fill=DIVIDER, width=1)
    dot_r = 3; spacing = 14
    total_w = total * spacing
    sx = (W - total_w) // 2
    for i in range(total):
        cx = sx + i * spacing + dot_r
        cy_ = H - 9
        if i == cur:
            d.ellipse((cx-dot_r, cy_-dot_r, cx+dot_r, cy_+dot_r), fill=accent)
        else:
            d.ellipse((cx-dot_r, cy_-dot_r, cx+dot_r, cy_+dot_r), fill=DIVIDER)

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

TOTAL_PAGES = 7

# ══════════════════════════════════════════════════════════════════════
# Page 1: System — big thick gauges (2x size, 2x thickness)
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    bg = PAGE_BG["sys"]
    img = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(img)
    ac = AC["sys"]
    hdr(d, "SYSTEM", ac, bg)

    # Three big arc gauges — 2x radius, 2x width
    gy = 110
    positions = [82, 240, 398]
    gauges = [
        (s.get("cpu",0)/100,  AC["sys"],  "CPU",  f"{int(s.get('cpu',0))}%", 56, 22),
        (s.get("mem",0)/100,  AC["api"],  "MEM",  f"{int(s.get('mem',0))}%", 48, 18),
        (s.get("disk",0)/100, AC["clash"], "DISK", f"{int(s.get('disk',0))}%", 42, 16),
    ]
    for i, (pct, color, label, val, radius, w_) in enumerate(gauges):
        x = positions[i]
        p = min(pct, 1)
        arc_g(d, x, gy, radius, p, color, width=w_)
        # Value centered inside ring
        tw = d.textlength(val, font=F["b24"])
        d.text((x - tw//2, gy - 14), val, fill=TX, font=F["b24"])
        # Label below ring
        tw2 = d.textlength(label, font=F["r12"])
        d.text((x - tw2//2, gy + radius + 8), label, fill=color, font=F["r12"])

    # Bottom info row — 3 cards
    by = 220
    cw = (W - 30) // 3
    card(d, 10, by, cw, 52)
    d.text((18, by+6), "UPTIME", fill=TX3, font=F["r10"])
    d.text((18, by+24), s.get("uptime","?"), fill=ac, font=F["b18"])

    card(d, 20+cw, by, cw, 52)
    ld = s.get("load",[0,0,0])
    d.text((28+cw, by+6), "LOAD", fill=TX3, font=F["r10"])
    d.text((28+cw, by+24), f"{ld[0]:.1f}  {ld[1]:.1f}", fill=TX2, font=F["b18"])

    card(d, 30+2*cw, by, cw, 52)
    hn = s.get("hostname","?")
    if len(hn) > 14: hn = hn[:12]+".."
    d.text((38+2*cw, by+6), "HOST", fill=TX3, font=F["r10"])
    d.text((38+2*cw, by+24), hn, fill=TX2, font=F["b14"])

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
    hdr(d, "API USAGE", ac, bg)

    y0 = 44
    for i, (label, key, color) in enumerate([
        ("DeepSeek", "ds_balance", AC["api"]),
        ("MiMo", "mm_balance", AC["codex"]),
    ]):
        by = y0 + i * 68
        card(d, 10, by, W-20, 54)
        d.text((20, by+6), label, fill=color, font=F["b14"])
        bal = api.get(key, "?")
        try: val = float(bal)
        except: val = 0
        pct = min(val / 50.0, 1.0) if val > 0 else 0
        bar(d, 20, by+28, W-180, 12, pct, color, radius=6)
        vs = f"CNY {val:.1f}" if isinstance(val, float) else str(val)
        d.text((W-140, by+22), vs, fill=TX, font=F["b16"])

    y1 = 188
    cw = (W - 40) // 3
    items = [
        ("TOTAL", fmt_tk(api.get("total_tokens",0)), TX),
        ("OUTPUT", fmt_tk(api.get("output_tokens",0)), AC["sys"]),
        ("CACHE", f"{api.get('cache_hit_rate',0):.1f}%", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(items):
        x = 10 + i * (cw + 10)
        card(d, x, y1, cw, 60)
        d.text((x+10, y1+8), lbl, fill=TX3, font=F["r10"])
        d.text((x+10, y1+28), val, fill=col, font=F["b22"])

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
    hdr(d, "CLASH VERGE", ac, bg)

    # Status badge
    st = "ONLINE" if cl.get("running") else "OFFLINE"
    st_col = AC["sys"] if cl.get("running") else (220, 80, 80)
    badge_w = d.textlength(st, font=F["b11"]) + 16
    rr(d, (W-14-badge_w, 8, W-14, 26), 4, st_col)
    d.text((W-14-badge_w+8, 9), st, fill=BG, font=F["b11"])

    # Current node
    node = cl.get("current_node", "?")
    if len(node) > 26: node = node[:24]+".."
    card(d, 10, 40, W-20, 38)
    d.text((20, 44), "NODE", fill=TX3, font=F["r10"])
    d.text((70, 42), node, fill=TX, font=F["b16"])

    # Traffic bar
    y0 = 84
    card(d, 10, y0, W-20, 40)
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    d.text((20, y0+4), "TRAFFIC", fill=TX3, font=F["r10"])
    if tu and tt:
        d.text((100, y0+4), f"{tu} / {tt}", fill=TX, font=F["b14"])
        try:
            num = float(''.join(c for c in tu.split()[0] if c.isdigit() or c=='.'))
            den = float(''.join(c for c in tt.split()[0] if c.isdigit() or c=='.'))
            tp = num/den if den > 0 else 0
        except: tp = 0
        bar(d, 20, y0+24, W-40, 10, tp, ac, radius=5)
    else:
        d.text((100, y0+6), "N/A", fill=TX3, font=F["r14"])

    # Upload + Download
    y1 = 132
    hw = (W-30)//2
    card(d, 10, y1, hw, 40)
    d.text((20, y1+4), "UPLOAD", fill=AC["api"], font=F["r10"])
    d.text((20, y1+20), fmtb(cl.get("upload_total",0)), fill=AC["api"], font=F["b16"])

    card(d, 20+hw, y1, hw, 40)
    d.text((30+hw, y1+4), "DOWNLOAD", fill=ac, font=F["r10"])
    d.text((30+hw, y1+20), fmtb(cl.get("download_total",0)), fill=ac, font=F["b16"])

    # Expire + Mode + Connections
    y2 = 180
    cw3 = (W-40)//3
    card(d, 10, y2, cw3, 40)
    d.text((18, y2+4), "EXPIRE", fill=TX3, font=F["r10"])
    exp = cl.get("expire_date", "") or "N/A"
    if len(exp) > 14: exp = exp[:12]+".."
    d.text((18, y2+20), exp, fill=AC["wthr"], font=F["b13"])

    card(d, 20+cw3, y2, cw3, 40)
    d.text((28+cw3, y2+4), "MODE", fill=TX3, font=F["r10"])
    d.text((28+cw3, y2+20), cl.get("mode","Rule"), fill=AC["codex"], font=F["b13"])

    card(d, 30+2*cw3, y2, cw3, 40)
    d.text((38+2*cw3, y2+4), "CONNS", fill=TX3, font=F["r10"])
    d.text((38+2*cw3, y2+20), str(cl.get("active_connections",0)), fill=TX, font=F["b16"])

    # Updated time
    y3 = 228
    upd = cl.get("update_time", "")
    if upd:
        card(d, 10, y3, W-20, 28)
        d.text((20, y3+6), f"Updated: {upd}", fill=TX3, font=F["r10"])

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
    hdr(d, "CODEX", ac, bg)

    # Two large percentage arc gauges — color changes by pct
    gy = 112
    for i, (label, key, mx) in enumerate([
        ("5 Hour", "tokens_5h", 2_000_000),
        ("7 Day", "tokens_7d", 10_000_000),
    ]):
        x = 130 + i * 220
        tok = cx.get(key, 0)
        pct = min(tok/mx, 1.0) if tok > 0 else 0
        pct_int = int(pct * 100)
        color = pct_color(pct)
        arc_g(d, x, gy, 50, pct, color, width=14)
        # Large percentage in center
        vs = f"{pct_int}%"
        tw = d.textlength(vs, font=F["b36"])
        d.text((x-tw//2, gy-22), vs, fill=color, font=F["b36"])
        # Token count
        ts = fmt_tk(tok)
        tw2 = d.textlength(ts, font=F["r10"])
        d.text((x-tw2//2, gy+22), ts, fill=TX3, font=F["r10"])
        # Label
        tw3 = d.textlength(label, font=F["r13"])
        d.text((x-tw3//2, gy+56), label, fill=TX2, font=F["r13"])

    # Model + Reset
    y0 = 196
    card(d, 10, y0, W-20, 34)
    d.text((20, y0+8), "MODEL", fill=TX3, font=F["r10"])
    model = cx.get("model", "?")
    if len(model) > 28: model = model[:26]+".."
    d.text((80, y0+8), model, fill=ac, font=F["b14"])

    y1 = 238
    card(d, 10, y1, W-20, 30)
    d.text((20, y1+6), "RESET", fill=TX3, font=F["r10"])
    d.text((80, y1+5), cx.get("reset_time","?"), fill=AC["wthr"], font=F["r12"])

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
    hdr(d, "WEATHER", ac, bg)

    hw = (W-30)//2

    temp_c = w.get("temp_c", 0)
    card(d, 10, 40, hw, 68)
    d.text((20, 44), "TEMP", fill=TX3, font=F["r10"])
    d.text((20, 60), f"{int(temp_c)}°C", fill=TX, font=F["b30"])
    d.text((20, 96), w.get("city","?"), fill=TX3, font=F["r10"])

    cond = w.get("condition", "?")
    if len(cond) > 16: cond = cond[:14]+".."
    card(d, 20+hw, 40, hw, 68)
    d.text((30+hw, 44), "CONDITION", fill=TX3, font=F["r10"])
    d.text((30+hw, 64), cond, fill=TX, font=F["b16"])
    fl = w.get("feels_like_c", temp_c)
    d.text((30+hw, 88), f"Feels {int(fl)}°C", fill=TX3, font=F["r10"])

    y0 = 116
    card(d, 10, y0, hw, 38)
    d.text((20, y0+4), "HUMIDITY", fill=TX3, font=F["r10"])
    d.text((20, y0+18), f"{w.get('humidity',0)}%", fill=AC["api"], font=F["b16"])

    card(d, 20+hw, y0, hw, 38)
    d.text((30+hw, y0+4), "WIND", fill=TX3, font=F["r10"])
    d.text((30+hw, y0+18), f"{w.get('wind_kph',0)} km/h", fill=TX2, font=F["b16"])

    y1 = 162
    forecasts = w.get("forecast", [])[:3]
    fw = (W-40)//3
    for i, fc in enumerate(forecasts):
        fx = 10 + i*(fw+5)
        card(d, fx, y1, fw, 54)
        day = fc.get("day","?")[:3]
        d.text((fx+8, y1+4), day, fill=TX3, font=F["r10"])
        hi = fc.get("high_c","?")
        lo = fc.get("low_c","?")
        d.text((fx+8, y1+18), f"{hi}°", fill=AC["clash"], font=F["b16"])
        d.text((fx+56, y1+18), f"{lo}°", fill=AC["api"], font=F["b16"])
        c2 = fc.get("condition","?")
        if len(c2) > 12: c2 = c2[:10]+".."
        d.text((fx+8, y1+38), c2, fill=TX2, font=F["r9"])

    y2 = 224
    card(d, 10, y2, hw, 32)
    d.text((20, y2+8), "SUN", fill=TX3, font=F["r10"])
    d.text((56, y2+6), f"{w.get('sunrise','?')}", fill=ac, font=F["r12"])
    d.text((140, y2+6), f"{w.get('sunset','?')}", fill=ac, font=F["r12"])

    card(d, 20+hw, y2, hw, 32)
    d.text((30+hw, y2+8), "UV", fill=TX3, font=F["r10"])
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
    hdr(d, "DATETIME", ac, bg)

    ts = dt_data.get("timestamp", 0)
    if ts > 0:
        utc_now = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_tz = timezone(timedelta(hours=8))
        now = utc_now.astimezone(local_tz)
    else:
        now = datetime.now()

    # Time card (left) — big clock
    card(d, 10, 40, 200, 76, fill=(20, 26, 36))
    time_str = now.strftime("%H:%M")
    tw = d.textlength(time_str, font=F["b40"])
    d.text((10+(200-tw)//2, 44), time_str, fill=ac, font=F["b40"])
    sec_str = now.strftime("%S")
    d.text((10+(200-d.textlength(sec_str,font=F["b18"]))//2, 90), sec_str, fill=(140, 180, 210), font=F["b18"])

    # Date card (right)
    card(d, 220, 40, 250, 76, fill=(20, 26, 36))
    date_str = now.strftime("%Y-%m-%d")
    d.text((232, 46), date_str, fill=TX, font=F["b18"])
    dow = now.strftime("%A")
    d.text((232, 68), dow, fill=AC["wthr"], font=F["b14"])
    tz = now.strftime("UTC%z")
    d.text((232, 88), tz, fill=(80, 130, 180), font=F["r11"])

    # Calendar — colorful
    y0 = 128
    card(d, 10, y0, W-20, 152, fill=(16, 22, 32))
    month_str = now.strftime("%B %Y")
    tw = d.textlength(month_str, font=F["b13"])
    d.text(((W-tw)//2, y0+4), month_str, fill=ac, font=F["b13"])

    # Day headers — different colors for each
    day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    day_colors = [
        (168, 172, 188), (168, 172, 188), (168, 172, 188),
        (168, 172, 188), (168, 172, 188), AC["api"], AC["clash"],
    ]
    col_w = (W-40) // 7
    for i, dn in enumerate(day_names):
        dx = 14 + i*col_w
        d.text((dx, y0+20), dn, fill=day_colors[i], font=F["b10"])

    cal = calendar.monthcalendar(now.year, now.month)
    today_day = now.day
    for wi, week in enumerate(cal):
        for di, day in enumerate(week):
            if day == 0: continue
            dx = 14 + di*col_w
            dy = y0 + 36 + wi*16
            if day == today_day:
                rr(d, (dx-2, dy-1, dx+col_w-6, dy+13), 3, ac)
                d.text((dx, dy), f"{day:2d}", fill=BG, font=F["b10"])
            else:
                # Weekends in warm colors, weekdays in cool
                if di >= 5:
                    col = (255, 170, 80) if di == 5 else (255, 120, 80)
                else:
                    col = (140, 200, 240)
                d.text((dx, dy), f"{day:2d}", fill=col, font=F["r10"])

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
    hdr(d, "OMLX", ac, bg)

    st = "ONLINE" if om.get("running") else "OFFLINE"
    st_col = AC["sys"] if om.get("running") else (220, 80, 80)
    badge_w = d.textlength(st, font=F["b11"]) + 16
    rr(d, (W-14-badge_w, 8, W-14, 26), 4, st_col)
    d.text((W-14-badge_w+8, 9), st, fill=BG, font=F["b11"])

    # Memory bar
    card(d, 10, 40, W-20, 48)
    mem_u = om.get("memory_used", 0)
    mem_c = om.get("memory_ceiling", 1)
    mem_p = mem_u/mem_c if mem_c > 0 else 0
    d.text((20, 44), "MEMORY", fill=TX3, font=F["r10"])
    d.text((110, 44), f"{mem_u:.1f} / {mem_c:.1f} GB", fill=TX2, font=F["b14"])
    bar(d, 20, 66, W-40, 10, mem_p, ac, radius=5)

    # 4 stat boxes (2x2) — bigger
    y0 = 98
    hw = (W-30)//2
    stats = [
        ("MODELS", f"{om.get('loaded_count',0)} / {om.get('model_count',0)}", ac),
        ("REQUESTS", fmt_tk(om.get("total_requests",0)), TX),
        ("PROMPT SPEED", f"{om.get('avg_prompt_speed',0):.1f} tk/s", AC["api"]),
        ("GEN SPEED", f"{om.get('avg_gen_speed',0):.1f} tk/s", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(stats):
        x = 10 + (i%2)*(hw+10)
        y = y0 + (i//2)*52
        card(d, x, y, hw, 46)
        d.text((x+10, y+6), lbl, fill=TX3, font=F["r10"])
        d.text((x+10, y+24), val, fill=col, font=F["b18"])

    # Cache efficiency
    y1 = 208
    ce = om.get("cache_efficiency", 0)*100
    card(d, 10, y1, W-20, 52)
    d.text((20, y1+6), "CACHE EFFICIENCY", fill=TX3, font=F["r10"])
    d.text((20, y1+26), f"{ce:.1f}%", fill=ac, font=F["b24"])
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
    return f"IP: {', '.join(ips)}" if ips else "Waiting for IP..."

def write_fb(path, img):
    img = img.rotate(180, expand=False)
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "BGRA"))

def show_waiting(fb_dev):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 34), fill=PN)
    d.rectangle((0, 0, 4, 34), fill=AC["sys"])
    d.text((14, 7), "SIDEMON", fill=AC["sys"], font=F["b18"])
    ct(d, H//2-40, "Waiting...", TX3, "b20")
    ct(d, H//2, waiting_ip_text(), TX, "b18")
    ct(d, H//2+24, "Mac -> Pi", TX3, "r12")
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
