#!/usr/bin/env python3
"""SideMon Mac sender — system / proxy / clash / codex / weather → Pi Zero W."""

import argparse, datetime, json, os, re, socket, sqlite3, subprocess, sys, time

try: import psutil, requests
except ImportError: print("pip3 install psutil requests"); sys.exit(1)

MIHOMO = "/tmp/verge/verge-mihomo.sock"
CODEX_DB = os.path.expanduser("~/.codex/state_5.sqlite")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")

# API keys for balance queries
DEEPSEEK_KEY = "sk-b99b7bc5a9ab418a9a4a049730de95f9"
MINIMI_KEY = "sk-ci9tn1mcclqu2txx28ns8gijaar1yblqqno7kjwmyrkkla9m"
MINIMI_BASE = "https://api.xiaomimimo.com"  # XiaoMi Mimo API base URL

# ══════════════════════════════════════════════════════════════════════
# System
# ══════════════════════════════════════════════════════════════════════

def get_cpu(): return psutil.cpu_percent(interval=0.1)

def get_mem():
    m = psutil.virtual_memory()
    return m.percent, m.total / (1024 * 1024)

def get_disk(): return psutil.disk_usage("/").percent
def get_load(): return [round(l, 2) for l in os.getloadavg()]

def get_uptime():
    try:
        s = int(time.time() - psutil.boot_time())
        d, s = divmod(s, 86400); h, s = divmod(s, 3600); m = s // 60
        if d: return f"{d}d {h}h"
        if h: return f"{h}h {m}m"
        return f"{m}m"
    except: return "N/A"

def get_temp():
    try:
        r = subprocess.run(["osx-cpu-temp"], capture_output=True, text=True, timeout=3)
        m = re.search(r"([\d.]+)", r.stdout)
        if m: return float(m.group(1))
    except: pass
    return None

# ══════════════════════════════════════════════════════════════════════
# Proxy (macOS system proxy state)
# ══════════════════════════════════════════════════════════════════════

def get_proxy():
    try:
        r = subprocess.run(["scutil","--proxy"], capture_output=True, text=True, timeout=3)
        out = r.stdout
        on = "HTTPEnable : 1" in out
        m = re.search(r"HTTPProxy : (.+)", out); host = m.group(1).strip() if m else ""
        m = re.search(r"HTTPPort : (\d+)", out); port = int(m.group(1)) if m else 0
        return {"enabled": on, "host": host, "port": port}
    except:
        return {"enabled": False, "host": "", "port": 0}

# ══════════════════════════════════════════════════════════════════════
# CC Switch — DeepSeek balance
# ══════════════════════════════════════════════════════════════════════

def _ccswitch_api_key():
    """Extract DeepSeek API key from CC Switch settings."""
    try:
        db = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True, timeout=2)
        row = db.execute("SELECT value FROM settings WHERE key='common_config_openclaw'").fetchone()
        db.close()
        if row:
            m = re.search(r'apiKey["\s:]+["\']([^"\']+)', row[0])
            if m: return m.group(1)
    except: pass
    return None

_ccswitch_cache = {"ts": 0, "data": None}



def get_apis():
    global _ccswitch_cache
    now = time.time()
    if now - _ccswitch_cache["ts"] < 15 and _ccswitch_cache["data"]:
        return _ccswitch_cache["data"]

    data = {
        "ds_balance": "?", "ds_currency": "CNY",
        "mm_balance": "?", "mm_currency": "CNY",
        "node": "Unknown",
        "total_tokens": 0, "output_tokens": 0, "cache_hit_rate": 0,
    }

    # DeepSeek balance
    try:
        key = DEEPSEEK_KEY or _ccswitch_api_key()
        if key:
            r = requests.get("https://api.deepseek.com/user/balance",
                           headers={"Authorization": f"Bearer {key}"}, timeout=5)
            if r.ok:
                b = r.json()
                if b.get("is_available") and b.get("balance_infos"):
                    bi = b["balance_infos"][0]
                    data["ds_balance"] = bi.get("total_balance", "?")
                    data["ds_currency"] = bi.get("currency", "CNY")
    except: pass

    # MiniMi stats — from CC Switch DB (no public balance API)
    try:
        db3 = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True, timeout=2)
        today_ts = int(datetime.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        row = db3.execute(
                "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COUNT(*) "
                "FROM proxy_request_logs WHERE created_at>=? AND model LIKE '%mimo%'",
                (today_ts,)).fetchone()
        inp, out, cnt = row
        data["mm_balance"] = str(inp + out)  # total tokens
        data["mm_currency"] = f"{cnt} reqs"
        db3.close()
    except: pass

    # Daily token usage from CC Switch DB
    try:
        db2 = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True, timeout=2)
        today_ts = int(datetime.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        row = db2.execute(
                "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
                "COALESCE(SUM(cache_read_tokens),0),COALESCE(SUM(cache_creation_tokens),0),COUNT(*) "
                "FROM proxy_request_logs WHERE created_at>=?",
                (today_ts,)).fetchone()
        inp, out, cache_r, cache_c, cnt = row
        data["output_tokens"] = out
        data["total_tokens"] = inp + out
        data["cache_hit_rate"] = round(cache_r / inp * 100, 1) if inp > 0 else 0
        db2.close()
    except: pass

    # Current node from Clash
    try:
        node = _mihomo_node()
        if node:
            data["node"] = node.strip() if node else "Unknown"
    except: pass
    _ccswitch_cache = {"ts": now, "data": data}
    return data

# ══════════════════════════════════════════════════════════════════════
# Mihomo / Clash helpers
# ══════════════════════════════════════════════════════════════════════

def _mihomo(path):
    try:
        r = subprocess.run(["curl","-s","--max-time","2","--unix-socket",MIHOMO,
                           f"http://localhost{path}"], capture_output=True, text=True, timeout=3)
        if not r.stdout: return None
        # Take last JSON line (traffic endpoint may return multiple)
        lines = [l for l in r.stdout.strip().split("\n") if l.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None
    except: return None

def _mihomo_node():
    px = _mihomo("/proxies")
    if px:
        g = px.get("proxies", {}).get("GLOBAL", {})
        return g.get("now", None)
    return None

_clash_cache = {"ts": 0, "data": None}

def get_clash():
    global _clash_cache
    now = time.time()
    if now - _clash_cache["ts"] < 30 and _clash_cache["data"]:
        return _clash_cache["data"]

    d = {"running": False, "version": "", "mode": "Rule",
         "current_node": "Unknown", "traffic_used": "", "traffic_total": "",
         "expire_date": "", "upload_total": 0, "download_total": 0,
         "active_connections": 0}

    ver = _mihomo("/version")
    if not ver: return d
    d["running"] = True; d["version"] = ver.get("version", "")

    cfg = _mihomo("/configs")
    if cfg: d["mode"] = cfg.get("mode", "Rule")

    px = _mihomo("/proxies")
    if px:
        g = px.get("proxies", {}).get("GLOBAL", {})
        d["current_node"] = g.get("now", "Unknown")
        # Parse traffic and expire from proxy names
        all_names = g.get("all", [])
        for name in all_names:
            if name.startswith("Traffic:"):
                parts = name.replace("Traffic:", "").strip().split("/")
                if len(parts) >= 2:
                    d["traffic_used"] = parts[0].strip()
                    d["traffic_total"] = parts[1].strip()
            elif name.startswith("Expire:"):
                d["expire_date"] = name.replace("Expire:", "").strip()

    tr = _mihomo("/traffic")
    if tr:
        d["upload_total"] = tr.get("up", 0)
        d["download_total"] = tr.get("down", 0)

    conns = _mihomo("/connections")
    if conns:
        d["active_connections"] = len(conns.get("connections", []))

    _clash_cache = {"ts": now, "data": d}
    return d

# ══════════════════════════════════════════════════════════════════════
# Codex usage
# ══════════════════════════════════════════════════════════════════════

_codex_cache = {"ts": 0, "data": None}

def get_codex():
    global _codex_cache
    now = time.time()
    if now - _codex_cache["ts"] < 30 and _codex_cache["data"]:
        return _codex_cache["data"]

    result = {
        "tokens_5h": 0, "tokens_7d": 0,
        "reset_time": "", "model": "deepseek-v4-pro",
    }
    try:
        now_ms = int(time.time() * 1000)
        db = sqlite3.connect(f"file:{CODEX_DB}?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT COALESCE(SUM(tokens_used),0) as t FROM threads WHERE updated_at_ms >= ?",
                       (now_ms - 5*3600*1000,)).fetchone()
        result["tokens_5h"] = r["t"]
        r = db.execute("SELECT COALESCE(SUM(tokens_used),0) as t FROM threads WHERE updated_at_ms >= ?",
                       (now_ms - 7*86400*1000,)).fetchone()
        result["tokens_7d"] = r["t"]
        r = db.execute("SELECT model FROM threads ORDER BY updated_at_ms DESC LIMIT 1").fetchone()
        if r and r["model"]: result["model"] = r["model"]
        db.close()
    except: pass



    from datetime import datetime, timezone, timedelta
    utc = datetime.now(timezone.utc)
    rst = utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    result["reset_time"] = rst.strftime("%m/%d %H:%M UTC")

    _codex_cache = {"ts": now, "data": result}
    return result

# ══════════════════════════════════════════════════════════════════════
# Weather (wttr.in)
# ══════════════════════════════════════════════════════════════════════

_weather_cache = {"ts": 0, "data": None}


# ══════════════════════════════════════════════════════════════════════
# omLX — local LLM inference stats
# ══════════════════════════════════════════════════════════════════════

OMLX_STATS = os.path.expanduser("~/.omlx/stats.json")
OMLX_SETTINGS = os.path.expanduser("~/.omlx/settings.json")

def _read_omlx_stats():
    """Read omLX stats.json and health endpoint."""
    result = {"running": False, "default_model": "?", "model_count": 0,
              "loaded_count": 0, "total_requests": 0,
              "total_prompt_tk": 0, "total_comp_tk": 0, "total_cached_tk": 0,
              "cache_efficiency": 0, "avg_prompt_speed": 0, "avg_gen_speed": 0,
              "memory_used": 0, "memory_ceiling": 0, "top_models": []}
    try:
        # Check health
        r = requests.get("http://127.0.0.1:9876/health", timeout=3)
        if r.ok:
            h = r.json()
            result["running"] = h.get("status") == "healthy"
            result["default_model"] = h.get("default_model", "?")
            pool = h.get("engine_pool", {})
            result["model_count"] = pool.get("model_count", 0)
            result["loaded_count"] = pool.get("loaded_count", 0)
            result["memory_ceiling"] = pool.get("final_ceiling", 0) / (1024**3)
            result["memory_used"] = pool.get("current_model_memory", 0) / (1024**3)
    except:
        pass

    # Read stats.json
    try:
        with open(OMLX_STATS) as f:
            s = json.load(f)
        result["total_requests"] = s.get("total_requests", 0)
        result["total_prompt_tk"] = s.get("total_prompt_tokens", 0)
        result["total_comp_tk"] = s.get("total_completion_tokens", 0)
        result["total_cached_tk"] = s.get("total_cached_tokens", 0)
        # Cache efficiency
        total_tk = result["total_prompt_tk"]
        cached = result["total_cached_tk"]
        if total_tk > 0:
            result["cache_efficiency"] = cached / total_tk
        # Speed
        ppt = s.get("total_prefill_duration", 0)
        pgt = s.get("total_generation_duration", 0)
        if ppt > 0:
            result["avg_prompt_speed"] = result["total_prompt_tk"] / ppt
        if pgt > 0:
            result["avg_gen_speed"] = result["total_comp_tk"] / pgt
        # Top models by total tokens
        per_model = s.get("per_model", {})
        top = []
        for name, m in per_model.items():
            tk = m.get("prompt_tokens", 0) + m.get("completion_tokens", 0)
            # Shorten model name for display
            short = name.replace("-MLX-4bit","").replace("-MLX-8bit","").replace("-MLX-mxfp4","")
            short = short.replace("-mlx-lm-mxfp4","").replace("-nvfp4","").replace("-4bit","")
            short = short.replace("Claude-4.6-Opus-Distilled-","C4.6-")
            if len(short) > 30:
                short = short[:28] + ".."
            top.append({"name": short, "requests": m.get("requests", 0), "tk_total": tk})
        top.sort(key=lambda x: x["tk_total"], reverse=True)
        result["top_models"] = top[:5]
    except:
        pass

    return result

def get_omlx():
    return _read_omlx_stats()

def get_weather():
    global _weather_cache
    now = time.time()
    if now - _weather_cache["ts"] < 600 and _weather_cache["data"]:
        return _weather_cache["data"]

    data = {"temp": "", "desc": "", "humidity": "", "wind": "",
            "hi": "", "lo": "", "city": "Guangzhou"}
    try:
        r = requests.get("https://wttr.in/Guangzhou?format=j1", timeout=5)
        if r.ok:
            j = r.json()
            cc = j["current_condition"][0]
            data["temp"] = cc["temp_C"] + "°C"
            data["feels"] = cc["FeelsLikeC"] + "°C"
            data["humidity"] = cc["humidity"] + "%"
            data["desc"] = cc["weatherDesc"][0]["value"]
            data["wind"] = f"{cc['winddir16Point']} {cc['windspeedKmph']}km/h"
            # Today's forecast
            today = j["weather"][0]
            data["hi"] = today["maxtempC"] + "°"
            data["lo"] = today["mintempC"] + "°"
    except: pass

    _weather_cache = {"ts": now, "data": data}
    return data

# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# UDP Auto-Discovery
# ══════════════════════════════════════════════════════════════════════

DISCOVERY_PORT = 9878

def _discover_pi(tcp_port, wait_secs=8.0):
    """Listen for SideMon UDP broadcasts; try for wait_secs, return Pi IP or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", DISCOVERY_PORT))
    except OSError:
        s.close()
        print("Discovery port busy, skipping auto-discovery")
        return None
    s.settimeout(wait_secs)
    deadline = time.time() + wait_secs
    try:
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "sidemon":
                    port = msg.get("port", tcp_port)
                    print(f"Discovered SideMon Pi at {addr[0]}:{port}")
                    return addr[0]
            except socket.timeout:
                break
    except Exception as e:
        print(f"Discovery error: {e}")
    finally:
        s.close()
    print("No SideMon Pi found on LAN, falling back to --host")
    return None

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", "-H", default="192.168.1.24")
    p.add_argument("--port", "-P", type=int, default=9877)
    p.add_argument("--interval", "-i", type=float, default=1.0)
    p.add_argument("--once", "-1", action="store_true")
    p.add_argument("--ds-key", help="DeepSeek API key (or set DEEPSEEK_KEY env)")
    p.add_argument("--mm-key", help="MiniMi API key (or set MINIMI_KEY env)")
    p.add_argument("--mm-base", help="MiniMi API base URL")
    args = p.parse_args()

    global DEEPSEEK_KEY, MINIMI_KEY, MINIMI_BASE
    DEEPSEEK_KEY = args.ds_key or os.environ.get("DEEPSEEK_KEY")
    MINIMI_KEY = args.mm_key or os.environ.get("MINIMI_KEY")
    if args.mm_base:
        MINIMI_BASE = args.mm_base

    # ── UDP auto-discovery ──
    ip = _discover_pi(args.port)
    if ip is None:
        try:
            ip = socket.getaddrinfo(args.host, args.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            print(f"Using --host {args.host} ({ip})")
        except:
            print(f"Can't resolve {args.host}"); sys.exit(1)
    else:
        print(f"Discovered Pi at {ip}")

    def send(data):
        try:
            js = json.dumps(data, ensure_ascii=False) + "\n"
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2); s.connect((ip, args.port))
            s.sendall(js.encode("utf-8")); s.close()
        except: pass

    if args.once:
        cpu = get_cpu(); mp, mt = get_mem()
        sysd = {
            "cpu": round(cpu,1), "mem": round(mp,1), "mem_total": round(mt),
            "disk": round(get_disk(),1), "load": get_load(),
            "net_rx": 0, "net_tx": 0,
            "uptime": get_uptime(), "hostname": socket.gethostname(),
        }
        t = get_temp()
        if t is not None: sysd["temp"] = round(t,1)
        payload = {
            "system": sysd, "proxy": get_proxy(),
            "ccswitch": get_apis(), "clash": get_clash(),
            "codex": get_codex(), "weather": get_weather(),
            "omlx": get_omlx(),
        }
        send(payload)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print(f"SideMon → {ip}:{args.port}  every {args.interval}s", flush=True)
    prev_net = None; prev_t = None
    codex_last = 0; weather_last = 0

    while True:
        try:
            now = time.time()
            cpu = get_cpu(); mp, mt = get_mem()
            c = psutil.net_io_counters()

            if prev_net and prev_t:
                ela = now - prev_t
                nr = (c.bytes_recv - prev_net[0]) / ela if ela > 0 else 0
                nt = (c.bytes_sent - prev_net[1]) / ela if ela > 0 else 0
            else: nr, nt = 0, 0
            prev_net = (c.bytes_recv, c.bytes_sent); prev_t = now

            dk = round(get_disk(),1)
            sysd = {
                "cpu": round(cpu,1), "mem": round(mp,1), "mem_total": round(mt),
                "disk": dk, "load": get_load(),
                "net_rx": nr, "net_tx": nt,
                "uptime": get_uptime(), "hostname": socket.gethostname(),
            }

            payload = {"system": sysd, "proxy": get_proxy(), "clash": get_clash(),
                       "ccswitch": get_apis(), "omlx": get_omlx()}
            if now - codex_last > 30:
                payload["codex"] = get_codex(); codex_last = now
            if now - weather_last > 600:
                payload["weather"] = get_weather(); weather_last = now

            send(payload)

            cn = payload.get("clash",{}).get("current_node","?")[:25]
            bal = payload.get("ccswitch",{}).get("ds_balance","?")
            print(f"CPU:{cpu:5.1f}% MEM:{mp:5.1f}% DISK:{dk:5.1f}% "
                  f"LOAD:{sysd['load'][0]:.2f} Proxy:{'ON' if payload['proxy']['enabled'] else 'OFF'} "
                  f"Node:{cn} Bal:{bal}",
                  end="\r", flush=True)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nDone."); break
        except Exception as e:
            print(f"\nErr: {e}", flush=True); time.sleep(2)

if __name__ == "__main__":
    main()
