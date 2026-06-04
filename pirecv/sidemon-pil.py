#!/usr/bin/env python3
import socket, json, threading, time, os, sys, argparse, select, re
from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
FONT_DIR = "/usr/share/fonts/truetype"

def find_font(name, size):
    paths = {
        "reg": [
            f"{FONT_DIR}/piboto/Piboto-Regular.ttf",
            f"{FONT_DIR}/dejavu/DejaVuSans.ttf",
            f"{FONT_DIR}/liberation2/LiberationSans-Regular.ttf",
        ],
        "bold": [
            f"{FONT_DIR}/piboto/Piboto-Bold.ttf",
            f"{FONT_DIR}/dejavu/DejaVuSans-Bold.ttf",
            f"{FONT_DIR}/liberation2/LiberationSans-Bold.ttf",
        ],
    }
    for p in paths.get(name, paths["reg"]):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except:
                continue
    return ImageFont.load_default()

F = {}
for s in [10,11,12,13,14,15,16,18,20,22,24,28,32,36,40,44,52]:
    F["r"+str(s)] = find_font("reg", s)
    F["b"+str(s)] = find_font("bold", s)

# Colors
BG = (8,10,18,255); PANEL = (18,20,34,255); WHITE = (238,240,246,255)
GRAY = (136,140,158,255); DIM = (80,84,104,255)
CPU_C = (62,216,122); CPU_BG = (12,32,20); MEM_C = (64,168,240); MEM_BG = (12,24,42)
DISK_C = (240,150,40); DISK_BG = (40,24,8); NET_C = (38,192,164); LOAD_C = (240,192,32)
CODEX_C = (155,107,255); CODEX_BG = (24,14,44)
GREEN = (62,216,122); WARN_C = (240,150,40); DANGER = (240,84,68)

state = {}
lock = threading.Lock()

def rr(d, xy, r, fill):
    d.rounded_rectangle(xy, radius=r, fill=fill)

def draw_arc(d, cx, cy, ri, ro, pct, fg, bg):
    bbox = (cx-ro, cy-ro, cx+ro, cy+ro)
    d.ellipse(bbox, outline=bg, width=ro-ri)
    if pct > 0.001:
        start = 225
        end = start - 360*pct
        if end < 0: end += 360
        d.arc(bbox, start, end, fill=fg, width=ro-ri)

def gc(pct):
    if pct < 60: return GREEN
    if pct < 85: return WARN_C
    return DANGER

def fb(n):
    if n >= 1<<30: return f"{n/(1<<30):.1f} GB"
    if n >= 1<<20: return f"{n/(1<<20):.1f} MB"
    if n >= 1<<10: return f"{n/(1<<10):.0f} KB"
    return f"{n:.0f} B"

def ft(n):
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1000: return f"{n/1000:.0f}K"
    return f"{n:.0f}"

def hdr(d, t, s):
    d.rectangle((0,0,W,42), fill=PANEL)
    d.text((16,6), t, fill=WHITE, font=F["b18"])
    tw = d.textlength(s, font=F["r11"])
    d.text((W-16-tw, 10), s, fill=DIM, font=F["r11"])

def dots(d, cur, total):
    dr=3; sp=12
    tw = total*(2*dr)+(total-1)*sp
    sx = (W-tw)//2
    for i in range(total):
        cx = sx + i*(2*dr+sp)
        col = WHITE if i==cur else DIM
        d.ellipse((cx-dr, H-16-dr, cx+dr, H-16+dr), fill=col)

def write_fb(path, img):
    with open(path, "wb") as f:
        f.write(img.tobytes("raw", "BGRA"))

def render_system(s):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    hdr(d, "System", s.get("hostname","?"))
    ry, cx_list = 125, [96, 240, 384]
    ri, ro = 32, 46
    rings = [
        (s.get("cpu",0)/100, CPU_C, CPU_BG, str(int(s.get("cpu",0)))+"%", "CPU"),
        (s.get("mem",0)/100, MEM_C, MEM_BG, str(int(s.get("mem",0)))+"%", "MEM"),
        (s.get("disk",0)/100, DISK_C, DISK_BG, str(int(s.get("disk",0)))+"%", "DISK"),
    ]
    for i,(pct,fg,bg,v,lb) in enumerate(rings):
        cx = cx_list[i]
        draw_arc(d, cx, ry, ri, ro, min(pct,1), fg, bg)
        tw = d.textlength(v, font=F["b28"])
        d.text((cx-tw/2, ry-10), v, fill=WHITE, font=F["b28"])
        tw = d.textlength(lb, font=F["r12"])
        d.text((cx-tw/2, ry+ro+14), lb, fill=DIM, font=F["r12"])

    iy = 198
    rr(d, (12,iy,226,iy+68), 7, PANEL)
    d.text((22,iy+8), "LOAD", fill=DIM, font=F["r11"])
    ld = s.get("load",[0,0,0])
    d.text((22,iy+28), "{:.2f}  {:.2f}  {:.2f}".format(ld[0], ld[1], ld[2]), fill=LOAD_C, font=F["r16"])
    temp = s.get("temp",0); up = s.get("uptime","?")
    d.text((22,iy+48), str(int(temp)) + "\u00b0C  \u2191 " + str(up), fill=GRAY, font=F["r11"])

    rr(d, (240,iy,468,iy+68), 7, PANEL)
    d.text((250,iy+8), "NETWORK", fill=DIM, font=F["r11"])
    rx = s.get("net_rx",0); tx = s.get("net_tx",0)
    d.text((250,iy+28), "\u2193 " + fb(rx) + "/s", fill=NET_C, font=F["r15"])
    d.text((250,iy+48), "\u2191 " + fb(tx) + "/s", fill=NET_C, font=F["r15"])

    mp = s.get("mem",0); mt = s.get("mem_total",0)
    ug = mp*mt/100/1024; tg = mt/1024
    d.text((W//2, H-10), "RAM: {:.1f} / {:.0f} GB".format(ug, tg), fill=DIM, font=F["r10"], anchor="mb")
    dots(d, 0, 5)
    return img

def render_ccswitch(cc):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    hdr(d, "CC Switch", cc.get("provider","?"))
    by = 52
    rr(d, (12,by,W-12,by+72), 8, PANEL)
    d.text((24,by+10), "BALANCE", fill=DIM, font=F["r12"])
    d.text((24,by+40), "{} {}".format(cc.get("balance","?"), cc.get("currency","?")), fill=WHITE, font=F["b32"])
    ny = 136
    rr(d, (12,ny,W-12,ny+60), 8, PANEL)
    d.text((24,ny+10), "NODE", fill=DIM, font=F["r12"])
    node = cc.get("node","?")[:30]
    d.text((24,ny+34), node, fill=WHITE, font=F["r18"])
    my = 208
    rr(d, (12,my,W-12,my+54), 8, PANEL)
    d.text((24,my+10), "ACTIVE CONNECTIONS", fill=DIM, font=F["r11"])
    d.text((24,my+30), str(cc.get("total_requests","?")) + " requests  |  " + str(cc.get("success_rate","?")) + "% success", fill=GRAY, font=F["r14"])
    dots(d, 1, 5)
    return img

def fmt_speed(n):
    if n >= 1<<30: return f"{n/(1<<30):.1f} GB"
    if n >= 1<<20: return f"{n/(1<<20):.1f} MB"
    if n >= 1<<10: return f"{n/(1<<10):.0f} KB"
    return f"{n:.0f} B"

def render_clash(cl):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    hdr(d, "Clash Verge", "Mode: " + str(cl.get("mode","?")))
    by = 52
    rr(d, (12,by,W-12,by+64), 8, PANEL)
    d.text((24,by+8), "NODE", fill=DIM, font=F["r12"])
    d.text((24,by+32), str(cl.get("node","?"))[:30], fill=WHITE, font=F["b20"])
    ny = 126
    rr(d, (12,ny,W-12,ny+66), 8, PANEL)
    cw = (W-24-16)/3
    stats = [
        ("UP", fmt_speed(cl.get("upload",0))+"/s", NET_C),
        ("DOWN", fmt_speed(cl.get("download",0))+"/s", GREEN),
        ("TOTAL", fmt_speed(cl.get("total",0)), WARN_C),
    ]
    for i,(lb,v,col) in enumerate(stats):
        sx = 22 + i*cw
        d.text((sx, ny+10), lb, fill=DIM, font=F["r11"])
        d.text((sx, ny+38), v, fill=col, font=F["b16"])
    ey = 206
    rr(d, (12,ey,W-12,ey+50), 8, PANEL)
    d.text((24,ey+8), "EXPIRE: " + str(cl.get("expire","?")), fill=GRAY, font=F["r13"])
    d.text((24,ey+28), "UPDATE: " + str(cl.get("updated","?")), fill=DIM, font=F["r12"])
    dots(d, 2, 5)
    return img

def render_codex(cx):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    hdr(d, "Codex", "Reset: " + str(cx.get("reset_time","?")))
    sy = 52
    rr(d, (12,sy,W-12,sy+96), 8, PANEL)
    hw = (W-24-8)/2
    p5 = cx.get("tokens_5h_pct", 0)
    pw = cx.get("tokens_7d_pct", 0)
    stats = [
        ("5H USAGE", str(int(p5))+"%", CODEX_C),
        ("WEEK USAGE", str(int(pw))+"%", WARN_C),
    ]
    for i,(lb,v,col) in enumerate(stats):
        sx = 22 + i*hw
        d.text((sx, sy+14), lb, fill=DIM, font=F["r12"])
        d.text((sx, sy+46), v, fill=col, font=F["b36"])
    d.text((22, sy+78), "Reset: " + str(cx.get("reset_time","?")), fill=DIM, font=F["r10"])
    by = 162
    rr(d, (12,by,W-12,by+50), 8, PANEL)
    d.text((24,by+8), "MODEL", fill=DIM, font=F["r12"])
    d.text((24,by+30), str(cx.get("model","?"))[:30], fill=WHITE, font=F["b18"])
    ny = 224
    rr(d, (12,ny,W-12,ny+48), 8, PANEL)
    d.text((24,ny+8), "TOKENS", fill=DIM, font=F["r11"])
    d.text((24,ny+28), "5H: " + ft(cx.get("tokens_5h",0)) + "  7D: " + ft(cx.get("tokens_7d",0)), fill=GRAY, font=F["r15"])
    dots(d, 3, 5)
    return img

def render_weather(w):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    now = time.localtime()
    tstr = time.strftime("%H:%M:%S", now)
    hdr(d, "Weather", tstr)
    wy = 52
    rr(d, (12,wy,W-12,wy+68), 8, PANEL)
    d.text((24,wy+10), str(w.get("temp","?")) + "\u00b0C", fill=WHITE, font=F["b36"])
    d.text((130,wy+10), w.get("desc","?"), fill=WHITE, font=F["r18"])
    d.text((130,wy+38), "Feels " + str(w.get("feels","?")) + "  Humid " + str(w.get("humidity","?")), fill=GRAY, font=F["r11"])
    wd = 246
    rr(d, (12,wd,W-12,wd+38), 8, PANEL)
    d.text((24,wd+10), "Wind: " + str(w.get("wind","?")), fill=GRAY, font=F["r13"])
    dots(d, 4, 5)
    return img

RENDERERS = {
    "system": render_system, "ccswitch": render_ccswitch,
    "clash": render_clash, "codex": render_codex,
    "weather": render_weather,
}

def show_waiting(fb_dev):
    img = Image.new("RGBA", (W,H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0,0,W,42), fill=PANEL)
    d.text((16,6), "SideMon", fill=WHITE, font=F["b18"])
    d.text((W//2, H//2 - 30), "Waiting for data...", fill=DIM, font=F["b20"], anchor="mm")
    d.text((W//2, H//2 + 10), "Connecting to Mac...", fill=GRAY, font=F["r14"], anchor="mm")
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
    order = ["system", "ccswitch", "clash", "codex", "weather"]
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
    print(f"SideMon PIL fb :{args.port} -> {args.fb}, {args.cycle}s, 5 pages", file=sys.stderr)

    while True:
        conn, addr = srv.accept()
        print(f"Connected: {addr[0]}", file=sys.stderr)
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
