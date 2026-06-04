#!/usr/bin/env python3
"""SideMon PIL receiver — renders 6 dashboards to /dev/fb0"""
import socket, json, threading, time, os, sys, argparse, select, re
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
for s in [10,11,12,13,14,15,16,18,20,22,24,28,32,36,40,44,52]:
    F["r"+str(s)] = ff("r", s); F["b"+str(s)] = ff("b", s)

# ── Colors ──
C = {
    "bg": (8,10,18,255), "pn": (18,20,34,255), "w": (238,240,246,255),
    "gr": (136,140,158,255), "dm": (80,84,104,255),
    "cpu": (62,216,122), "cpu_bg": (12,32,20), "mem": (64,168,240), "mem_bg": (12,24,42),
    "disk": (240,150,40), "disk_bg": (40,24,8), "net": (38,192,164), "load": (240,192,32),
    "codex": (155,107,255), "codex_bg": (24,14,44),
    "gn": (62,216,122), "warn": (240,150,40), "dng": (240,84,68),
}

state = {}; lock = threading.Lock()

# ── Helpers ──
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
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1000: return f"{n/1000:.0f}K"
    return f"{n:.0f}"

def hdr(d, title, sub):
    d.rectangle((0,0,W,42), fill=C["pn"])
    d.text((16,6), title, fill=C["w"], font=F["b18"])
    tw = d.textlength(sub, font=F["r11"])
    d.text((W-16-tw, 10), sub, fill=C["dm"], font=F["r11"])

def dots(d, cur, total):
    dr, sp = 3, 12
    tw = total*(2*dr) + (total-1)*sp
    sx = (W-tw)//2
    for i in range(total):
        cx = sx + i*(2*dr+sp)
        col = C["w"] if i==cur else C["dm"]
        d.ellipse((cx-dr, H-16-dr, cx+dr, H-16+dr), fill=col)

def fmt_spd(n):
    if n >= 1<<30: return f"{n/(1<<30):.1f} GB"
    if n >= 1<<20: return f"{n/(1<<20):.1f} MB"
    if n >= 1<<10: return f"{n/(1<<10):.0f} KB"
    return f"{n:.0f} B"

# ── Page: System ──
def pg_system(s):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "System", s.get("hostname","?"))
    # Rings
    ry = 118; cx = [96, 240, 384]; ri, ro = 30, 44
    rings = [
        (s.get("cpu",0)/100, C["cpu"], C["cpu_bg"], f'{int(s.get("cpu",0))}%', "CPU"),
        (s.get("mem",0)/100, C["mem"], C["mem_bg"], f'{int(s.get("mem",0))}%', "MEM"),
        (s.get("disk",0)/100, C["disk"], C["disk_bg"], f'{int(s.get("disk",0))}%', "DISK"),
    ]
    for i,(pct,fg,bg,v,lb) in enumerate(rings):
        x = cx[i]; arc(d, x, ry, ri, ro, min(pct,1), fg, bg)
        tw = d.textlength(v, font=F["b28"])
        d.text((x-tw/2, ry-8), v, fill=C["w"], font=F["b28"])
        tw = d.textlength(lb, font=F["r12"])
        d.text((x-tw/2, ry+ro+12), lb, fill=C["dm"], font=F["r12"])

    # Load + temp panel
    py = 190
    rrect(d, (12, py, 226, py+72), 8, C["pn"])
    d.text((22, py+10), "LOAD", fill=C["dm"], font=F["r11"])
    ld = s.get("load",[0,0,0])
    d.text((22, py+30), f'{ld[0]:.2f}  {ld[1]:.2f}  {ld[2]:.2f}', fill=C["load"], font=F["b16"])
    t = s.get("temp",0); up = s.get("uptime","?")
    d.text((22, py+52), f'{int(t)}°C  ↑ {up}', fill=C["gr"], font=F["r12"])

    # Network panel
    rrect(d, (240, py, 468, py+72), 8, C["pn"])
    d.text((250, py+10), "NETWORK", fill=C["dm"], font=F["r11"])
    rx = s.get("net_rx",0); tx = s.get("net_tx",0)
    d.text((250, py+30), f'↓ {fmtb(rx)}/s', fill=C["net"], font=F["b16"])
    d.text((250, py+52), f'↑ {fmtb(tx)}/s', fill=C["net"], font=F["b16"])

    mp = s.get("mem",0); mt = s.get("mem_total",0)
    ug = mp*mt/100/1024; tg = mt/1024
    d.text((W//2, H-4), f'RAM: {ug:.1f} / {tg:.0f} GB', fill=C["dm"], font=F["r10"], anchor="mb")
    dots(d, 0, 6)
    return img

# ── Page: CC Switch ──
def pg_ccswitch(cc):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "CC Switch", cc.get("provider","Deepseek"))
    py = 52
    rrect(d, (12, py, W-12, py+76), 8, C["pn"])
    d.text((24, py+10), "BALANCE", fill=C["dm"], font=F["r12"])
    bal = f'{cc.get("balance","?")} {cc.get("currency","CNY")}'
    d.text((24, py+40), bal, fill=C["w"], font=F["b32"])

    py = 140
    rrect(d, (12, py, W-12, py+64), 8, C["pn"])
    d.text((24, py+10), "NODE", fill=C["dm"], font=F["r12"])
    n = str(cc.get("node","?"))[:38]
    d.text((24, py+34), n, fill=C["w"], font=F["r18"])

    py = 216
    rrect(d, (12, py, W-12, py+56), 8, C["pn"])
    d.text((24, py+10), "STATS", fill=C["dm"], font=F["r11"])
    req = cc.get("total_requests","?"); sr = cc.get("success_rate","?")
    d.text((24, py+32), f'{req} requests  |  {sr}% success', fill=C["gr"], font=F["r15"])
    dots(d, 1, 6)
    return img

# ── Page: Clash Verge ──
def pg_clash(cl):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "Clash", str(cl.get("current_node","?"))[:35])
    py = 52
    rrect(d, (12, py, W-12, py+64), 8, C["pn"])
    d.text((24, py+10), "TRAFFIC", fill=C["dm"], font=F["r12"])
    used = str(cl.get("traffic_used","?")); total = str(cl.get("traffic_total","?"))
    d.text((24, py+34), f'{used} / {total}', fill=C["w"], font=F["b22"])

    py = 128
    rrect(d, (12, py, W-12, py+66), 8, C["pn"])
    cw = (W-24-16)//3
    ul = cl.get("upload_total",0); dl = cl.get("download_total",0)
    stats = [
        ("UP", fmt_spd(ul), C["net"]),
        ("DOWN", fmt_spd(dl), C["gn"]),
        ("CONN", str(cl.get("active_connections","?")), C["warn"]),
    ]
    for i,(lb,v,col) in enumerate(stats):
        sx = 22 + i*cw
        d.text((sx, py+10), lb, fill=C["dm"], font=F["r11"])
        d.text((sx, py+36), v, fill=col, font=F["b16"])

    py = 208
    rrect(d, (12, py, W-12, py+54), 8, C["pn"])
    d.text((24, py+8), f'Expire: {cl.get("expire_date","?")}', fill=C["gr"], font=F["r14"])
    d.text((24, py+30), f'Mode: {cl.get("mode","?")}  |  v{cl.get("version","?")}', fill=C["dm"], font=F["r12"])
    dots(d, 2, 6)
    return img

# ── Page: Codex ──
def pg_codex(cx):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    hdr(d, "Codex", f'Reset: {cx.get("reset_time","?")}')

    py = 52
    rrect(d, (12, py, W-12, py+100), 8, C["pn"])
    hw = (W-24-8)//2
    p5 = min(cx.get("tokens_5h_pct", 0), 100)
    pw = min(cx.get("tokens_7d_pct", 0), 100)
    stats = [
        ("5H USAGE", f'{int(p5)}%', C["codex"]),
        ("WEEK USAGE", f'{int(pw)}%', C["warn"]),
    ]
    for i,(lb,v,col) in enumerate(stats):
        sx = 22 + i*hw
        d.text((sx, py+14), lb, fill=C["dm"], font=F["r12"])
        d.text((sx, py+46), v, fill=col, font=F["b36"])

    t5 = cx.get("tokens_5h",0); b5 = cx.get("budget_5h",200000)
    t7 = cx.get("tokens_7d",0); b7 = cx.get("budget_7d",7000000)
    d.text((22, py+80), f'5H: {fmtk(t5)}/{fmtk(b5)}  7D: {fmtk(t7)}/{fmtk(b7)}', fill=C["dm"], font=F["r10"])

    py = 164
    rrect(d, (12, py, W-12, py+52), 8, C["pn"])
    d.text((24, py+8), "MODEL", fill=C["dm"], font=F["r12"])
    d.text((24, py+28), str(cx.get("model","?"))[:38], fill=C["w"], font=F["b18"])

    py = 228
    rrect(d, (12, py, W-12, py+44), 8, C["pn"])
    d.text((24, py+8), "TOKENS USED", fill=C["dm"], font=F["r11"])
    d.text((24, py+26), f'5H: {fmtk(t5)}  |  7D: {fmtk(t7)}', fill=C["gr"], font=F["r15"])
    dots(d, 3, 6)
    return img

# ── Page: Weather ──
def pg_weather(w):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    now = time.localtime()
    hdr(d, "Weather", time.strftime("%m/%d %H:%M", now))

    py = 52
    rrect(d, (12, py, W-12, py+72), 8, C["pn"])
    d.text((24, py+10), f'{w.get("temp","?")}°C', fill=C["w"], font=F["b36"])
    d.text((140, py+12), str(w.get("desc","?")), fill=C["w"], font=F["r18"])
    d.text((140, py+40), f'Feels {w.get("feels","?")}  ·  {w.get("humidity","?")}', fill=C["gr"], font=F["r12"])

    py = 136
    rrect(d, (12, py, W-12, py+50), 8, C["pn"])
    d.text((24, py+10), "DETAILS", fill=C["dm"], font=F["r11"])
    hi = w.get("hi","?"); lo = w.get("lo","?")
    d.text((24, py+30), f'Wind: {w.get("wind","?")}  |  H:{hi}  L:{lo}', fill=C["gr"], font=F["r14"])

    py = 180
    rrect(d, (12, py, W-12, py+40), 8, C["pn"])
    d.text((W//2, py+10), f'{w.get("city","Guangzhou")}  ·  {time.strftime("%Y-%m-%d %H:%M", now)}', fill=C["dm"], font=F["r14"], anchor="ma")
    dots(d, 4, 6)
    return img

# ── Page: omLX ──
def pg_omlx(om):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    st = "Running" if om.get("running") else "Offline"
    hdr(d, "omLX", f'{st}  |  {om.get("default_model","?")[:25]}')

    py = 52
    rrect(d, (12, py, W-12, py+52), 8, C["pn"])
    cw = (W-24-16)//4
    stats = [
        ("REQUESTS", str(om.get("total_requests",0)), C["w"]),
        ("PROMPT TK", fmtk(om.get("total_prompt_tk",0)), C["net"]),
        ("COMP TK", fmtk(om.get("total_comp_tk",0)), C["gn"]),
        ("CACHED", f'{int(om.get("cache_efficiency",0)*100)}%', C["warn"]),
    ]
    for i,(lb,v,col) in enumerate(stats):
        sx = 22 + i*cw
        d.text((sx, py+8), lb, fill=C["dm"], font=F["r10"])
        d.text((sx, py+28), v, fill=col, font=F["b16"])

    py = 116
    rrect(d, (12, py, W-12, py+52), 8, C["pn"])
    d.text((24, py+8), "SPEED", fill=C["dm"], font=F["r12"])
    ps = om.get("avg_prompt_speed",0); gs = om.get("avg_gen_speed",0)
    d.text((24, py+28), f'Prompt: {fmtk(ps)}/s  |  Gen: {fmtk(gs)}/s', fill=C["net"], font=F["b16"])

    py = 180
    rrect(d, (12, py, W-12, py+52), 8, C["pn"])
    d.text((24, py+8), "MEMORY", fill=C["dm"], font=F["r12"])
    used = om.get("memory_used",0); ceil = om.get("memory_ceiling",1)
    mp = max(0, min(used/ceil if ceil>0 else 0, 1))
    # Progress bar
    bar_x, bar_w, bar_h = 130, 200, 16
    rrect(d, (bar_x, py+12, bar_x+bar_w, py+12+bar_h), 4, C["bg"])
    if mp > 0:
        rrect(d, (bar_x, py+12, bar_x+int(bar_w*mp), py+12+bar_h), 4, gcol(mp*100))
    d.text((bar_x+bar_w+16, py+20), f'{used:.1f} / {ceil:.1f} GB', fill=C["gr"], font=F["r11"])

    py = 246
    top = om.get("top_models",[])
    if top:
        names = []
        for m in top[:5]:
            if isinstance(m, dict):
                names.append(str(m.get("name","?")))
            else:
                names.append(str(m))
        d.text((W//2, py), "TOP MODELS: " + ", ".join(names), fill=C["dm"], font=F["r10"], anchor="ma")
    dots(d, 5, 6)
    return img

RENDERERS = {
    "system": pg_system, "ccswitch": pg_ccswitch,
    "clash": pg_clash, "codex": pg_codex,
    "weather": pg_weather, "omlx": pg_omlx,
}

# ── I/O ──
def write_fb(path, img):
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "BGRA"))

def show_waiting(fb_dev):
    img = Image.new("RGBA", (W,H), C["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0,0,W,42), fill=C["pn"])
    d.text((16,6), "SideMon", fill=C["w"], font=F["b18"])
    d.text((W//2, H//2-30), "Waiting for data...", fill=C["dm"], font=F["b20"], anchor="mm")
    d.text((W//2, H//2+10), "Connecting to Mac...", fill=C["gr"], font=F["r14"], anchor="mm")
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
    print(f"SideMon PIL :{args.port} → {args.fb}  {args.cycle}s × 6", file=sys.stderr)

    while True:
        conn, addr = srv.accept()
        print(f"Connected: {addr[0]}", file=sys.stderr)
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
