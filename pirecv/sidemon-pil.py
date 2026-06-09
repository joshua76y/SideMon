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
for s in [10,11,12,13,14,16,18,20,22,24,26,28,30,32,36,40,48]:
    F["r"+str(s)] = ff("r", s)
    F["b"+str(s)] = ff("b", s)

# ══════════════════════════════════════════════════════════════════════
# Design System
# ══════════════════════════════════════════════════════════════════════

# Base palette — dark charcoal with warm undertone
BG = (14, 16, 22)
PN = (22, 24, 32)
CARD = (28, 30, 40)
CARD_B = (34, 36, 48)
BORDER = (48, 50, 62)
DIVIDER = (42, 44, 56)

# Text hierarchy
TX = (230, 232, 240)      # primary — bright white
TX2 = (160, 164, 180)     # secondary — mid gray
TX3 = (100, 104, 120)     # tertiary — dim

# Page accent colors (muted, sophisticated)
AC = {
    "sys":   (100, 190, 155),   # sage green
    "api":   (90, 170, 230),    # steel blue
    "clash": (220, 140, 70),    # amber
    "codex": (165, 120, 240),   # lavender
    "wthr":  (235, 185, 80),    # warm gold
    "dt":    (120, 180, 240),   # sky blue
    "omlx":  (110, 200, 120),   # emerald
}

# Accent card backgrounds (very desaturated version of accent)
AC_BG = {k: (v[0]//10+14, v[1]//10+14, v[2]//10+18) for k, v in AC.items()}

state = {}; lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════
# Drawing Primitives
# ══════════════════════════════════════════════════════════════════════

def rr(d, xy, r, fill):
    """Rounded rectangle."""
    d.rounded_rectangle(xy, radius=r, fill=fill)

def card(d, x, y, w, h, fill=None):
    """Card with subtle border."""
    f = fill or CARD
    rr(d, (x, y, x+w, y+h), 8, f)
    # top highlight line
    d.line((x+8, y, x+w-8, y), fill=(f[0]+12, f[1]+12, f[2]+12), width=1)

def arc_g(d, cx, cy, r, pct, fg, bg=None):
    """Clean arc gauge — thin ring style."""
    if bg is None: bg = BORDER
    d.ellipse((cx-r, cy-r, cx+r, cy+r), outline=bg, width=6)
    if pct > 0.005:
        s, e = 225, 225 - 360*min(pct, 1)
        if e < 0: e += 360
        d.arc((cx-r, cy-r, cx+r, cy+r), s, e, fill=fg, width=6)

def bar(d, x, y, w, h, pct, fg):
    """Progress bar with rounded ends."""
    rr(d, (x, y, x+w, y+h), h//2, BORDER)
    fw = max(4, int(w * min(pct, 1)))
    if fw > h:
        rr(d, (x, y, x+fw, y+h), h//2, fg)

def ct(d, y, text, fill, fk):
    """Center text."""
    tw = d.textlength(text, font=F[fk])
    d.text(((W-tw)//2, y), text, fill=fill, font=F[fk])

def hdr(d, title, accent):
    """Page header — accent line on left + title."""
    d.rectangle((0, 0, W, 36), fill=PN)
    d.rectangle((0, 0, 3, 36), fill=accent)
    d.text((14, 8), title, fill=accent, font=F["b18"])

def nav(d, cur, total):
    """Bottom navigation — thin line + page numbers."""
    d.rectangle((0, H-18, W, H), fill=PN)
    d.line((0, H-18, W, H-18), fill=DIVIDER, width=1)
    # page numbers centered
    nums = "  ".join([str(i+1) for i in range(total)])
    tw = d.textlength(nums, font=F["r10"])
    sx = (W - tw) // 2
    x = sx
    for i in range(total):
        ch = str(i+1)
        cw = d.textlength(ch, font=F["r10"])
        col = AC["sys"] if i == cur else TX3  # accent color for current
        d.text((x, H-14), ch, fill=col, font=F["r10"])
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

TOTAL_PAGES = 7

# ══════════════════════════════════════════════════════════════════════
# Page 1: System
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["sys"]
    hdr(d, "SYSTEM", ac)

    # Three arc gauges — centered, well-spaced
    gy = 110
    positions = [80, 240, 400]
    gauges = [
        (s.get("cpu",0)/100,  AC["sys"],  "CPU",  f"{int(s.get('cpu',0))}%"),
        (s.get("mem",0)/100,  AC["api"],  "MEM",  f"{int(s.get('mem',0))}%"),
        (s.get("disk",0)/100, AC["clash"], "DISK", f"{int(s.get('disk',0))}%"),
    ]
    for i, (pct, color, label, val) in enumerate(gauges):
        x = positions[i]
        arc_g(d, x, gy, 38, min(pct, 1), color)
        # value centered in arc
        tw = d.textlength(val, font=F["b20"])
        d.text((x - tw//2, gy - 12), val, fill=TX, font=F["b20"])
        # label below
        tw2 = d.textlength(label, font=F["r11"])
        d.text((x - tw2//2, gy + 44), label, fill=TX3, font=F["r11"])

    # Bottom info row
    by = 180
    # Uptime
    card(d, 10, by, 148, 54)
    d.text((20, by+6), "UPTIME", fill=TX3, font=F["r10"])
    d.text((20, by+24), s.get("uptime","?"), fill=ac, font=F["b20"])

    # Load
    card(d, 168, by, 148, 54)
    d.text((178, by+6), "LOAD", fill=TX3, font=F["r10"])
    ld = s.get("load",[0,0,0])
    d.text((178, by+24), f"{ld[0]:.1f}  {ld[1]:.1f}", fill=TX2, font=F["b18"])

    # Hostname
    card(d, 326, by, 144, 54)
    d.text((336, by+6), "HOST", fill=TX3, font=F["r10"])
    hn = s.get("hostname","?")
    if len(hn) > 16: hn = hn[:14]+".."
    d.text((336, by+24), hn, fill=TX2, font=F["b14"])

    nav(d, 0, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 2: API Usage — horizontal bars
# ══════════════════════════════════════════════════════════════════════

def pg_apis(api):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["api"]
    hdr(d, "API USAGE", ac)

    # Two balance bars
    y0 = 48
    for i, (label, key, color) in enumerate([
        ("DeepSeek", "ds_balance", AC["api"]),
        ("MiMo", "mm_balance", AC["codex"]),
    ]):
        by = y0 + i * 70
        card(d, 10, by, W-20, 58)
        d.text((20, by+6), label, fill=color, font=F["b14"])
        bal = api.get(key, "?")
        try: val = float(bal)
        except: val = 0
        pct = min(val / 50.0, 1.0) if val > 0 else 0
        bar(d, 20, by+28, W-180, 12, pct, color)
        vs = f"{val:.1f}" if isinstance(val, float) else str(val)
        d.text((W-140, by+22), f"CNY {vs}", fill=TX, font=F["b16"])

    # 3 stat cards
    y1 = 192
    cw = (W - 40) // 3
    items = [
        ("TOTAL", fmt_tk(api.get("total_tokens",0)), TX),
        ("OUTPUT", fmt_tk(api.get("output_tokens",0)), AC["sys"]),
        ("CACHE", f"{api.get('cache_hit_rate',0):.1f}%", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(items):
        x = 10 + i * (cw + 10)
        card(d, x, y1, cw, 64)
        d.text((x+10, y1+10), lbl, fill=TX3, font=F["r10"])
        d.text((x+10, y1+32), val, fill=col, font=F["b22"])

    nav(d, 1, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 3: Clash
# ══════════════════════════════════════════════════════════════════════

def pg_clash(cl):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["clash"]
    hdr(d, "CLASH", ac)

    st = "ONLINE" if cl.get("running") else "OFFLINE"
    st_col = AC["sys"] if cl.get("running") else AC["clash"]
    tw_st = d.textlength(st, font=F["r11"])
    d.text((W-14-tw_st, 12), st, fill=st_col, font=F["r11"])

    # Node card
    node = cl.get("current_node", "?")
    if len(node) > 28: node = node[:26]+".."
    card(d, 10, 44, W-20, 44)
    d.text((20, 48), "NODE", fill=TX3, font=F["r10"])
    d.text((20, 64), node, fill=TX, font=F["b16"])

    # Traffic
    y0 = 98
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    card(d, 10, y0, W-20, 44)
    d.text((20, y0+6), "TRAFFIC", fill=TX3, font=F["r10"])
    if tu and tt:
        d.text((20, y0+24), f"{tu} / {tt}", fill=TX, font=F["b16"])
        try:
            num = float(''.join(c for c in tu.split()[0] if c.isdigit() or c=='.'))
            den = float(''.join(c for c in tt.split()[0] if c.isdigit() or c=='.'))
            tp = num/den if den > 0 else 0
        except: tp = 0
        bar(d, W-180, y0+26, 150, 8, tp, ac)
    else:
        d.text((20, y0+24), "N/A", fill=TX3, font=F["r14"])

    # Upload + Download
    y1 = 152
    hw = (W-30)//2
    card(d, 10, y1, hw, 48)
    d.text((20, y1+6), "UPLOAD", fill=TX3, font=F["r10"])
    d.text((20, y1+24), fmtb(cl.get("upload_total",0)), fill=AC["api"], font=F["b18"])

    card(d, 20+hw, y1, hw, 48)
    d.text((30+hw, y1+6), "DOWNLOAD", fill=TX3, font=F["r10"])
    d.text((30+hw, y1+24), fmtb(cl.get("download_total",0)), fill=AC["clash"], font=F["b18"])

    # Expire + Mode
    y2 = 210
    card(d, 10, y2, hw, 42)
    d.text((20, y2+6), "EXPIRE", fill=TX3, font=F["r10"])
    exp = cl.get("expire_date", "") or "N/A"
    d.text((20, y2+22), exp, fill=AC["wthr"], font=F["b14"])

    card(d, 20+hw, y2, hw, 42)
    d.text((30+hw, y2+6), "MODE", fill=TX3, font=F["r10"])
    d.text((30+hw, y2+22), cl.get("mode","Rule"), fill=AC["codex"], font=F["b14"])

    nav(d, 2, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 4: Codex — percentage gauges
# ══════════════════════════════════════════════════════════════════════

def pg_codex(cx):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["codex"]
    hdr(d, "CODEX", ac)

    # Two large percentage arc gauges
    gy = 115
    for i, (label, key, mx, color) in enumerate([
        ("5 Hour", "tokens_5h", 2_000_000, AC["codex"]),
        ("7 Day", "tokens_7d", 10_000_000, AC["api"]),
    ]):
        x = 130 + i * 220
        tok = cx.get(key, 0)
        pct = min(tok/mx, 1.0) if tok > 0 else 0
        pct_int = int(pct * 100)
        arc_g(d, x, gy, 46, pct, color)
        # Large percentage
        vs = f"{pct_int}%"
        tw = d.textlength(vs, font=F["b32"])
        d.text((x-tw//2, gy-20), vs, fill=color, font=F["b32"])
        # Token count small
        ts = fmt_tk(tok)
        tw2 = d.textlength(ts, font=F["r10"])
        d.text((x-tw2//2, gy+18), ts, fill=TX3, font=F["r10"])
        # Label
        tw3 = d.textlength(label, font=F["r12"])
        d.text((x-tw3//2, gy+50), label, fill=TX2, font=F["r12"])

    # Model + Reset
    y0 = 196
    card(d, 10, y0, W-20, 36)
    d.text((20, y0+8), "MODEL", fill=TX3, font=F["r10"])
    model = cx.get("model", "?")
    if len(model) > 28: model = model[:26]+".."
    d.text((80, y0+8), model, fill=ac, font=F["b14"])

    y1 = 240
    card(d, 10, y1, W-20, 30)
    d.text((20, y1+5), "RESET", fill=TX3, font=F["r10"])
    d.text((80, y1+4), cx.get("reset_time","?"), fill=AC["wthr"], font=F["r12"])

    nav(d, 3, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 5: Weather
# ══════════════════════════════════════════════════════════════════════

def pg_weather(w):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["wthr"]
    hdr(d, "WEATHER", ac)

    hw = (W-30)//2

    # Temperature (left) + Condition (right)
    temp_c = w.get("temp_c", 0)
    tc = AC["wthr"] if temp_c < 30 else AC["clash"]
    card(d, 10, 44, hw, 70)
    d.text((20, 48), "TEMP", fill=TX3, font=F["r10"])
    d.text((20, 66), f"{int(temp_c)}°C", fill=TX, font=F["b30"])
    d.text((20, 100), w.get("city","?"), fill=TX3, font=F["r10"])

    cond = w.get("condition", "?")
    if len(cond) > 16: cond = cond[:14]+".."
    card(d, 20+hw, 44, hw, 70)
    d.text((30+hw, 48), "CONDITION", fill=TX3, font=F["r10"])
    d.text((30+hw, 66), cond, fill=TX, font=F["b16"])
    fl = w.get("feels_like_c", temp_c)
    d.text((30+hw, 90), f"Feels {int(fl)}°C", fill=TX3, font=F["r10"])

    # Humidity + Wind
    y0 = 124
    card(d, 10, y0, hw, 40)
    d.text((20, y0+4), "HUMIDITY", fill=TX3, font=F["r10"])
    d.text((20, y0+20), f"{w.get('humidity',0)}%", fill=AC["api"], font=F["b16"])

    card(d, 20+hw, y0, hw, 40)
    d.text((30+hw, y0+4), "WIND", fill=TX3, font=F["r10"])
    d.text((30+hw, y0+20), f"{w.get('wind_kph',0)} km/h", fill=TX2, font=F["b16"])

    # 3-day forecast
    y1 = 174
    forecasts = w.get("forecast", [])[:3]
    fw = (W-40)//3
    for i, fc in enumerate(forecasts):
        fx = 10 + i*(fw+5)
        card(d, fx, y1, fw, 58)
        day = fc.get("day","?")[:3]
        d.text((fx+8, y1+4), day, fill=TX3, font=F["r10"])
        hi = fc.get("high_c","?")
        lo = fc.get("low_c","?")
        d.text((fx+8, y1+22), f"{hi}°", fill=AC["clash"], font=F["b16"])
        d.text((fx+56, y1+22), f"{lo}°", fill=AC["api"], font=F["b16"])
        c2 = fc.get("condition","?")
        if len(c2) > 12: c2 = c2[:10]+".."
        d.text((fx+8, y1+42), c2, fill=TX2, font=F["r9"])

    # Sunrise + UV
    y2 = 242
    card(d, 10, y2, hw, 32)
    d.text((20, y2+8), "SUN  ", fill=TX3, font=F["r10"])
    d.text((60, y2+6), f"{w.get('sunrise','?')}", fill=ac, font=F["r12"])
    d.text((140, y2+6), f"{w.get('sunset','?')}", fill=ac, font=F["r12"])

    card(d, 20+hw, y2, hw, 32)
    d.text((30+hw, y2+8), "UV", fill=TX3, font=F["r10"])
    d.text((60+hw, y2+5), str(w.get("uv_index","?")), fill=AC["clash"], font=F["b16"])

    nav(d, 4, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 6: DateTime
# ══════════════════════════════════════════════════════════════════════

def pg_datetime(dt_data):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["dt"]
    hdr(d, "DATETIME", ac)

    ts = dt_data.get("timestamp", 0)
    if ts > 0:
        utc_now = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_tz = timezone(timedelta(hours=8))
        now = utc_now.astimezone(local_tz)
    else:
        now = datetime.now()

    # Time (left) + Date info (right)
    card(d, 10, 44, 200, 84)
    time_str = now.strftime("%H:%M")
    tw = d.textlength(time_str, font=F["b48"])
    d.text((10+(200-tw)//2, 48), time_str, fill=TX, font=F["b48"])
    # Seconds
    sec_str = now.strftime("%S")
    d.text((10+(200-d.textlength(sec_str,font=F["b22"]))//2, 100), sec_str, fill=TX3, font=F["b22"])

    card(d, 220, 44, 250, 84)
    date_str = now.strftime("%Y-%m-%d")
    d.text((230, 50), date_str, fill=TX2, font=F["b18"])
    dow = now.strftime("%A")
    d.text((230, 74), dow, fill=ac, font=F["r14"])
    tz = now.strftime("UTC%z")
    d.text((230, 96), tz, fill=TX3, font=F["r11"])

    # Calendar
    y0 = 140
    card(d, 10, y0, W-20, 140)
    month_str = now.strftime("%B %Y")
    tw = d.textlength(month_str, font=F["b12"])
    d.text(((W-tw)//2, y0+4), month_str, fill=ac, font=F["b12"])

    day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    col_w = (W-40) // 7
    for i, dn in enumerate(day_names):
        dx = 14 + i*col_w
        color = AC["api"] if i >= 5 else TX3
        d.text((dx, y0+20), dn, fill=color, font=F["r10"])

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
                col = AC["api"] if di >= 5 else TX2
                d.text((dx, dy), f"{day:2d}", fill=col, font=F["r10"])

    nav(d, 5, TOTAL_PAGES)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page 7: omLX
# ══════════════════════════════════════════════════════════════════════

def pg_omlx(om):
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    ac = AC["omlx"]
    hdr(d, "OMLX", ac)

    st = "ONLINE" if om.get("running") else "OFFLINE"
    st_col = AC["sys"] if om.get("running") else AC["clash"]
    tw_st = d.textlength(st, font=F["r11"])
    d.text((W-14-tw_st, 12), st, fill=st_col, font=F["r11"])

    # Memory bar
    card(d, 10, 44, W-20, 48)
    mem_u = om.get("memory_used", 0)
    mem_c = om.get("memory_ceiling", 1)
    mem_p = mem_u/mem_c if mem_c > 0 else 0
    d.text((20, 48), "MEMORY", fill=TX3, font=F["r10"])
    d.text((110, 48), f"{mem_u:.1f} / {mem_c:.1f} GB", fill=TX2, font=F["b14"])
    bar(d, 20, 70, W-40, 10, mem_p, ac)

    # 4 stat boxes (2x2)
    y0 = 102
    hw = (W-30)//2
    stats = [
        ("MODELS", f"{om.get('loaded_count',0)} / {om.get('model_count',0)}", ac),
        ("REQUESTS", fmt_tk(om.get("total_requests",0)), TX),
        ("PROMPT", f"{om.get('avg_prompt_speed',0):.1f} tk/s", AC["api"]),
        ("GEN", f"{om.get('avg_gen_speed',0):.1f} tk/s", AC["wthr"]),
    ]
    for i, (lbl, val, col) in enumerate(stats):
        x = 10 + (i%2)*(hw+10)
        y = y0 + (i//2)*44
        card(d, x, y, hw, 38)
        d.text((x+10, y+4), lbl, fill=TX3, font=F["r10"])
        d.text((x+10, y+20), val, fill=col, font=F["b14"])

    # Cache bar
    y1 = 194
    ce = om.get("cache_efficiency", 0)*100
    card(d, 10, y1, W-20, 28)
    d.text((20, y1+6), "CACHE", fill=TX3, font=F["r10"])
    bar(d, 74, y1+8, W-140, 10, ce/100, ac)
    d.text((W-58, y1+5), f"{ce:.0f}%", fill=ac, font=F["r10"])

    # Top 3 models
    y2 = 230
    top = om.get("top_models", [])[:3]
    if top:
        max_tk = max((m.get("tk_total",1) for m in top), default=1)
        colors = [ac, AC["api"], AC["wthr"]]
        for i, m in enumerate(top):
            by = y2 + i*20
            tk = m.get("tk_total",0)
            pct = tk/max_tk if max_tk>0 else 0
            nm = m.get("name","?")
            if len(nm) > 22: nm = nm[:20]+".."
            d.text((14, by+2), nm, fill=TX3, font=F["r10"])
            bar(d, 140, by+3, W-194, 10, pct, colors[i%3])
            d.text((W-46, by), fmt_tk(tk), fill=TX2, font=F["r10"])

    nav(d, 6, TOTAL_PAGES)
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
        import fcntl, struct, socket as _s
        for ifn in ["wlan0", "eth0"]:
            try:
                s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                ip = _s.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', ifn[:15].encode()))[20:24])
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
    d.rectangle((0, 0, W, 36), fill=PN)
    d.rectangle((0, 0, 3, 36), fill=AC["sys"])
    d.text((14, 8), "SIDEMON", fill=AC["sys"], font=F["b18"])
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
