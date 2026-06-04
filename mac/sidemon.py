#!/usr/bin/env python3
"""SideMon Mac sender — system / proxy / clash / codex / weather → Pi Zero W."""

import argparse, json, os, re, socket, sqlite3, subprocess, sys, time

try: import psutil, requests
except ImportError: print("pip3 install psutil requests"); sys.exit(1)

MIHOMO = "/tmp/verge/verge-mihomo.sock"
CODEX_DB = os.path.expanduser("~/.codex/state_5.sqlite")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")

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


# Node name translation map (Chinese → English display names)
_NODE_TRANS = {
    "香港": "Hong Kong", "台湾": "Taiwan", "新加坡": "Singapore",
    "日本": "Japan", "美国": "United States", "加拿大": "Canada",
    "英国": "United Kingdom", "德国": "Germany", "荷兰": "Netherlands",
    "意大利": "Italy", "西班牙": "Spain", "土耳其": "Turkey",
    "澳大利亚": "Australia", "阿根廷": "Argentina", "巴西": "Brazil",
    "智利": "Chile", "韩国": "Korea", "印度": "India",
    "以色列": "Israel", "泰国": "Thailand", "越南": "Vietnam",
    "马来西亚": "Malaysia", "南非": "South Africa",
    "约翰内斯堡": "Johannesburg",
}

def _translate_node(name):
    """Translate Chinese node names to English."""
    for cn, en in _NODE_TRANS.items():
        name = name.replace(cn, en)
    # Remove flag emojis (they're multi-codepoint)
    import re as _re
    name = _re.sub(r'[\U0001F1E0-\U0001F1FF]+', '', name).strip()
    # Clean up separators
    name = _re.sub(r'\s*\|\s*', ' | ', name)
    name = _re.sub(r'\s+', ' ', name).strip()
    return name

def get_ccswitch():
    global _ccswitch_cache
    now = time.time()
    if now - _ccswitch_cache["ts"] < 60 and _ccswitch_cache["data"]:
        return _ccswitch_cache["data"]

    data = {"provider": "Deepseek", "balance": "0", "currency": "CNY", "node": "Unknown"}
    try:
        # CC Switch status
        r = requests.get("http://127.0.0.1:15721/status", timeout=2)
        if r.ok:
            s = r.json()
            data["provider"] = s.get("current_provider", "Deepseek")
            data["success_rate"] = round(s.get("success_rate", 100), 1)
            data["total_requests"] = s.get("total_requests", 0)

        # DeepSeek balance
        key = _ccswitch_api_key()
        if key:
            r = requests.get("https://api.deepseek.com/user/balance",
                           headers={"Authorization": f"Bearer {key}"}, timeout=5)
            if r.ok:
                b = r.json()
                if b.get("is_available") and b.get("balance_infos"):
                    bi = b["balance_infos"][0]
                    data["balance"] = bi.get("total_balance", "0")
                    data["currency"] = bi.get("currency", "CNY")
                    data["topped_up"] = bi.get("topped_up_balance", "0")
                    data["granted"] = bi.get("granted_balance", "0")

        # Current node from Clash GLOBAL selector
        node = _mihomo_node()
        if node:
            data["node"] = node if node else "Unknown"
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
        return json.loads(r.stdout) if r.stdout else None
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
        "tokens_5h_pct": 0.0, "tokens_7d_pct": 0.0,
        "budget_5h": 200000, "budget_7d": 7000000,
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

    if result["budget_5h"] > 0:
        result["tokens_5h_pct"] = round(result["tokens_5h"] / result["budget_5h"] * 100, 1)
    if result["budget_7d"] > 0:
        result["tokens_7d_pct"] = round(result["tokens_7d"] / result["budget_7d"] * 100, 1)

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

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", "-H", default="192.168.1.24")
    p.add_argument("--port", "-P", type=int, default=9877)
    p.add_argument("--interval", "-i", type=float, default=1.0)
    p.add_argument("--once", "-1", action="store_true")
    args = p.parse_args()

    try: ip = socket.getaddrinfo(args.host, args.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except: print(f"Can't resolve {args.host}"); sys.exit(1)

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
            "ccswitch": get_ccswitch(), "clash": get_clash(),
            "codex": get_codex(), "weather": get_weather(),
            "omlx": get_omlx(),
        }
        send(payload)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print(f"SideMon → {args.host}:{args.port}  every {args.interval}s", flush=True)
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

            sysd = {
                "cpu": round(cpu,1), "mem": round(mp,1), "mem_total": round(mt),
                "disk": round(get_disk(),1), "load": get_load(),
                "net_rx": nr, "net_tx": nt,
                "uptime": get_uptime(), "hostname": socket.gethostname(),
            }
            t = get_temp()
            if t is not None: sysd["temp"] = round(t,1)

            payload = {"system": sysd, "proxy": get_proxy(), "clash": get_clash(),
                       "ccswitch": get_ccswitch(), "omlx": get_omlx()}
            if now - codex_last > 30:
                payload["codex"] = get_codex(); codex_last = now
            if now - weather_last > 600:
                payload["weather"] = get_weather(); weather_last = now

            send(payload)

            cn = payload.get("clash",{}).get("current_node","?")[:25]
            bal = payload.get("ccswitch",{}).get("balance","?")
            print(f"CPU:{cpu:5.1f}% MEM:{mp:5.1f}% DISK:{get_disk():5.1f}% "
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
