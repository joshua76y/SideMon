#!/usr/bin/env python3
"""SideMon PIL receiver — renders dashboards to /dev/fb0"""
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
    F["r"+str(s)] = ff("r", s); F["b"+str(s)] = ff("b", s)

C = {
    "w": (225,228,236), "gr": (115,118,136), "dm": (75,78,96),
    "cpu": (75,195,135), "mem": (75,155,235), "disk": (225,155,50),
    "gn": (75,195,135), "warn": (225,155,50), "dng": (215,75,65),
    "cyan": (75,175,225), "purple": (155,115,235), "gold": (235,185,75),
    "rose": (215,95,105), "sky": (95,165,215),
}

THEMES = {
    "system":   {"bg": (10,12,20),   "pn": (18,20,32), "card": (22,24,38),
                 "accent": (75,195,135)},
    "ccswitch": {"bg": (8,14,20),    "pn": (16,24,32), "card": (18,28,38),
                 "accent": (75,175,225)},
    "clash":    {"bg": (14,10,18),   "pn": (26,18,30), "card": (30,22,36),
                 "accent": (225,135,55)},
    "codex":    {"bg": (16,12,24),   "pn": (28,22,40), "card": (32,24,48),
                 "accent": (155,115,235)},
    "weather":  {"bg": (8,12,24),    "pn": (14,20,38), "card": (18,26,48),
                 "accent": (235,185,75)},
    "omlx":     {"bg": (10,16,12),   "pn": (18,28,20), "card": (22,34,24),
                 "accent": (95,195,95)},
    "datetime": {"bg": (8,10,22),    "pn": (16,18,34), "card": (20,22,40),
                 "accent": (100,160,240)},
}

state = {}; lock = threading.Lock()

# ── Helpers ──

def rrect(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def arc(d, cx, cy, ri, ro, pct, fg, bgc):
    bb = (cx-ro, cy-ro, cx+ro, cy+ro)
    d.ellipse(bb, outline=bgc, width=ro-ri)
    if pct > 0.005:
        s, e = 225, 225 - 360*min(pct,1)
        if e < 0: e += 360
        d.arc(bb, s, e, fill=fg, width=ro-ri)

def bar(d, x, y, w, h, pct, fg):
    bgc = (fg[0]//6, fg[1]//6, fg[2]//6)
    rrect(d, (x, y, x+w, y+h), h//2, bgc)
    fw = max(4, int(w * min(pct, 1)))
    if fw > h: rrect(d, (x, y, x+fw, y+h), h//2, fg)

def card(d, x, y, w, h, fill):
    rrect(d, (x, y, x+w, y+h), 6, fill)

def ct(d, y, text, fill, fk):
    tw = d.textlength(text, font=F[fk])
    d.text(((W-tw)//2, y), text, fill=fill, font=F[fk])

def hdr(d, title, sub, t):
    d.rectangle((0, 0, W, 38), fill=t["pn"])
    fg = t.get("accent", C["w"])
    d.text((14, 8), title, fill=fg, font=F["b18"])
    if sub:
        tw = d.textlength(sub, font=F["r11"])
        d.text((W-14-tw, 12), sub, fill=C["dm"], font=F["r11"])

def dots(d, cur, total):
    dr, sp = 3, 12
    tw = total*(2*dr)+(total-1)*sp
    sx = (W-tw)//2
    for i in range(total):
        cx = sx + i*(2*dr+sp)
        col = C["w"] if i==cur else C["dm"]
        d.ellipse((cx-dr, H-10-dr, cx+dr, H-10+dr), fill=col)

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

# Content area: y 38..306 (268px usable), dots at y=300

# ══════════════════════════════════════════════════════════════════════
# System — 3 arc gauges with labels
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    t = THEMES["system"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "SYSTEM", s.get("hostname","?"), t)

    gy = 125; cx_list = [80, 240, 400]; ri, ro = 30, 46
    rings = [
        (s.get("cpu",0)/100,  C["cpu"],  t["card"], f"{int(s.get('cpu',0))}%", "CPU"),
        (s.get("mem",0)/100,  C["mem"],  t["card"], f"{int(s.get('mem',0))}%", "MEM"),
        (s.get("disk",0)/100, C["disk"], t["card"], f"{int(s.get('disk',0))}%", "DISK"),
    ]
    for i, (pct, fg, bg, val, label) in enumerate(rings):
        x = cx_list[i]
        arc(d, x, gy, ri, ro, min(pct,1), fg, bg)
        tw = d.textlength(val, font=F["b22"])
        d.text((x-tw//2, gy-13), val, fill=fg, font=F["b22"])
        tw2 = d.textlength(label, font=F["r12"])
        d.text((x-tw2//2, gy+ro+6), label, fill=C["dm"], font=F["r12"])

    # Bottom: uptime + load — two big cards
    by = 196
    hw = (W-30)//2
    card(d, 10, by, hw, 56, t["card"])
    d.text((20, by+6), "UPTIME", fill=C["dm"], font=F["r11"])
    d.text((20, by+26), s.get("uptime","?"), fill=C["gn"], font=F["b22"])

    card(d, 20+hw, by, hw, 56, t["card"])
    d.text((30+hw, by+6), "LOAD AVG", fill=C["dm"], font=F["r11"])
    ld = s.get("load",[0,0,0])
    d.text((30+hw, by+26), f"{ld[0]:.2f}  {ld[1]:.2f}", fill=C["disk"], font=F["b18"])

    dots(d, 0, 7)
    return img

# ══════════════════════════════════════════════════════════════════════
# API Usage — balance bars + 3 stat cards
# ══════════════════════════════════════════════════════════════════════

def pg_apis(api):
    t = THEMES["ccswitch"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "API USAGE", "", t)

    # Two balance horizontal bars
    y0 = 48
    for i, (label, key, color) in enumerate([
        ("DeepSeek", "ds_balance", C["cyan"]),
        ("MiMo", "mm_balance", C["purple"]),
    ]):
        by = y0 + i*56
        card(d, 10, by, W-20, 46, t["card"])
        d.text((20, by+4), label, fill=color, font=F["b14"])
        bal = api.get(key, "?")
        try: val = float(bal)
        except: val = 0
        pct = min(val/50.0, 1.0) if val > 0 else 0
        bar(d, 20, by+24, W-160, 14, pct, color)
        vs = f"{val:.1f}" if isinstance(val, float) else str(val)
        d.text((W-120, by+22), f"CNY {vs}", fill=C["w"], font=F["b16"])

    # 3 stat cards
    y1 = 164
    cw = (W-40)//3
    for i, (lbl, val, col) in enumerate([
        ("TOTAL", fmt_tk(api.get("total_tokens",0)), C["w"]),
        ("OUTPUT", fmt_tk(api.get("output_tokens",0)), C["gn"]),
        ("CACHE HIT", f"{api.get('cache_hit_rate',0):.1f}%", C["gold"]),
    ]):
        x = 10 + i*(cw+10)
        card(d, x, y1, cw, 72, t["card"])
        d.text((x+10, y1+10), lbl, fill=C["dm"], font=F["r11"])
        d.text((x+10, y1+34), val, fill=col, font=F["b24"])

    dots(d, 1, 7)
    return img

# ══════════════════════════════════════════════════════════════════════
# Clash — node + traffic + up/down
# ══════════════════════════════════════════════════════════════════════

def pg_clash(cl):
    t = THEMES["clash"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    st = "ONLINE" if cl.get("running") else "OFFLINE"
    hdr(d, "CLASH", st, t)

    # Node
    node = cl.get("current_node", "?")
    if len(node) > 26: node = node[:24]+".."
    card(d, 10, 46, W-20, 44, t["card"])
    d.text((20, 52), "NODE", fill=C["dm"], font=F["r11"])
    d.text((20, 70), node, fill=C["w"], font=F["b16"])

    # Traffic
    y0 = 100
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    card(d, 10, y0, W-20, 44, t["card"])
    d.text((20, y0+6), "TRAFFIC", fill=C["dm"], font=F["r11"])
    if tu and tt:
        d.text((20, y0+24), f"{tu} / {tt}", fill=C["w"], font=F["b16"])
    else:
        d.text((20, y0+24), "N/A", fill=C["dm"], font=F["b14"])

    # Upload + Download
    y1 = 154
    hw = (W-30)//2
    card(d, 10, y1, hw, 52, t["card"])
    d.text((20, y1+6), "UPLOAD", fill=C["dm"], font=F["r11"])
    d.text((20, y1+26), fmtb(cl.get("upload_total",0)), fill=C["cyan"], font=F["b18"])

    card(d, 20+hw, y1, hw, 52, t["card"])
    d.text((30+hw, y1+6), "DOWNLOAD", fill=C["dm"], font=F["r11"])
    d.text((30+hw, y1+26), fmtb(cl.get("download_total",0)), fill=C["rose"], font=F["b18"])

    # Expire + Mode
    y2 = 216
    card(d, 10, y2, hw, 44, t["card"])
    d.text((20, y2+6), "EXPIRE", fill=C["dm"], font=F["r11"])
    exp = cl.get("expire_date", "") or "N/A"
    d.text((20, y2+24), exp, fill=C["gold"], font=F["b14"])

    card(d, 20+hw, y2, hw, 44, t["card"])
    d.text((30+hw, y2+6), "MODE", fill=C["dm"], font=F["r11"])
    d.text((30+hw, y2+24), cl.get("mode","Rule"), fill=C["purple"], font=F["b16"])

    dots(d, 2, 7)
    return img

# ══════════════════════════════════════════════════════════════════════
# Codex — 5h & 7d PERCENTAGE gauges (arc + large %)
# ══════════════════════════════════════════════════════════════════════

def pg_codex(cx):
    t = THEMES["codex"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CODEX", "", t)

    # Two percentage arcs — these show usage as % of quota
    gy = 120
    for i, (label, key, mx, color) in enumerate([
        ("5 Hour", "tokens_5h", 2_000_000, C["purple"]),
        ("7 Day", "tokens_7d", 10_000_000, C["cyan"]),
    ]):
        x = 130 + i*220
        tok = cx.get(key, 0)
        pct = min(tok/mx, 1.0) if tok > 0 else 0
        pct_int = int(pct*100)
        arc(d, x, gy, 40, 58, pct, color, t["card"])
        # Large percentage number
        vs = f"{pct_int}%"
        tw = d.textlength(vs, font=F["b30"])
        d.text((x-tw//2, gy-18), vs, fill=color, font=F["b30"])
        # Token count below
        ts = fmt_tk(tok)
        tw2 = d.textlength(ts, font=F["r10"])
        d.text((x-tw2//2, gy+16), ts, fill=C["dm"], font=F["r10"])
        # Label
        tw3 = d.textlength(label, font=F["r12"])
        d.text((x-tw3//2, gy+48), label, fill=C["w"], font=F["r12"])

    # Model + reset
    y0 = 194
    card(d, 10, y0, W-20, 38, t["card"])
    d.text((20, y0+8), "MODEL", fill=C["dm"], font=F["r11"])
    model = cx.get("model", "?")
    if len(model) > 26: model = model[:24]+".."
    d.text((90, y0+8), model, fill=C["purple"], font=F["b14"])

    y1 = 240
    card(d, 10, y1, W-20, 30, t["card"])
    d.text((20, y1+5), "RESET", fill=C["dm"], font=F["r11"])
    d.text((90, y1+4), cx.get("reset_time","?"), fill=C["gold"], font=F["r14"])

    dots(d, 3, 7)
    return img

# ══════════════════════════════════════════════════════════════════════
# Weather — temperature bar + condition + forecast
# ══════════════════════════════════════════════════════════════════════

def pg_weather(w):
    t = THEMES["weather"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    date = w.get("date", "")
    dow = w.get("day_of_week", "")
    hdr(d, "WEATHER", f"{date} {dow}", t)

    # Temperature + condition — two side cards
    temp_c = w.get("temp_c", 0)
    tc = C["gold"] if temp_c < 30 else C["dng"]
    hw = (W-30)//2

    card(d, 10, 46, hw, 64, t["card"])
    d.text((20, 50), "TEMP", fill=C["dm"], font=F["r11"])
    d.text((20, 68), f"{int(temp_c)}°C", fill=tc, font=F["b30"])
    city = w.get("city", "?")
    d.text((20, 100), city, fill=C["dm"], font=F["r10"])

    cond = w.get("condition", "?")
    if len(cond) > 14: cond = cond[:12]+".."
    card(d, 20+hw, 46, hw, 64, t["card"])
    d.text((30+hw, 50), "CONDITION", fill=C["dm"], font=F["r11"])
    d.text((30+hw, 70), cond, fill=C["w"], font=F["b18"])
    fl = w.get("feels_like_c", temp_c)
    d.text((30+hw, 100), f"Feels {int(fl)}°C", fill=C["dm"], font=F["r10"])

    # Humidity + Wind
    y0 = 120
    card(d, 10, y0, hw, 42, t["card"])
    d.text((20, y0+6), "HUMIDITY", fill=C["dm"], font=F["r11"])
    d.text((20, y0+22), f"{w.get('humidity',0)}%", fill=C["cyan"], font=F["b16"])

    card(d, 20+hw, y0, hw, 42, t["card"])
    d.text((30+hw, y0+6), "WIND", fill=C["dm"], font=F["r11"])
    d.text((30+hw, y0+22), f"{w.get('wind_kph',0)} km/h", fill=C["sky"], font=F["b16"])

    # 3-day forecast
    y1 = 172
    forecasts = w.get("forecast", [])[:3]
    fw = (W-40)//3
    for i, fc in enumerate(forecasts):
        fx = 10 + i*(fw+5)
        card(d, fx, y1, fw, 60, t["card"])
        day = fc.get("day","?")[:3]
        d.text((fx+8, y1+4), day, fill=C["dm"], font=F["r11"])
        hi = fc.get("high_c","?")
        lo = fc.get("low_c","?")
        d.text((fx+8, y1+24), f"{hi}°", fill=C["dng"], font=F["b18"])
        d.text((fx+60, y1+24), f"{lo}°", fill=C["sky"], font=F["b18"])
        c2 = fc.get("condition","?")
        if len(c2) > 12: c2 = c2[:10]+".."
        d.text((fx+8, y1+44), c2, fill=C["w"], font=F["r10"])

    # Sunrise/Sunset + UV
    y2 = 242
    card(d, 10, y2, hw, 30, t["card"])
    d.text((20, y2+5), "SUN", fill=C["dm"], font=F["r10"])
    d.text((56, y2+4), f"{w.get('sunrise','?')}", fill=C["gold"], font=F["r12"])
    d.text((130, y2+4), f"{w.get('sunset','?')}", fill=C["gold"], font=F["r12"])

    card(d, 20+hw, y2, hw, 30, t["card"])
    d.text((30+hw, y2+5), "UV", fill=C["dm"], font=F["r10"])
    d.text((60+hw, y2+3), str(w.get("uv_index","?")), fill=C["rose"], font=F["b16"])

    dots(d, 4, 7)
    return img

# ══════════════════════════════════════════════════════════════════════
# omLX — memory bar + stats + models
# ══════════════════════════════════════════════════════════════════════

def pg_omlx(om):
    t = THEMES["omlx"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    st = "ONLINE" if om.get("running") else "OFFLINE"
    hdr(d, "OMLX", st, t)

    # Memory — horizontal bar
    card(d, 10, 46, W-20, 46, t["card"])
    mem_u = om.get("memory_used", 0)
    mem_c = om.get("memory_ceiling", 1)
    mem_p = mem_u/mem_c if mem_c > 0 else 0
    d.text((20, 50), "MEMORY", fill=C["dm"], font=F["r11"])
    d.text((120, 50), f"{mem_u:.1f} / {mem_c:.1f} GB", fill=C["gn"], font=F["b14"])
    bar(d, 20, 72, W-40, 12, mem_p, C["gn"])

    # Loaded + requests
    y0 = 102
    cw = (W-30)//2
    card(d, 10, y0, cw, 38, t["card"])
    d.text((20, y0+6), "MODELS", fill=C["dm"], font=F["r11"])
    d.text((20, y0+22), f"{om.get('loaded_count',0)} / {om.get('model_count',0)}", fill=C["gn"], font=F["b14"])

    card(d, 20+cw, y0, cw, 38, t["card"])
    d.text((30+cw, y0+6), "REQUESTS", fill=C["dm"], font=F["r11"])
    d.text((30+cw, y0+22), fmt_tk(om.get("total_requests",0)), fill=C["w"], font=F["b14"])

    # Speed
    y1 = 150
    card(d, 10, y1, cw, 38, t["card"])
    d.text((20, y1+6), "PROMPT", fill=C["dm"], font=F["r11"])
    d.text((20, y1+22), f"{om.get('avg_prompt_speed',0):.1f} tk/s", fill=C["cyan"], font=F["b14"])

    card(d, 20+cw, y1, cw, 38, t["card"])
    d.text((30+cw, y1+6), "GEN", fill=C["dm"], font=F["r11"])
    d.text((30+cw, y1+22), f"{om.get('avg_gen_speed',0):.1f} tk/s", fill=C["gold"], font=F["b14"])

    # Cache efficiency bar
    y2 = 198
    card(d, 10, y2, W-20, 28, t["card"])
    ce = om.get("cache_efficiency", 0)*100
    d.text((20, y2+6), "CACHE", fill=C["dm"], font=F["r10"])
    bar(d, 74, y2+8, W-130, 10, ce/100, C["gn"])
    d.text((W-56, y2+5), f"{ce:.0f}%", fill=C["gn"], font=F["r10"])

    # Top 3 models
    y3 = 236
    top = om.get("top_models", [])[:3]
    if top:
        max_tk = max((m.get("tk_total",1) for m in top), default=1)
        colors = [C["gn"], C["cyan"], C["gold"]]
        for i, m in enumerate(top):
            by = y3 + i*20
            tk = m.get("tk_total",0)
            pct = tk/max_tk if max_tk>0 else 0
            nm = m.get("name","?")
            if len(nm) > 20: nm = nm[:18]+".."
            d.text((14, by+2), nm, fill=C["dm"], font=F["r10"])
            bar(d, 140, by+3, W-190, 10, pct, colors[i%3])
            d.text((W-42, by), fmt_tk(tk), fill=C["w"], font=F["r10"])

    dots(d, 5, 7)
    return img


# ══════════════════════════════════════════════════════════════════════
# Infrastructure
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# DateTime — clock + calendar
# ══════════════════════════════════════════════════════════════════════

def pg_datetime(dt_data):
    t = THEMES["datetime"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "DATETIME", "", t)

    # Get current time from Mac (or local)
    ts = dt_data.get("timestamp", 0)
    if ts > 0:
        utc_now = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_tz = timezone(timedelta(hours=8))
        now = utc_now.astimezone(local_tz)
    else:
        now = datetime.now()

    # ── Large time display (left) ──
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y-%m-%d")
    dow_str = now.strftime("%A")

    card(d, 10, 46, 220, 90, t["card"])
    # Time
    tw = d.textlength(time_str, font=F["b48"])
    d.text((10 + (220-tw)//2, 52), time_str, fill=C["w"], font=F["b48"])
    # Date
    tw2 = d.textlength(date_str, font=F["r14"])
    d.text((10 + (220-tw2)//2, 108), date_str, fill=C["gr"], font=F["r14"])
    # Day of week
    tw3 = d.textlength(dow_str, font=F["r12"])
    d.text((10 + (220-tw3)//2, 126), dow_str, fill=C["cyan"], font=F["r12"])

    # ── Uptime (right of time) ──
    card(d, 240, 46, 230, 90, t["card"])
    d.text((250, 52), "SYSTEM TIME", fill=C["dm"], font=F["r11"])
    sec_str = now.strftime(":%S")
    d.text((250, 72), f"{now.strftime('%H:%M')}{sec_str}", fill=C["accent"], font=F["b22"])
    tz_str = now.strftime("UTC%z")
    d.text((250, 100), tz_str, fill=C["gr"], font=F["r12"])
    # Seconds as large number
    d.text((420, 68), now.strftime("%S"), fill=C["dm"], font=F["b30"])

    # ── Calendar ──
    y0 = 148
    card(d, 10, y0, W-20, 140, t["card"])

    # Month header
    month_str = now.strftime("%B %Y")
    tw = d.textlength(month_str, font=F["b14"])
    d.text(((W-tw)//2, y0+4), month_str, fill=C["accent"], font=F["b14"])

    # Day headers
    day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    col_w = (W-40) // 7
    for i, dn in enumerate(day_names):
        dx = 16 + i*col_w
        color = C["sky"] if i >= 5 else C["dm"]
        d.text((dx, y0+22), dn, fill=color, font=F["r10"])

    # Calendar days
    cal = calendar.monthcalendar(now.year, now.month)
    today_day = now.day
    row_h = 16
    for week_i, week in enumerate(cal):
        for day_i, day in enumerate(week):
            if day == 0: continue
            dx = 16 + day_i*col_w
            dy = y0 + 40 + week_i*row_h
            is_today = day == today_day
            is_weekend = day_i >= 5
            if is_today:
                # Highlight today
                rrect(d, (dx-2, dy-1, dx+col_w-6, dy+row_h-3), 3, C["accent"])
                d.text((dx, dy), f"{day:2d}", fill=(10,12,20), font=F["b11"])
            else:
                color = C["sky"] if is_weekend else C["w"]
                d.text((dx, dy), f"{day:2d}", fill=color, font=F["r11"])

    dots(d, 6, 7)
    return img

RENDERERS = {
    "system": pg_system, "ccswitch": pg_apis, "clash": pg_clash,
    "codex": pg_codex, "weather": pg_weather, "omlx": pg_omlx,
    "datetime": pg_datetime,
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
    t = THEMES["system"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 38), fill=t["pn"])
    d.text((14, 8), "SideMon", fill=C["w"], font=F["b18"])
    ct(d, H//2-48, "Waiting...", C["dm"], "b24")
    ct(d, H//2-8, waiting_ip_text(), C["w"], "b22")
    ct(d, H//2+24, "Mac -> Pi", C["gr"], "r14")
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
    print(f"SideMon PIL :{args.port} -> {args.fb}  {args.cycle}s x 6", file=sys.stderr)
    while True:
        conn, addr = srv.accept()
        print(f"Connected: {addr[0]}", file=sys.stderr)
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
