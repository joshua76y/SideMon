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
for s in [9,10,11,12,13,14,15,16,18,20,22,24,26,28,30,32,36,40,44,48,52,60]:
    F["r"+str(s)] = ff("r", s); F["b"+str(s)] = ff("b", s)

C = {
    "w": (238,240,246,255), "gr": (136,140,158,255), "dm": (90,94,114,255),
    "cpu": (62,216,122), "mem": (64,168,240), "disk": (240,150,40),
    "net": (38,192,164), "load": (240,192,32),
    "codex": (155,107,255), "gn": (62,216,122), "warn": (240,150,40), "dng": (240,84,68),
}

THEMES = {
    "system":   {"bg": (8, 10, 18, 255),   "pn": (18, 20, 34, 255),
                 "cpu_bg": (12, 32, 20), "mem_bg": (12, 24, 42), "disk_bg": (40, 24, 8)},
    "ccswitch": {"bg": (4, 12, 16, 255),  "pn": (14, 26, 32, 255)},
    "clash":    {"bg": (16, 6, 16, 255),  "pn": (30, 14, 30, 255)},
    "codex":    {"bg": (20, 12, 28, 255), "pn": (32, 22, 42, 255), "codex_bg": (24, 14, 44)},
    "weather":  {"bg": (6, 10, 28, 255),  "pn": (16, 22, 44, 255),
                 "card": (20, 28, 54, 255), "hdr_bg": (12, 18, 38, 255)},
    "omlx":     {"bg": (12, 18, 8, 255),  "pn": (22, 34, 18, 255)},
}

state = {}; lock = threading.Lock()

def rrect(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def arc(d, cx, cy, ri, ro, pct, fg, bgc):
    bb = (cx-ro, cy-ro, cx+ro, cy+ro)
    d.ellipse(bb, outline=bgc, width=ro-ri)
    if pct > 0.001:
        s = 225; e = s - 360*pct
        if e < 0: e += 360
        d.arc(bb, s, e, fill=fg, width=ro-ri)

def gcol(p):
    if p < 60: return C["gn"]
    if p < 85: return C["warn"]
    return C["dng"]

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
    d.text((16, 8), title, fill=C["w"], font=F["b20"])
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

# ── Page: System ──
def pg_system(s):
    t = THEMES["system"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "SYSTEM", s.get("hostname", "?"), t)

    ry = 130; cx = [96, 240, 384]; ri, ro = 35, 50
    rings = [
        (s.get("cpu", 0)/100,  C["cpu"],  t["cpu_bg"],  f"{int(s.get('cpu',0))}", "%"),
        (s.get("mem", 0)/100,  C["mem"],  t["mem_bg"],  f"{int(s.get('mem',0))}", "%"),
        (s.get("disk", 0)/100, C["disk"], t["disk_bg"], f"{int(s.get('disk',0))}", "%"),
    ]
    for i, (pct, fg, bg, val, suf) in enumerate(rings):
        x = cx[i]
        # Draw arc ring
        arc(d, x, ry, ri, ro, min(pct, 1), fg, bg)
        # Main number — centered in ring using anchor="mm"
        d.text((x, ry-2), val, fill=C["w"], font=F["b36"], anchor="mm")
        # Suffix "%" — small, placed to the right of the number, same baseline
        tw = d.textlength(val, font=F["b36"])
        d.text((x + tw//2 + 4, ry+4), suf, fill=C["dm"], font=F["b18"], anchor="lm")

    by = 200
    rrect(d, (12, by, W-12, by+60), 8, t["pn"])
    ld = s.get("load", [0,0,0]); rx = s.get("net_rx", 0); tx = s.get("net_tx", 0)
    temp = s.get("temp", 0); up = s.get("uptime", "?")
    cw = (W-24)//4
    items = [
        ("LOAD", f"{ld[0]:.1f} {ld[1]:.1f} {ld[2]:.1f}", C["load"]),
        ("NET",  f"↓{fmtb(rx)} ↑{fmtb(tx)}", C["net"]),
        ("TEMP", f"{int(temp)}°C", C["dng"] if temp > 80 else C["gn"]),
        ("UP",   str(up), C["gr"]),
    ]
    for i, (lb, v, col) in enumerate(items):
        sx = 18 + i*cw
        d.text((sx, by+8), lb, fill=C["dm"], font=F["r11"])
        fnt = F["r13"] if len(v) > 12 else F["b15"]
        d.text((sx, by+30), v, fill=col, font=fnt)

    dots(d, 0, 6)
    return img

# ── Page: CC Switch ──
def pg_ccswitch(cc):
    t = THEMES["ccswitch"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CC SWITCH", "DeepSeek", t)
    bal = f'{cc.get("balance", "?")}'
    cur = cc.get("currency", "CNY")
    d.text((W//2, 58), bal, fill=C["w"], font=F["b52"], anchor="ma")
    d.text((W//2, 112), f'{cur} remaining', fill=C["gr"], font=F["r16"], anchor="ma")
    n = str(cc.get("current_node", "?"))[:35]
    rrect(d, (40, 138, W-40, 198), 8, t["pn"])
    d.text((W//2, 150), "CURRENT NODE", fill=C["dm"], font=F["r12"], anchor="ma")
    d.text((W//2, 174), n, fill=C["w"], font=F["b18"], anchor="ma")
    rrect(d, (40, 210, W-40, 270), 8, t["pn"])
    req = cc.get("total_requests", "?"); sr = cc.get("success_rate", "?")
    d.text((W//2, 222), "TOTAL REQUESTS", fill=C["dm"], font=F["r12"], anchor="ma")
    d.text((W//2, 248), f'{req}  ·  {sr}% success', fill=C["gr"], font=F["b16"], anchor="ma")
    dots(d, 1, 6)
    return img

# ── Page: Clash ──
def pg_clash(cl):
    t = THEMES["clash"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    node = str(cl.get("current_node", "?"))[:22]
    hdr(d, "CLASH", node, t)
    used_s = cl.get("traffic_used", "0 GB")
    total_s = cl.get("traffic_total", "?")
    rrect(d, (30, 54, W-30, 112), 8, t["pn"])
    d.text((W//2, 68), "TRAFFIC", fill=C["dm"], font=F["r12"], anchor="ma")
    d.text((W//2, 90), f'{used_s}  /  {total_s}', fill=C["w"], font=F["b24"], anchor="ma")
    rrect(d, (30, 124, W-30, 188), 8, t["pn"])
    cw = (W-60)//3
    ul = cl.get("upload_total", 0); dl = cl.get("download_total", 0)
    items = [
        ("UP",   fmtb(ul), C["net"]),
        ("DOWN", fmtb(dl), C["gn"]),
        ("CONN", str(cl.get("active_connections", "?")), C["warn"]),
    ]
    for i, (lb, v, col) in enumerate(items):
        sx = 36 + i*cw
        d.text((sx, 134), lb, fill=C["dm"], font=F["r12"])
        d.text((sx, 160), v, fill=col, font=F["b20"])
    rrect(d, (30, 200, W-30, 254), 8, t["pn"])
    exp = cl.get("expire_date", "?"); ver = cl.get("version", "?")
    mode = str(cl.get("mode", "?")).upper()
    d.text((W//2, 210), f'{mode}  ·  Expires {exp}  ·  v{ver}',
           fill=C["gr"], font=F["r14"], anchor="ma")
    d.text((W//2, 234), f'Connections: {cl.get("active_connections", "?")}',
           fill=C["dm"], font=F["r13"], anchor="ma")
    dots(d, 2, 6)
    return img

# ── Page: Codex ──
def pg_codex(cx):
    t = THEMES["codex"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CODEX", f'Reset {cx.get("reset_time", "?")}', t)
    py = 58
    rrect(d, (20, py, W-20, py+110), 8, t["pn"])
    hw = (W-40)//2
    p5 = min(cx.get("tokens_5h_pct", 0), 100)
    pw = min(cx.get("tokens_7d_pct", 0), 100)
    items = [
        ("5-HOUR", f'{int(p5)}%', C["codex"] if p5 < 85 else C["dng"]),
        ("7-DAY",  f'{int(pw)}%', C["warn"] if pw < 85 else C["dng"]),
    ]
    for i, (lb, v, col) in enumerate(items):
        sx = 30 + i*hw
        d.text((sx, py+10), lb, fill=C["dm"], font=F["r14"])
        d.text((sx, py+34), v, fill=col, font=F["b44"])
    bar_x, bar_w = 30, hw-20
    for i, (pct, col) in enumerate([(p5, C["codex"]), (pw, C["warn"])]):
        bx = 30 + i*hw
        rrect(d, (bx, py+86, bx+bar_w, py+96), 3, t["bg"])
        if pct > 0:
            rrect(d, (bx, py+86, bx+int(bar_w*pct/100), py+96), 3, col)
    py = 182
    rrect(d, (20, py, W-20, py+52), 8, t["pn"])
    m = str(cx.get("model", "?"))[:38]
    t5 = cx.get("tokens_5h", 0); t7 = cx.get("tokens_7d", 0)
    d.text((30, py+8), m, fill=C["w"], font=F["b18"])
    d.text((30, py+34), f'5H:{fmtk(t5)}  7D:{fmtk(t7)}', fill=C["gr"], font=F["r14"])
    dots(d, 3, 6)
    return img

# ── Page: Weather ──
def pg_weather(w):
    t = THEMES["weather"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 42), fill=t["hdr_bg"])
    city = w.get("city", "Guangzhou")
    d.text((16, 8), city.upper(), fill=C["w"], font=F["b20"])
    full_date = time.strftime("%Y-%m-%d  %A")
    tw = d.textlength(full_date, font=F["r14"])
    d.text((W-16-tw, 10), full_date, fill=C["dm"], font=F["r14"])
    temp_val = w.get("temp", "--°C").replace("°C", "")
    desc = w.get("desc", "?")
    time_str = time.strftime("%H:%M")
    d.text((24, 50), f'{temp_val}°', fill=C["w"], font=F["b60"])
    d.text((26, 112), desc, fill=C["gr"], font=F["b16"])
    d.text((26, 135), time_str, fill=(190, 205, 235, 255), font=F["b30"])
    d.text((26, 168), time.strftime("%m/%d %A"), fill=C["dm"], font=F["r15"])
    rx, ry0 = 195, 50
    cw, ch = 130, 64
    gap = 8
    stats = [
        ("HUMIDITY",  w.get("humidity", "?%"),    (100, 175, 255)),
        ("FEELS LIKE", w.get("feels", "--°C"),     (240, 90, 74)),
        ("WIND",      w.get("wind", "?"),         (130, 200, 175)),
        ("HI / LO",   f'{w.get("hi","--")}  {w.get("lo","--")}', (240, 150, 40)),
    ]
    for idx, (label, value, accent) in enumerate(stats):
        col = idx % 2
        row = idx // 2
        cx_card = rx + col*(cw+gap)
        cy_card = ry0 + row*(ch+gap)
        rrect(d, (cx_card, cy_card, cx_card+cw, cy_card+ch), 6, t["card"])
        d.rectangle((cx_card+2, cy_card+8, cx_card+6, cy_card+ch-8), fill=accent)
        d.text((cx_card+14, cy_card+7), label, fill=C["dm"], font=F["r10"])
        d.text((cx_card+14, cy_card+30), value, fill=accent, font=F["b20"])
    by = 258
    rrect(d, (20, by, W-20, by+36), 8, t["card"])
    d.text((W//2, by+9), city, fill=C["dm"], font=F["r13"], anchor="ma")
    dots(d, 4, 6)
    return img

# ── Page: omLX ──
def pg_omlx(om):
    t = THEMES["omlx"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    st = "Running" if om.get("running") else "Offline"
    hdr(d, "omLX", st, t)
    py = 54
    rrect(d, (14, py, W-14, py+64), 8, t["pn"])
    cw = (W-32)//4
    items = [
        ("REQUESTS",   str(om.get("total_requests", 0)),        C["w"]),
        ("PROMPT",     fmtk(om.get("total_prompt_tk", 0)),      C["net"]),
        ("COMPLETION", fmtk(om.get("total_comp_tk", 0)),        C["gn"]),
        ("CACHE",      f'{int(om.get("cache_efficiency",0)*100)}%', C["warn"]),
    ]
    for i, (lb, v, col) in enumerate(items):
        sx = 20 + i*cw
        d.text((sx, py+10), lb, fill=C["dm"], font=F["r11"])
        d.text((sx, py+34), v, fill=col, font=F["b20"])
    py = 130
    ps = om.get("avg_prompt_speed", 0); gs = om.get("avg_gen_speed", 0)
    rrect(d, (14, py, W-14, py+52), 8, t["pn"])
    d.text((24, py+10), "SPEED", fill=C["dm"], font=F["r12"])
    d.text((24, py+28), f'Prompt {fmtk(ps)}/s  ·  Gen {fmtk(gs)}/s',
           fill=C["net"], font=F["b18"])
    py = 194
    rrect(d, (14, py, W-14, py+54), 8, t["pn"])
    d.text((24, py+10), "MEMORY", fill=C["dm"], font=F["r12"])
    used = om.get("memory_used", 0); ceil = om.get("memory_ceiling", 1)
    mp = max(0, min(used/ceil if ceil>0 else 0, 1))
    bar_x, bar_w, bar_h = 24, 260, 16
    rrect(d, (bar_x, py+24, bar_x+bar_w, py+24+bar_h), 4, t["bg"])
    if mp > 0:
        rrect(d, (bar_x, py+24, bar_x+int(bar_w*mp), py+24+bar_h), 4, gcol(mp*100))
    d.text((bar_x+bar_w+12, py+32), f'{used:.1f}/{ceil:.1f} GB',
           fill=C["gr"], font=F["r13"])
    dots(d, 5, 6)
    return img

RENDERERS = {
    "system": pg_system, "ccswitch": pg_ccswitch,
    "clash": pg_clash, "codex": pg_codex,
    "weather": pg_weather, "omlx": pg_omlx,
}

def write_fb(path, img):
    img = img.transpose(Image.ROTATE_180)
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "BGRA"))

def show_waiting(fb_dev):
    t = THEMES["system"]
    img = Image.new("RGBA", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 42), fill=t["pn"])
    d.text((16, 8), "SideMon", fill=C["w"], font=F["b20"])
    d.text((W//2, H//2-30), "Waiting...", fill=C["dm"], font=F["b24"], anchor="mm")
    d.text((W//2, H//2+10), "Mac -> Pi", fill=C["gr"], font=F["r16"], anchor="mm")
    write_fb(fb_dev, img)

def handle_client(conn):
    buf = b""
    while True:
        try:
            r, _, _ = select.select([conn], [], [], 1.0)
            if not r: continue
            data = conn.recv(65536)
            if not data: break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip(): continue
                try:
                    payload = json.loads(line.decode("utf-8"))
                    with lock:
                        for key in RENDERERS:
                            if key in payload and payload[key] is not None:
                                state[key] = payload[key]
                except Exception as e:
                    print(f"Parse: {e}", file=sys.stderr)
        except Exception:
            break
    conn.close()

def page_cycler(fb_dev, cycle_secs):
    order = ["system", "ccswitch", "clash", "codex", "weather", "omlx"]
    page = 0
    while True:
        with lock:
            cur = dict(state)
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
