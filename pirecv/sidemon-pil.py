#!/usr/bin/env python3
"""SideMon PIL receiver — renders 6 dashboards to /dev/fb0"""
import socket, json, threading, time, os, sys, argparse, select

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
FD = "/usr/share/fonts/truetype"

# ══════════════════════════════════════════════════════════════════════
# Fonts
# ══════════════════════════════════════════════════════════════════════

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
for s in [8,9,10,11,12,13,14,15,16,18,20,22,24,26,28,30,32,36,40,44,48,52,60]:
    F["r"+str(s)] = ff("r", s); F["b"+str(s)] = ff("b", s)

# ══════════════════════════════════════════════════════════════════════
# Colors
# ══════════════════════════════════════════════════════════════════════

C = {
    "w": (238,240,246), "gr": (136,140,158), "dm": (90,94,114),
    "cpu": (62,216,122), "mem": (64,168,240), "disk": (240,150,40),
    "net": (38,192,164), "load": (240,192,32),
    "codex": (155,107,255), "gn": (62,216,122), "warn": (240,150,40), "dng": (240,84,68),
    "cyan": (64,192,240), "purple": (180,130,255), "gold": (255,200,80),
    "rose": (240,100,120), "sky": (100,180,240),
}

THEMES = {
    "system":   {"bg": (8, 10, 18),   "pn": (18, 20, 34),
                 "cpu_bg": (12, 32, 20), "mem_bg": (12, 24, 42), "disk_bg": (40, 24, 8),
                 "hdr_fg": (62, 216, 122)},
    "ccswitch": {"bg": (4, 12, 16),  "pn": (14, 26, 32),
                 "hdr_fg": (64, 192, 240)},
    "clash":    {"bg": (16, 6, 16),  "pn": (30, 14, 30),
                 "hdr_fg": (240, 130, 50)},
    "codex":    {"bg": (20, 12, 28), "pn": (32, 22, 42), "codex_bg": (24, 14, 44),
                 "hdr_fg": (180, 130, 255)},
    "weather":  {"bg": (6, 10, 28),  "pn": (16, 22, 44),
                 "card": (20, 28, 54), "hdr_bg": (12, 18, 38),
                 "hdr_fg": (255, 200, 80)},
    "omlx":     {"bg": (12, 18, 8),  "pn": (22, 34, 18),
                 "hdr_fg": (100, 220, 100)},
}

state = {}; lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════
# Drawing Helpers
# ══════════════════════════════════════════════════════════════════════

def rrect(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def arc(d, cx, cy, ri, ro, pct, fg, bgc):
    bb = (cx-ro, cy-ro, cx+ro, cy+ro)
    d.ellipse(bb, outline=bgc, width=ro-ri)
    if pct > 0.001:
        s = 225; e = s - 360*min(pct,1)
        if e < 0: e += 360
        d.arc(bb, s, e, fill=fg, width=ro-ri)

def bar_h(d, x, y, w, h, pct, fg, bgc=None):
    if bgc is None: bgc = (40,42,56)
    rrect(d, (x, y, x+w, y+h), h//2, bgc)
    fw = max(2, int(w * min(pct, 1)))
    if fw > h:
        rrect(d, (x, y, x+fw, y+h), h//2, fg)

def bar_v(d, x, y, w, h, pct, fg, bgc=None):
    if bgc is None: bgc = (40,42,56)
    rrect(d, (x, y, x+w, y+h), 4, bgc)
    fh = max(2, int(h * min(pct, 1)))
    if fh > 2:
        rrect(d, (x, y+h-fh, x+w, y+h), 4, fg)

def card(d, x, y, w, h, fill=None):
    if fill is None: fill = (20,22,36)
    rrect(d, (x, y, x+w, y+h), 8, fill)

def pill(d, x, y, text, fg, bgc, font_key="r10"):
    tw = d.textlength(text, font=F[font_key])
    ph = F[font_key].size + 6
    pw = tw + 10
    rrect(d, (x, y, x+pw, y+ph), ph//2, bgc)
    d.text((x+5, y+2), text, fill=fg, font=F[font_key])
    return pw

def center_text(d, y, text, fill, font_key):
    tw = d.textlength(text, font=F[font_key])
    d.text(((W-tw)//2, y), text, fill=fill, font=F[font_key])

def value_label(d, cx, cy, val_text, unit, val_font="b28", unit_font="r12", val_color=None, unit_color=None):
    if val_color is None: val_color = C["w"]
    if unit_color is None: unit_color = C["gr"]
    tw_v = d.textlength(val_text, font=F[val_font])
    tw_u = d.textlength(unit, font=F[unit_font])
    total = tw_v + tw_u + 2
    sx = cx - total/2
    d.text((sx, cy), val_text, fill=val_color, font=F[val_font])
    d.text((sx + tw_v + 2, cy + F[val_font].size - F[unit_font].size), unit, fill=unit_color, font=F[unit_font])

def gcol(p):
    if p < 60: return C["gn"]
    if p < 85: return C["warn"]
    return C["dng"]

def fmt_tk(n):
    if n >= 1_000_000_000: return f"{n/1e9:.1f}B"
    if n >= 1_000_000: return f"{n/1e6:.1f}M"
    if n >= 1000: return f"{n/1000:.0f}K"
    return str(n)

def fmtb(n):
    if n >= 1<<30: return f"{n/(1<<30):.1f} GB"
    if n >= 1<<20: return f"{n/(1<<20):.1f} MB"
    if n >= 1<<10: return f"{n/(1<<10):.0f} KB"
    return f"{n:.0f} B"

def fmtk(n):
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1000: return f"{n/1000:.0f}K"
    return f"{n:.0f}"

def hdr(d, title, sub, t):
    d.rectangle((0, 0, W, 42), fill=t["pn"])
    fg = t.get("hdr_fg", C["w"])
    d.text((16, 8), title, fill=fg, font=F["b20"])
    if sub:
        tw = d.textlength(sub, font=F["r12"])
        d.text((W-16-tw, 11), sub, fill=C["dm"], font=F["r12"])

def dots(d, cur, total):
    dr, sp = 3, 12
    tw = total*(2*dr) + (total-1)*sp
    sx = (W-tw)//2
    for i in range(total):
        cx = sx + i*(2*dr+sp)
        col = C["w"] if i==cur else C["dm"]
        d.ellipse((cx-dr, H-16-dr, cx+dr, H-16+dr), fill=col)

# ══════════════════════════════════════════════════════════════════════
# Page: System  — 3 ring gauges + hostname / uptime / net / load
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    t = THEMES["system"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "SYSTEM", s.get("hostname", "?"), t)

    # ── Ring gauges row (top area: y=52..200) ──
    ry = 130; cx = [80, 240, 400]; ri, ro = 35, 52
    rings_data = [
        (s.get("cpu", 0)/100,  C["cpu"],  t["cpu_bg"],  f"{int(s.get('cpu',0))}", "%", "CPU"),
        (s.get("mem", 0)/100,  C["mem"],  t["mem_bg"],  f"{int(s.get('mem',0))}", "%", "MEM"),
        (s.get("disk", 0)/100, C["disk"], t["disk_bg"], f"{int(s.get('disk',0))}", "%", "DISK"),
    ]
    for i, (pct, fg, bg, val, suf, label) in enumerate(rings_data):
        x = cx[i]
        arc(d, x, ry, ri, ro, min(pct,1), fg, bg)
        tw_v = d.textlength(val, font=F["b22"])
        tw_s = d.textlength(suf, font=F["r11"])
        total_w = tw_v + tw_s
        sx = x - total_w/2
        d.text((sx, ry-14), val, fill=fg, font=F["b22"])
        d.text((sx+tw_v+1, ry-9), suf, fill=C["gr"], font=F["r11"])
        # label below ring
        tw_l = d.textlength(label, font=F["r10"])
        d.text((x - tw_l//2, ry+ro+6), label, fill=C["dm"], font=F["r10"])

    # ── Bottom info row: uptime / net / load ──
    by = 208
    # Uptime card
    card(d, 10, by, 145, 48)
    d.text((20, by+6), "UPTIME", fill=C["dm"], font=F["r9"])
    d.text((20, by+22), s.get("uptime","?"), fill=C["gn"], font=F["b16"])
    # Net card
    card(d, 165, by, 150, 48)
    d.text((175, by+6), "NETWORK", fill=C["dm"], font=F["r9"])
    rx_s = fmtb(s.get("net_rx",0)) + "/s"
    tx_s = fmtb(s.get("net_tx",0)) + "/s"
    d.text((175, by+20), f"R {rx_s}", fill=C["cyan"], font=F["r11"])
    d.text((175, by+34), f"T {tx_s}", fill=C["rose"], font=F["r11"])
    # Load card
    card(d, 325, by, 145, 48)
    d.text((335, by+6), "LOAD AVG", fill=C["dm"], font=F["r9"])
    ld = s.get("load", [0,0,0])
    d.text((335, by+22), f"{ld[0]:.2f}  {ld[1]:.2f}  {ld[2]:.2f}", fill=C["load"], font=F["b12"])

    dots(d, 0, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: API Usage (CC Switch) — balance gauges + today's stats
# ══════════════════════════════════════════════════════════════════════

def pg_apis(api):
    t = THEMES["ccswitch"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "API USAGE", "", t)

    # ── Balance arc gauges (side by side) ──
    # DeepSeek
    ds_bal = api.get("ds_balance", "?")
    try: ds_val = float(ds_bal)
    except: ds_val = 0
    ds_pct = min(ds_val / 50.0, 1.0) if ds_val > 0 else 0
    cx1, gy = 130, 145
    arc(d, cx1, gy, 40, 58, ds_pct, C["cyan"], (14,26,32))
    ds_str = f"{ds_val:.1f}" if isinstance(ds_val, float) else str(ds_val)
    center_text(d, gy-14, ds_str, C["cyan"], "b28")
    center_text(d, gy+18, "CNY", C["dm"], "r12")
    center_text(d, gy+50, "DeepSeek", C["w"], "r11")

    # MiMo
    mm_bal = api.get("mm_balance", "?")
    try: mm_val = float(mm_bal)
    except: mm_val = 0
    mm_pct = min(mm_val / 50.0, 1.0) if mm_val > 0 else 0
    cx2 = 350
    arc(d, cx2, gy, 40, 58, mm_pct, C["purple"], (14,26,32))
    mm_str = f"{mm_val:.1f}" if isinstance(mm_val, float) else str(mm_val)
    center_text(d, gy-14, mm_str, C["purple"], "b28")
    center_text(d, gy+18, "CNY", C["dm"], "r12")
    center_text(d, gy+50, "MiMo", C["w"], "r11")

    # ── Separator line ──
    d.line((20, 200, W-20, 200), fill=(40,42,56), width=1)

    # ── Today's consumption stats ──
    y0 = 210
    card(d, 10, y0, 145, 58)
    d.text((20, y0+6), "TOTAL TOKENS", fill=C["dm"], font=F["r9"])
    d.text((20, y0+24), fmt_tk(api.get("total_tokens", 0)), fill=C["w"], font=F["b20"])

    card(d, 165, y0, 145, 58)
    d.text((175, y0+6), "OUTPUT", fill=C["dm"], font=F["r9"])
    d.text((175, y0+24), fmt_tk(api.get("output_tokens", 0)), fill=C["gn"], font=F["b20"])

    card(d, 320, y0, 150, 58)
    d.text((330, y0+6), "CACHE HIT", fill=C["dm"], font=F["r9"])
    ch = api.get("cache_hit_rate", 0)
    d.text((330, y0+24), f"{ch:.1f}%", fill=C["gold"], font=F["b20"])

    # ── Status bar ──
    node = api.get("node", "Unknown")
    if len(node) > 22: node = node[:20] + ".."
    d.text((16, 276), f"Node: {node}", fill=C["dm"], font=F["r10"])

    dots(d, 1, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Clash — gauge + node + traffic + connections
# ══════════════════════════════════════════════════════════════════════

def pg_clash(cl):
    t = THEMES["clash"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    status = "ONLINE" if cl.get("running") else "OFFLINE"
    hdr(d, "CLASH", status, t)

    # ── Current node card ──
    node = cl.get("current_node", "?")
    if len(node) > 32: node = node[:30] + ".."
    card(d, 10, 52, W-20, 40, fill=(28,16,28))
    pill(d, 20, 60, "NODE", C["warn"], (50,28,20))
    d.text((76, 60), node, fill=C["w"], font=F["b14"])

    # ── Traffic bar ──
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    y0 = 102
    card(d, 10, y0, W-20, 36)
    d.text((20, y0+4), "TRAFFIC", fill=C["dm"], font=F["r9"])
    if tu and tt:
        d.text((100, y0+4), f"{tu} / {tt}", fill=C["w"], font=F["r12"])
        # parse rough percentage
        try:
            num = float(''.join(c for c in tu.split()[0] if c.isdigit() or c=='.'))
            den = float(''.join(c for c in tt.split()[0] if c.isdigit() or c=='.'))
            tp = num/den if den > 0 else 0
        except: tp = 0
        bar_h(d, 20, y0+22, W-40, 6, tp, C["rose"])
    else:
        d.text((100, y0+4), "N/A", fill=C["dm"], font=F["r12"])

    # ── Expire ──
    y1 = 148
    exp = cl.get("expire_date", "")
    card(d, 10, y1, W-20, 32)
    d.text((20, y1+6), "EXPIRE", fill=C["dm"], font=F["r9"])
    d.text((90, y1+4), exp if exp else "N/A", fill=C["gold"], font=F["r12"])

    # ── Upload / Download totals ──
    y2 = 190
    card(d, 10, y2, 228, 52)
    d.text((20, y2+6), "UPLOAD", fill=C["dm"], font=F["r9"])
    d.text((20, y2+24), fmtb(cl.get("upload_total", 0)), fill=C["cyan"], font=F["b14"])
    card(d, 248, y2, 222, 52)
    d.text((258, y2+6), "DOWNLOAD", fill=C["dm"], font=F["r9"])
    d.text((258, y2+24), fmtb(cl.get("download_total", 0)), fill=C["rose"], font=F["b14"])

    # ── Active connections + mode ──
    y3 = 252
    conn = cl.get("active_connections", 0)
    mode = cl.get("mode", "Rule")
    card(d, 10, y3, 228, 36)
    d.text((20, y3+8), "CONNECTIONS", fill=C["dm"], font=F["r9"])
    d.text((140, y3+4), str(conn), fill=C["w"], font=F["b16"])
    card(d, 248, y3, 222, 36)
    d.text((258, y3+8), "MODE", fill=C["dm"], font=F["r9"])
    d.text((310, y3+4), mode, fill=C["purple"], font=F["b16"])

    # ── Upload / Download speed bar ──
    y4 = 296
    # mini speed bar decoration
    d.rectangle((0, y4, W, H), fill=t["pn"])
    d.text((16, y4+4), "U", fill=C["cyan"], font=F["r10"])
    bar_h(d, 36, y4+6, 100, 8, min(cl.get("upload_total",0)/(10<<30), 1), C["cyan"])
    d.text((W//2+16, y4+4), "D", fill=C["rose"], font=F["r10"])
    bar_h(d, W//2+36, y4+6, 100, 8, min(cl.get("download_total",0)/(50<<30), 1), C["rose"])

    dots(d, 2, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Codex — 5h & 7d arc percentage gauges + model + reset
# ══════════════════════════════════════════════════════════════════════

def pg_codex(cx):
    t = THEMES["codex"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CODEX", "", t)

    # ── 5h usage gauge (left) ──
    cx1 = 130; gy = 140
    # Use a maximum of ~2M tokens as 100% for 5h
    tok_5h = cx.get("tokens_5h", 0)
    pct_5h = min(tok_5h / 2_000_000, 1.0) if tok_5h > 0 else 0
    arc(d, cx1, gy, 44, 64, pct_5h, C["codex"], (24,14,44))
    val_5h = fmt_tk(tok_5h)
    center_text(d, gy-18, val_5h, C["codex"], "b26")
    center_text(d, gy+12, "/ 2.0M", C["dm"], "r11")
    center_text(d, gy+54, "5 Hour", C["w"], "r12")

    # ── 7d usage gauge (right) ──
    cx2 = 350
    tok_7d = cx.get("tokens_7d", 0)
    # Use a maximum of ~10M tokens as 100% for 7d
    pct_7d = min(tok_7d / 10_000_000, 1.0) if tok_7d > 0 else 0
    arc(d, cx2, gy, 44, 64, pct_7d, C["purple"], (32,22,42))
    val_7d = fmt_tk(tok_7d)
    center_text(d, gy-18, val_7d, C["purple"], "b26")
    center_text(d, gy+12, "/ 10.0M", C["dm"], "r11")
    center_text(d, gy+54, "7 Day", C["w"], "r12")

    # ── Bottom info ──
    y0 = 214
    card(d, 10, y0, W-20, 38, fill=(24,14,44))
    d.text((20, y0+4), "MODEL", fill=C["dm"], font=F["r9"])
    model = cx.get("model", "?")
    if len(model) > 30: model = model[:28] + ".."
    d.text((80, y0+4), model, fill=C["purple"], font=F["r12"])

    y1 = 260
    card(d, 10, y1, W-20, 32, fill=(24,14,44))
    d.text((20, y1+4), "RESET", fill=C["dm"], font=F["r9"])
    d.text((80, y1+4), cx.get("reset_time", "?"), fill=C["gold"], font=F["r12"])

    dots(d, 3, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Weather — city / temp arc + conditions + humidity / wind
# ══════════════════════════════════════════════════════════════════════

def pg_weather(w):
    t = THEMES["weather"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)

    city = w.get("city", "?")
    date = w.get("date", "")
    dow = w.get("day_of_week", "")
    hdr(d, "WEATHER", f"{date} {dow}", t)

    # ── Temperature arc gauge (left) ──
    temp_c = w.get("temp_c", 0)
    # normalize: -10..45 -> 0..1
    temp_pct = max(0, min(1, (temp_c + 10) / 55))
    cx1 = 120; gy = 138
    temp_color = C["gold"] if temp_c < 30 else C["dng"]
    arc(d, cx1, gy, 42, 62, temp_pct, temp_color, (12,18,38))
    # temp value
    temp_str = f"{int(temp_c)}"
    center_text(d, gy-18, temp_str, C["w"], "b36")
    center_text(d, gy+20, "°C", C["dm"], "r14")
    # city below
    center_text(d, gy+54, city, C["gold"], "r12")

    # ── Right panel: conditions + humidity + wind ──
    rx = 230
    # Condition card
    card(d, rx, 52, 230, 44, fill=t["card"])
    cond = w.get("condition", "?")
    if len(cond) > 24: cond = cond[:22] + ".."
    d.text((rx+12, 60), cond, fill=C["w"], font=F["b16"])

    # Feels-like
    fl = w.get("feels_like_c", temp_c)
    d.text((rx+12, 80), f"Feels {int(fl)}°C", fill=C["dm"], font=F["r10"])

    # Humidity
    card(d, rx, 104, 110, 38, fill=t["card"])
    d.text((rx+10, 108), "HUMIDITY", fill=C["dm"], font=F["r9"])
    hum = w.get("humidity", 0)
    d.text((rx+10, 122), f"{hum}%", fill=C["cyan"], font=F["b14"])

    # Wind
    card(d, rx+120, 104, 110, 38, fill=t["card"])
    d.text((rx+130, 108), "WIND", fill=C["dm"], font=F["r9"])
    ws = w.get("wind_kph", 0)
    wd = w.get("wind_dir", "")
    d.text((rx+130, 122), f"{ws}km/h", fill=C["sky"], font=F["b14"])

    # ── Forecast row (3 days) ──
    y0 = 158
    forecasts = w.get("forecast", [])
    fw = 148
    for i, f in enumerate(forecasts[:3]):
        fx = 10 + i*(fw+8)
        card(d, fx, y0, fw, 72, fill=t["card"])
        # day
        day = f.get("day", "?")[:3]
        d.text((fx+10, y0+6), day, fill=C["dm"], font=F["r10"])
        # temp range
        hi = f.get("high_c", "?")
        lo = f.get("low_c", "?")
        d.text((fx+10, y0+24), f"{hi}°", fill=C["dng"], font=F["b14"])
        d.text((fx+58, y0+24), f"{lo}°", fill=C["sky"], font=F["b14"])
        # condition
        fc = f.get("condition", "?")
        if len(fc) > 16: fc = fc[:14] + ".."
        d.text((fx+10, y0+46), fc, fill=C["w"], font=F["r9"])

    # ── Sun / Moon ──
    y1 = 240
    card(d, 10, y1, 228, 40, fill=t["card"])
    sunrise = w.get("sunrise", "?")
    sunset = w.get("sunset", "?")
    d.text((20, y1+6), "SUN", fill=C["dm"], font=F["r9"])
    d.text((50, y1+4), f"{sunrise}", fill=C["gold"], font=F["r12"])
    d.text((140, y1+4), f"{sunset}", fill=C["gold"], font=F["r12"])

    card(d, 248, y1, 222, 40, fill=t["card"])
    uv = w.get("uv_index", "?")
    d.text((258, y1+6), "UV INDEX", fill=C["dm"], font=F["r9"])
    d.text((340, y1+4), str(uv), fill=C["rose"], font=F["r12"])

    dots(d, 4, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: omLX — memory gauge + model stats + request bar chart
# ══════════════════════════════════════════════════════════════════════

def pg_omlx(om):
    t = THEMES["omlx"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    status = "ONLINE" if om.get("running") else "OFFLINE"
    hdr(d, "OMLX", status, t)

    # ── Memory gauge (left) ──
    cx1 = 90; gy = 130
    mem_used = om.get("memory_used", 0)
    mem_ceil = om.get("memory_ceiling", 1)
    mem_pct = mem_used/mem_ceil if mem_ceil > 0 else 0
    arc(d, cx1, gy, 36, 52, mem_pct, C["gn"], (16,28,14))
    mem_str = f"{mem_used:.1f}"
    center_text(d, gy-14, mem_str, C["gn"], "b22")
    center_text(d, gy+8, "GB", C["dm"], "r10")
    center_text(d, gy+46, f"/ {mem_ceil:.1f} GB", C["dm"], "r10")

    # ── Loaded models / total requests (right) ──
    rx = 190
    card(d, rx, 52, 280, 36)
    d.text((rx+12, 56), "LOADED", fill=C["dm"], font=F["r9"])
    d.text((rx+80, 54), str(om.get("loaded_count", 0)), fill=C["gn"], font=F["b16"])
    d.text((rx+120, 56), f"/ {om.get('model_count',0)} models", fill=C["dm"], font=F["r10"])
    d.text((rx+12, 72), f"Requests: {om.get('total_requests',0)}", fill=C["w"], font=F["r10"])

    # ── Speed gauges ──
    y0 = 96
    card(d, rx, y0, 136, 36)
    d.text((rx+12, y0+4), "PROMPT", fill=C["dm"], font=F["r9"])
    sp = om.get("avg_prompt_speed", 0)
    d.text((rx+12, y0+18), f"{sp:.1f} tk/s", fill=C["cyan"], font=F["b12"])

    card(d, rx+144, y0, 136, 36)
    d.text((rx+156, y0+4), "GEN", fill=C["dm"], font=F["r9"])
    sg = om.get("avg_gen_speed", 0)
    d.text((rx+156, y0+18), f"{sg:.1f} tk/s", fill=C["gold"], font=F["b12"])

    # ── Token summary ──
    y1 = 140
    card(d, 10, y1, 150, 40)
    d.text((20, y1+6), "PROMPT TK", fill=C["dm"], font=F["r9"])
    d.text((20, y1+20), fmt_tk(om.get("total_prompt_tk", 0)), fill=C["w"], font=F["b12"])

    card(d, 170, y1, 150, 40)
    d.text((180, y1+6), "COMPL TK", fill=C["dm"], font=F["r9"])
    d.text((180, y1+20), fmt_tk(om.get("total_comp_tk", 0)), fill=C["w"], font=F["b12"])

    card(d, 330, y1, 140, 40)
    d.text((340, y1+6), "CACHED", fill=C["dm"], font=F["r9"])
    ce = om.get("cache_efficiency", 0) * 100
    d.text((340, y1+20), f"{ce:.1f}%", fill=C["gn"], font=F["b12"])

    # ── Cache efficiency bar ──
    y2 = 190
    card(d, 10, y2, W-20, 28)
    d.text((20, y2+6), "CACHE", fill=C["dm"], font=F["r9"])
    bar_h(d, 70, y2+8, W-100, 10, ce/100, C["gn"])

    # ── Top models bar chart ──
    y3 = 226
    top = om.get("top_models", [])[:5]
    if top:
        max_tk = max((m.get("tk_total", 1) for m in top), default=1)
        bar_area_h = H - y3 - 24
        bar_h_each = max(6, min(12, bar_area_h // len(top)))
        colors = [C["gn"], C["cyan"], C["gold"], C["rose"], C["purple"]]
        for i, m in enumerate(top):
            by = y3 + i * (bar_h_each + 2)
            tk = m.get("tk_total", 0)
            pct = tk / max_tk if max_tk > 0 else 0
            nm = m.get("name", "?")
            if len(nm) > 18: nm = nm[:16] + ".."
            d.text((14, by), nm, fill=C["dm"], font=F["r8"])
            bar_h(d, 120, by+1, W-170, bar_h_each-2, pct, colors[i%len(colors)])
            d.text((W-44, by), fmt_tk(tk), fill=C["w"], font=F["r8"])

    dots(d, 5, 6)
    return img


# ══════════════════════════════════════════════════════════════════════
# Renderer dispatch
# ══════════════════════════════════════════════════════════════════════

RENDERERS = {
    "system": pg_system, "ccswitch": pg_apis, "clash": pg_clash,
    "codex": pg_codex, "weather": pg_weather, "omlx": pg_omlx,
}

DEFAULT_ORDER = ["system", "ccswitch", "clash", "codex", "weather", "omlx"]
active_order = list(DEFAULT_ORDER)

def normalize_page_order(pages):
    order = []
    seen = set()
    for p in pages or []:
        if p in RENDERERS and p not in seen:
            order.append(p); seen.add(p)
    return order or list(DEFAULT_ORDER)


# ══════════════════════════════════════════════════════════════════════
# Framebuffer / IP display
# ══════════════════════════════════════════════════════════════════════

def get_local_ips():
    ips = []
    try:
        import fcntl, struct, socket as _s
        for ifname in ["wlan0", "eth0"]:
            try:
                s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                ip = _s.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', ifname[:15].encode()))[20:24])
                ips.append(ip)
            except: pass
            finally: s.close()
    except: pass
    return ips

def waiting_ip_text(ips=None):
    if ips is None: ips = get_local_ips()
    if ips:
        return f"IP: {', '.join(ips)}"
    return "Waiting for IP..."

def write_fb(path, img):
    img = img.rotate(180, expand=False)
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "RGB"))

def show_waiting(fb_dev):
    t = THEMES["system"]
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 42), fill=t["pn"])
    d.text((16, 8), "SideMon", fill=C["w"], font=F["b20"])
    d.text((W//2, H//2-48), "Waiting...", fill=C["dm"], font=F["b24"], anchor="mm")
    d.text((W//2, H//2-8), waiting_ip_text(), fill=C["w"], font=F["b26"], anchor="mm")
    d.text((W//2, H//2+30), "Mac -> Pi", fill=C["gr"], font=F["r16"], anchor="mm")
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
    buf = b""
    idle = 0
    while idle < 10:
        try:
            r, _, _ = select.select([conn], [], [], 1.0)
            if not r:
                idle += 1; continue
            data = conn.recv(65536)
            if not data: break
            idle = 0
            buf += data
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

# ── UDP Discovery Broadcaster ──
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
            cur = dict(state)
            order = list(active_order)
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
    threading.Thread(target=page_cycler, args=(args.fb, args.cycle,), daemon=True).start()

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
