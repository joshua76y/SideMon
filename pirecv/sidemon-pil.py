#!/usr/bin/env python3
"""SideMon PIL receiver — renders 6 dashboards to /dev/fb0"""
import socket, json, threading, time, os, sys, argparse, select

from PIL import Image, ImageDraw, ImageFont

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
for s in [9,10,11,12,13,14,16,18,20,22,24,26,28,30,32,36,40]:
    F["r"+str(s)] = ff("r", s); F["b"+str(s)] = ff("b", s)

# ── Colors ──
C = {
    "w": (230,232,240), "gr": (120,124,140), "dm": (80,84,100),
    "cpu": (80,200,140), "mem": (80,160,240), "disk": (230,160,50),
    "gn": (80,200,140), "warn": (230,160,50), "dng": (220,80,70),
    "cyan": (80,180,230), "purple": (160,120,240), "gold": (240,190,80),
    "rose": (220,100,110), "sky": (100,170,220),
}

THEMES = {
    "system":   {"bg": (10,12,20),   "pn": (18,20,32), "card": (22,24,38),
                 "hdr_fg": (80,200,140)},
    "ccswitch": {"bg": (8,14,20),    "pn": (16,24,32), "card": (18,28,38),
                 "hdr_fg": (80,180,230)},
    "clash":    {"bg": (14,10,18),   "pn": (26,18,30), "card": (30,22,36),
                 "hdr_fg": (230,140,60)},
    "codex":    {"bg": (16,12,24),   "pn": (28,22,40), "card": (32,24,48),
                 "hdr_fg": (160,120,240)},
    "weather":  {"bg": (8,12,24),    "pn": (14,20,38), "card": (18,26,48),
                 "hdr_fg": (240,190,80)},
    "omlx":     {"bg": (10,16,12),   "pn": (18,28,20), "card": (22,34,24),
                 "hdr_fg": (100,200,100)},
}

state = {}; lock = threading.Lock()

# ── Drawing helpers ──

def rrect(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def arc(d, cx, cy, ri, ro, pct, fg, bgc):
    bb = (cx-ro, cy-ro, cx+ro, cy+ro)
    d.ellipse(bb, outline=bgc, width=ro-ri)
    if pct > 0.005:
        s = 225; e = s - 360*min(pct,1)
        if e < 0: e += 360
        d.arc(bb, s, e, fill=fg, width=ro-ri)

def bar_h(d, x, y, w, h, pct, fg):
    bgc = (fg[0]//5, fg[1]//5, fg[2]//5)
    rrect(d, (x, y, x+w, y+h), h//2, bgc)
    fw = max(4, int(w * min(pct, 1)))
    if fw > h:
        rrect(d, (x, y, x+fw, y+h), h//2, fg)

def card(d, x, y, w, h, fill=None):
    if fill is None: fill = (22,24,38)
    rrect(d, (x, y, x+w, y+h), 6, fill)

def ct(d, y, text, fill, fk):
    tw = d.textlength(text, font=F[fk])
    d.text(((W-tw)//2, y), text, fill=fill, font=F[fk])

def hdr(d, title, sub, t):
    d.rectangle((0, 0, W, 36), fill=t["pn"])
    fg = t.get("hdr_fg", C["w"])
    d.text((14, 6), title, fill=fg, font=F["b18"])
    if sub:
        tw = d.textlength(sub, font=F["r11"])
        d.text((W-14-tw, 10), sub, fill=C["dm"], font=F["r11"])

def dots(d, cur, total):
    dr, sp = 3, 12
    tw = total*(2*dr)+(total-1)*sp
    sx = (W-tw)//2
    for i in range(total):
        cx = sx + i*(2*dr+sp)
        col = C["w"] if i==cur else C["dm"]
        d.ellipse((cx-dr, H-12-dr, cx+dr, H-12+dr), fill=col)

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

# ══════════════════════════════════════════════════════════════════════
# Page: System — 3 arcs + uptime/load
# ══════════════════════════════════════════════════════════════════════

def pg_system(s):
    t = THEMES["system"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "SYSTEM", s.get("hostname","?"), t)

    # 3 arc rings — centered vertically in content area (y 36..296)
    gy = 120; cx = [80, 240, 400]; ri, ro = 32, 48
    rings = [
        (s.get("cpu",0)/100, C["cpu"], t["card"], f"{int(s.get('cpu',0))}%", "CPU"),
        (s.get("mem",0)/100, C["mem"], t["card"], f"{int(s.get('mem',0))}%", "MEM"),
        (s.get("disk",0)/100, C["disk"], t["card"], f"{int(s.get('disk',0))}%", "DISK"),
    ]
    for i, (pct, fg, bg, val, label) in enumerate(rings):
        x = cx[i]
        arc(d, x, gy, ri, ro, min(pct,1), fg, bg)
        # centered value inside ring
        tw = d.textlength(val, font=F["b22"])
        d.text((x-tw//2, gy-14), val, fill=fg, font=F["b22"])
        # label below ring
        tw2 = d.textlength(label, font=F["r11"])
        d.text((x-tw2//2, gy+ro+8), label, fill=C["dm"], font=F["r11"])

    # Bottom row: uptime + load
    by = 200
    card(d, 10, by, 226, 48, t["card"])
    d.text((20, by+6), "UPTIME", fill=C["dm"], font=F["r10"])
    d.text((20, by+24), s.get("uptime","?"), fill=C["gn"], font=F["b18"])

    card(d, 246, by, 224, 48, t["card"])
    d.text((256, by+6), "LOAD AVG", fill=C["dm"], font=F["r10"])
    ld = s.get("load",[0,0,0])
    d.text((256, by+24), f"{ld[0]:.2f}  {ld[1]:.2f}  {ld[2]:.2f}", fill=C["disk"], font=F["b14"])

    dots(d, 0, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: API Usage — 2 balance arcs + 3 stat cards
# ══════════════════════════════════════════════════════════════════════

def pg_apis(api):
    t = THEMES["ccswitch"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "API USAGE", "", t)

    # Two balance arcs
    gy = 118
    for i, (label, key, color) in enumerate([
        ("DeepSeek", "ds_balance", C["cyan"]),
        ("MiMo", "mm_balance", C["purple"]),
    ]):
        x = 130 + i*220
        bal = api.get(key, "?")
        try: val = float(bal)
        except: val = 0
        pct = min(val/50.0, 1.0) if val > 0 else 0
        arc(d, x, gy, 38, 56, pct, color, t["card"])
        vs = f"{val:.1f}" if isinstance(val, float) else str(val)
        tw = d.textlength(vs, font=F["b26"])
        d.text((x-tw//2, gy-16), vs, fill=color, font=F["b26"])
        tw2 = d.textlength("CNY", font=F["r10"])
        d.text((x-tw2//2, gy+14), "CNY", fill=C["dm"], font=F["r10"])
        tw3 = d.textlength(label, font=F["r11"])
        d.text((x-tw3//2, gy+48), label, fill=C["w"], font=F["r11"])

    # 3 stat cards
    y0 = 192
    cw = (W-40)//3
    items = [
        ("TOTAL", fmt_tk(api.get("total_tokens",0)), C["w"]),
        ("OUTPUT", fmt_tk(api.get("output_tokens",0)), C["gn"]),
        ("CACHE HIT", f"{api.get('cache_hit_rate',0):.1f}%", C["gold"]),
    ]
    for i, (lbl, val, col) in enumerate(items):
        x = 10 + i*(cw+10)
        card(d, x, y0, cw, 64, t["card"])
        d.text((x+10, y0+8), lbl, fill=C["dm"], font=F["r10"])
        d.text((x+10, y0+30), val, fill=col, font=F["b22"])

    dots(d, 1, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Clash — node + traffic + upload/download
# ══════════════════════════════════════════════════════════════════════

def pg_clash(cl):
    t = THEMES["clash"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    status = "ONLINE" if cl.get("running") else "OFFLINE"
    hdr(d, "CLASH", status, t)

    # Current node
    node = cl.get("current_node", "?")
    if len(node) > 28: node = node[:26]+".."
    card(d, 10, 44, W-20, 38, t["card"])
    d.text((20, 48), "NODE", fill=C["dm"], font=F["r10"])
    d.text((70, 46), node, fill=C["w"], font=F["b16"])

    # Traffic
    y0 = 92
    tu = cl.get("traffic_used", "")
    tt = cl.get("traffic_total", "")
    card(d, 10, y0, W-20, 36, t["card"])
    d.text((20, y0+4), "TRAFFIC", fill=C["dm"], font=F["r10"])
    if tu and tt:
        d.text((110, y0+4), f"{tu} / {tt}", fill=C["w"], font=F["b14"])
        try:
            num = float(''.join(c for c in tu.split()[0] if c.isdigit() or c=='.'))
            den = float(''.join(c for c in tt.split()[0] if c.isdigit() or c=='.'))
            tp = num/den if den>0 else 0
        except: tp = 0
        bar_h(d, 20, y0+24, W-40, 6, tp, C["rose"])
    else:
        d.text((110, y0+4), "N/A", fill=C["dm"], font=F["r14"])

    # Expire
    y1 = 138
    exp = cl.get("expire_date", "")
    card(d, 10, y1, W-20, 32, t["card"])
    d.text((20, y1+6), "EXPIRE", fill=C["dm"], font=F["r10"])
    d.text((100, y1+4), exp or "N/A", fill=C["gold"], font=F["b14"])

    # Upload + Download
    y2 = 182
    hw = (W-30)//2
    card(d, 10, y2, hw, 52, t["card"])
    d.text((20, y2+8), "UPLOAD", fill=C["dm"], font=F["r10"])
    d.text((20, y2+28), fmtb(cl.get("upload_total",0)), fill=C["cyan"], font=F["b18"])

    card(d, 20+hw, y2, hw, 52, t["card"])
    d.text((30+hw, y2+8), "DOWNLOAD", fill=C["dm"], font=F["r10"])
    d.text((30+hw, y2+28), fmtb(cl.get("download_total",0)), fill=C["rose"], font=F["b18"])

    # Connections + Mode
    y3 = 244
    card(d, 10, y3, hw, 34, t["card"])
    d.text((20, y3+6), "CONNECTIONS", fill=C["dm"], font=F["r10"])
    d.text((150, y3+4), str(cl.get("active_connections",0)), fill=C["w"], font=F["b16"])

    card(d, 20+hw, y3, hw, 34, t["card"])
    d.text((30+hw, y3+6), "MODE", fill=C["dm"], font=F["r10"])
    d.text((90+hw, y3+4), cl.get("mode","Rule"), fill=C["purple"], font=F["b16"])

    dots(d, 2, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Codex — 5h / 7d arc percentage + model + reset
# ══════════════════════════════════════════════════════════════════════

def pg_codex(cx):
    t = THEMES["codex"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CODEX", "", t)

    gy = 130
    for i, (label, key, mx, color) in enumerate([
        ("5 Hour", "tokens_5h", 2_000_000, C["purple"]),
        ("7 Day", "tokens_7d", 10_000_000, C["cyan"]),
    ]):
        x = 130 + i*220
        tok = cx.get(key, 0)
        pct = min(tok/mx, 1.0) if tok > 0 else 0
        arc(d, x, gy, 42, 62, pct, color, t["card"])
        vs = fmt_tk(tok)
        tw = d.textlength(vs, font=F["b24"])
        d.text((x-tw//2, gy-16), vs, fill=color, font=F["b24"])
        ms = f"/ {fmt_tk(mx)}"
        tw2 = d.textlength(ms, font=F["r10"])
        d.text((x-tw2//2, gy+14), ms, fill=C["dm"], font=F["r10"])
        tw3 = d.textlength(label, font=F["r12"])
        d.text((x-tw3//2, gy+50), label, fill=C["w"], font=F["r12"])

    # Model card
    y0 = 210
    card(d, 10, y0, W-20, 36, t["card"])
    d.text((20, y0+6), "MODEL", fill=C["dm"], font=F["r10"])
    model = cx.get("model", "?")
    if len(model) > 28: model = model[:26]+".."
    d.text((80, y0+6), model, fill=C["purple"], font=F["b14"])

    # Reset card
    y1 = 254
    card(d, 10, y1, W-20, 28, t["card"])
    d.text((20, y1+4), "RESET", fill=C["dm"], font=F["r10"])
    d.text((80, y1+3), cx.get("reset_time","?"), fill=C["gold"], font=F["r12"])

    dots(d, 3, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: Weather — temp arc + condition + forecast
# ══════════════════════════════════════════════════════════════════════

def pg_weather(w):
    t = THEMES["weather"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    date = w.get("date", "")
    dow = w.get("day_of_week", "")
    hdr(d, "WEATHER", f"{date} {dow}", t)

    # Temp arc (left half)
    temp_c = w.get("temp_c", 0)
    temp_pct = max(0, min(1, (temp_c+10)/55))
    cx1 = 115; gy = 120
    tc = C["gold"] if temp_c < 30 else C["dng"]
    arc(d, cx1, gy, 40, 58, temp_pct, tc, t["card"])
    vs = f"{int(temp_c)}°"
    tw = d.textlength(vs, font=F["b32"])
    d.text((cx1-tw//2, gy-18), vs, fill=C["w"], font=F["b32"])
    city = w.get("city", "?")
    tw2 = d.textlength(city, font=F["r12"])
    d.text((cx1-tw2//2, gy+44), city, fill=tc, font=F["r12"])

    # Right panel: condition + humidity + wind
    rx = 228
    card(d, rx, 44, 242, 40, t["card"])
    cond = w.get("condition", "?")
    if len(cond) > 22: cond = cond[:20]+".."
    d.text((rx+12, 52), cond, fill=C["w"], font=F["b16"])
    fl = w.get("feels_like_c", temp_c)
    d.text((rx+12, 70), f"Feels {int(fl)}°C", fill=C["dm"], font=F["r10"])

    card(d, rx, 92, 116, 34, t["card"])
    d.text((rx+10, 96), "HUMIDITY", fill=C["dm"], font=F["r10"])
    d.text((rx+10, 110), f"{w.get('humidity',0)}%", fill=C["cyan"], font=F["b14"])

    card(d, rx+126, 92, 116, 34, t["card"])
    d.text((rx+136, 96), "WIND", fill=C["dm"], font=F["r10"])
    d.text((rx+136, 110), f"{w.get('wind_kph',0)}km/h", fill=C["sky"], font=F["b14"])

    # 3-day forecast
    y0 = 138
    forecasts = w.get("forecast", [])[:3]
    fw = (W-40)//3
    for i, fc in enumerate(forecasts):
        fx = 10 + i*(fw+5)
        card(d, fx, y0, fw, 58, t["card"])
        day = fc.get("day","?")[:3]
        d.text((fx+8, y0+4), day, fill=C["dm"], font=F["r10"])
        hi = fc.get("high_c","?")
        lo = fc.get("low_c","?")
        d.text((fx+8, y0+20), f"{hi}°", fill=C["dng"], font=F["b16"])
        d.text((fx+50, y0+20), f"{lo}°", fill=C["sky"], font=F["b16"])
        c2 = fc.get("condition","?")
        if len(c2) > 12: c2 = c2[:10]+".."
        d.text((fx+8, y0+40), c2, fill=C["w"], font=F["r9"])

    # Sun + UV
    y1 = 206
    hw = (W-30)//2
    card(d, 10, y1, hw, 34, t["card"])
    d.text((20, y1+6), "SUN", fill=C["dm"], font=F["r10"])
    d.text((56, y1+5), f"{w.get('sunrise','?')}", fill=C["gold"], font=F["r12"])
    d.text((130, y1+5), f"{w.get('sunset','?')}", fill=C["gold"], font=F["r12"])

    card(d, 20+hw, y1, hw, 34, t["card"])
    d.text((30+hw, y1+6), "UV INDEX", fill=C["dm"], font=F["r10"])
    d.text((130+hw, y1+5), str(w.get("uv_index","?")), fill=C["rose"], font=F["b14"])

    dots(d, 4, 6)
    return img

# ══════════════════════════════════════════════════════════════════════
# Page: omLX — memory arc + stats + models
# ══════════════════════════════════════════════════════════════════════

def pg_omlx(om):
    t = THEMES["omlx"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    status = "ONLINE" if om.get("running") else "OFFLINE"
    hdr(d, "OMLX", status, t)

    # Memory arc (left)
    cx1 = 95; gy = 118
    mem_u = om.get("memory_used", 0)
    mem_c = om.get("memory_ceiling", 1)
    mem_p = mem_u/mem_c if mem_c > 0 else 0
    arc(d, cx1, gy, 34, 50, mem_p, C["gn"], t["card"])
    ms = f"{mem_u:.1f}"
    tw = d.textlength(ms, font=F["b22"])
    d.text((cx1-tw//2, gy-14), ms, fill=C["gn"], font=F["b22"])
    tw2 = d.textlength("GB", font=F["r10"])
    d.text((cx1-tw2//2, gy+8), "GB", fill=C["dm"], font=F["r10"])
    tw3 = d.textlength(f"/ {mem_c:.1f}", font=F["r9"])
    d.text((cx1-tw3//2, gy+42), f"/ {mem_c:.1f}", fill=C["dm"], font=F["r9"])

    # Right: loaded + requests
    rx = 190
    card(d, rx, 44, 280, 32, t["card"])
    d.text((rx+12, 48), "LOADED", fill=C["dm"], font=F["r10"])
    d.text((rx+80, 46), f"{om.get('loaded_count',0)} / {om.get('model_count',0)}", fill=C["gn"], font=F["b16"])
    d.text((rx+180, 48), "REQS", fill=C["dm"], font=F["r10"])
    d.text((rx+220, 46), fmt_tk(om.get("total_requests",0)), fill=C["w"], font=F["b16"])

    # Speed cards
    y0 = 84
    card(d, rx, y0, 136, 34, t["card"])
    d.text((rx+12, y0+4), "PROMPT", fill=C["dm"], font=F["r10"])
    d.text((rx+12, y0+18), f"{om.get('avg_prompt_speed',0):.1f} tk/s", fill=C["cyan"], font=F["b12"])

    card(d, rx+144, y0, 136, 34, t["card"])
    d.text((rx+156, y0+4), "GEN", fill=C["dm"], font=F["r10"])
    d.text((rx+156, y0+18), f"{om.get('avg_gen_speed',0):.1f} tk/s", fill=C["gold"], font=F["b12"])

    # Token summary
    y1 = 128
    cw3 = (W-40)//3
    for i, (lbl, val) in enumerate([
        ("PROMPT", fmt_tk(om.get("total_prompt_tk",0))),
        ("COMPL", fmt_tk(om.get("total_comp_tk",0))),
        ("CACHED", fmt_tk(om.get("total_cached_tk",0))),
    ]):
        x = 10 + i*(cw3+10)
        card(d, x, y1, cw3, 38, t["card"])
        d.text((x+8, y1+4), lbl, fill=C["dm"], font=F["r9"])
        d.text((x+8, y1+18), val, fill=C["w"], font=F["b14"])

    # Cache efficiency bar
    y2 = 176
    card(d, 10, y2, W-20, 24, t["card"])
    ce = om.get("cache_efficiency", 0)*100
    d.text((18, y2+4), "CACHE", fill=C["dm"], font=F["r10"])
    bar_h(d, 70, y2+7, W-90, 8, ce/100, C["gn"])
    d.text((W-56, y2+4), f"{ce:.0f}%", fill=C["gn"], font=F["r10"])

    # Top models (max 3 to keep readable)
    y3 = 210
    top = om.get("top_models", [])[:3]
    if top:
        max_tk = max((m.get("tk_total",1) for m in top), default=1)
        bar_h_each = 22
        colors = [C["gn"], C["cyan"], C["gold"]]
        for i, m in enumerate(top):
            by = y3 + i*bar_h_each
            tk = m.get("tk_total",0)
            pct = tk/max_tk if max_tk>0 else 0
            nm = m.get("name","?")
            if len(nm) > 22: nm = nm[:20]+".."
            d.text((14, by+4), nm, fill=C["dm"], font=F["r10"])
            bar_h(d, 160, by+5, W-210, 10, pct, colors[i%3])
            d.text((W-44, by+2), fmt_tk(tk), fill=C["w"], font=F["r10"])

    dots(d, 5, 6)
    return img


# ══════════════════════════════════════════════════════════════════════
# Renderer dispatch & networking
# ══════════════════════════════════════════════════════════════════════

RENDERERS = {
    "system": pg_system, "ccswitch": pg_apis, "clash": pg_clash,
    "codex": pg_codex, "weather": pg_weather, "omlx": pg_omlx,
}
DEFAULT_ORDER = ["system", "ccswitch", "clash", "codex", "weather", "omlx"]
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
    d.rectangle((0, 0, W, 36), fill=t["pn"])
    d.text((14, 6), "SideMon", fill=C["w"], font=F["b18"])
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
