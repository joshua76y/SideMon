#!/usr/bin/env python3
"""SideMon Mac sender — system / proxy / clash / codex / weather → Pi Zero W."""

import argparse, copy, datetime, json, os, re, socket, sqlite3, subprocess, sys, threading, time

try: import psutil, requests
except ImportError: print("pip3 install psutil requests"); sys.exit(1)

APP_NAME = "RpiZeroMon"
CONFIG_DIR = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_PAGES = ["system", "ccswitch", "clash", "codex", "weather", "omlx"]
PAGE_LABELS = {
    "system": "System",
    "ccswitch": "API Usage",
    "clash": "Clash",
    "codex": "Codex",
    "weather": "Weather",
    "omlx": "oMLX",
}

MIHOMO = "/tmp/verge/verge-mihomo.sock"
CODEX_DB = os.path.expanduser("~/.codex/state_5.sqlite")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")
DEEPSEEK_KEY = ""
MINIMI_KEY = ""
MINIMI_BASE = "https://api.xiaomimimo.com"
WEATHER_CITY = "Guangzhou"
OMLX_HEALTH_URL = "http://127.0.0.1:9876/health"
OMLX_STATS = os.path.expanduser("~/.omlx/stats.json")


def default_config():
    return {
        "host": "192.168.1.37",
        "port": 9877,
        "interval": 1.0,
        "pages": list(DEFAULT_PAGES),
        "deepseek_key": "",
        "mimo_key": "",
        "mimo_base": "https://api.xiaomimimo.com",
        "ccswitch_db": "~/.cc-switch/cc-switch.db",
        "mihomo_socket": "/tmp/verge/verge-mihomo.sock",
        "codex_db": "~/.codex/state_5.sqlite",
        "weather_city": "Guangzhou",
        "omlx_health_url": "http://127.0.0.1:9876/health",
        "omlx_stats": "~/.omlx/stats.json",
    }


def normalize_page_list(pages):
    ordered = []
    seen = set()
    for page in pages or []:
        if page in PAGE_LABELS and page not in seen:
            ordered.append(page)
            seen.add(page)
    return ordered or list(DEFAULT_PAGES)


def reorder_page_list(order, source_index, drop_row):
    items = list(order)
    if source_index < 0 or source_index >= len(items):
        return items
    item = items.pop(source_index)
    if drop_row > source_index:
        drop_row -= 1
    drop_row = max(0, min(drop_row, len(items)))
    items.insert(drop_row, item)
    return items


def _coerce_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_config(raw):
    cfg = default_config()
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["host"] = str(cfg.get("host") or default_config()["host"]).strip() or default_config()["host"]
    cfg["port"] = max(1, min(65535, _coerce_int(cfg.get("port"), default_config()["port"])))
    cfg["interval"] = max(0.2, _coerce_float(cfg.get("interval"), default_config()["interval"]))
    cfg["pages"] = normalize_page_list(cfg.get("pages"))
    for key in [
        "deepseek_key", "mimo_key", "mimo_base", "ccswitch_db", "mihomo_socket",
        "codex_db", "weather_city", "omlx_health_url", "omlx_stats",
    ]:
        cfg[key] = str(cfg.get(key, default_config().get(key, "")) or "").strip()
    return cfg


def load_config(path=CONFIG_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return normalize_config(json.load(f))
    except FileNotFoundError:
        return default_config()
    except Exception as e:
        print(f"Config load error: {e}", flush=True)
        return default_config()


def save_config(cfg, path=CONFIG_FILE):
    cfg = normalize_config(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


def apply_runtime_config(cfg):
    global MIHOMO, CODEX_DB, CCSWITCH_DB, DEEPSEEK_KEY, MINIMI_KEY, MINIMI_BASE
    global WEATHER_CITY, OMLX_HEALTH_URL, OMLX_STATS
    cfg = normalize_config(cfg)
    MIHOMO = os.path.expanduser(cfg["mihomo_socket"])
    CODEX_DB = os.path.expanduser(cfg["codex_db"])
    CCSWITCH_DB = os.path.expanduser(cfg["ccswitch_db"])
    DEEPSEEK_KEY = cfg["deepseek_key"] or os.environ.get("DEEPSEEK_KEY", "")
    MINIMI_KEY = cfg["mimo_key"] or os.environ.get("MINIMI_KEY", "")
    MINIMI_BASE = cfg["mimo_base"] or "https://api.xiaomimimo.com"
    WEATHER_CITY = cfg["weather_city"] or "Guangzhou"
    OMLX_HEALTH_URL = cfg["omlx_health_url"] or "http://127.0.0.1:9876/health"
    OMLX_STATS = os.path.expanduser(cfg["omlx_stats"])
    return cfg

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


def get_system_payload(net_rx=0, net_tx=0):
    cpu = get_cpu(); mp, mt = get_mem()
    return {
        "cpu": round(cpu,1), "mem": round(mp,1), "mem_total": round(mt),
        "disk": round(get_disk(),1), "load": get_load(),
        "net_rx": net_rx, "net_tx": net_tx,
        "uptime": get_uptime(), "hostname": socket.gethostname(),
    }


def default_collectors(net_rx=0, net_tx=0):
    return {
        "system": lambda: get_system_payload(net_rx, net_tx),
        "ccswitch": get_apis,
        "clash": get_clash,
        "codex": get_codex,
        "weather": get_weather,
        "omlx": get_omlx,
    }


def build_payload(pages, collectors=None):
    ordered = normalize_page_list(pages)
    collectors = collectors or default_collectors()
    payload = {"_control": {"pages": ordered}}
    for page in ordered:
        collector = collectors.get(page)
        if collector:
            payload[page] = collector()
    return payload

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

def _read_omlx_stats():
    """Read omLX stats.json and health endpoint."""
    result = {"running": False, "default_model": "?", "model_count": 0,
              "loaded_count": 0, "total_requests": 0,
              "total_prompt_tk": 0, "total_comp_tk": 0, "total_cached_tk": 0,
              "cache_efficiency": 0, "avg_prompt_speed": 0, "avg_gen_speed": 0,
              "memory_used": 0, "memory_ceiling": 0, "top_models": []}
    try:
        # Check health
        r = requests.get(OMLX_HEALTH_URL, timeout=3)
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

    city = WEATHER_CITY or "Guangzhou"
    data = {"temp": "", "desc": "", "humidity": "", "wind": "",
            "hi": "", "lo": "", "city": city}
    try:
        r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
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

def send_payload(payload, host, port):
    try:
        js = json.dumps(payload, ensure_ascii=False) + "\n"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2); s.connect((host, port))
        s.sendall(js.encode("utf-8")); s.close()
        return True
    except:
        return False


class SenderService:
    def __init__(self, config, status_cb=None):
        self._config = normalize_config(config)
        self._status_cb = status_cb
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._thread = None

    def start(self):
        self._enabled.set()
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._enabled.clear()

    def shutdown(self):
        self._enabled.clear()
        self._stop.set()

    def is_running(self):
        return self._enabled.is_set()

    def config(self):
        with self._lock:
            return copy.deepcopy(self._config)

    def update_config(self, config):
        with self._lock:
            self._config = normalize_config(config)

    def rediscover(self, wait_secs=8.0):
        cfg = self.config()
        ip = _discover_pi(cfg["port"], wait_secs=wait_secs)
        if ip:
            cfg["host"] = ip
            self.update_config(cfg)
        return ip

    def _emit_status(self, text):
        if self._status_cb:
            try:
                self._status_cb(text)
            except:
                pass

    def _loop(self):
        prev_net = None; prev_t = None
        while not self._stop.is_set():
            if not self._enabled.is_set():
                time.sleep(0.2)
                continue
            try:
                cfg = apply_runtime_config(self.config())
                now = time.time()
                c = psutil.net_io_counters()
                if prev_net and prev_t:
                    ela = now - prev_t
                    nr = (c.bytes_recv - prev_net[0]) / ela if ela > 0 else 0
                    nt = (c.bytes_sent - prev_net[1]) / ela if ela > 0 else 0
                else:
                    nr, nt = 0, 0
                prev_net = (c.bytes_recv, c.bytes_sent); prev_t = now

                payload = build_payload(cfg["pages"], default_collectors(nr, nt))
                ok = send_payload(payload, cfg["host"], cfg["port"])
                node = payload.get("clash", {}).get("current_node", "?")[:25]
                balance = payload.get("ccswitch", {}).get("ds_balance", "?")
                self._emit_status(
                    f"{'OK' if ok else 'ERR'} {cfg['host']} pages:{len(cfg['pages'])} "
                    f"node:{node} ds:{balance}"
                )
                time.sleep(cfg["interval"])
            except Exception as e:
                self._emit_status(f"Err: {e}")
                time.sleep(2)


def build_arg_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--host", "-H")
    p.add_argument("--port", "-P", type=int)
    p.add_argument("--interval", "-i", type=float)
    p.add_argument("--once", "-1", action="store_true")
    p.add_argument("--pages", help="Comma-separated page keys")
    p.add_argument("--ds-key", help="DeepSeek API key (or set DEEPSEEK_KEY env)")
    p.add_argument("--mm-key", help="MiMo API key (or set MINIMI_KEY env)")
    p.add_argument("--mm-base", help="MiMo API base URL")
    p.add_argument("--ui", action="store_true", help="Open the macOS settings window")
    p.add_argument("--no-ui", action="store_true", help="Force CLI sender mode")
    return p


def config_from_args(args, base=None):
    cfg = normalize_config(base or load_config())
    if args.host: cfg["host"] = args.host
    if args.port: cfg["port"] = args.port
    if args.interval: cfg["interval"] = args.interval
    if args.pages: cfg["pages"] = [p.strip() for p in args.pages.split(",") if p.strip()]
    if args.ds_key is not None: cfg["deepseek_key"] = args.ds_key
    if args.mm_key is not None: cfg["mimo_key"] = args.mm_key
    if args.mm_base is not None: cfg["mimo_base"] = args.mm_base
    return normalize_config(cfg)


def is_app_bundle():
    return bool(getattr(sys, "frozen", False)) or ".app/Contents" in os.path.abspath(sys.argv[0])


def run_cli(args):
    cfg = apply_runtime_config(config_from_args(args))
    ip = _discover_pi(cfg["port"])
    if ip:
        cfg["host"] = ip
        print(f"Discovered Pi at {ip}")
    else:
        try:
            resolved = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            print(f"Using --host {cfg['host']} ({resolved})")
            cfg["host"] = resolved
        except:
            print(f"Can't resolve {cfg['host']}"); sys.exit(1)

    if args.once:
        payload = build_payload(cfg["pages"], default_collectors(0, 0))
        send_payload(payload, cfg["host"], cfg["port"])
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print(f"SideMon → {cfg['host']}:{cfg['port']}  every {cfg['interval']}s", flush=True)
    service = SenderService(cfg, status_cb=lambda text: print(text, end="\r", flush=True))
    service.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.shutdown()
        print("\nDone.")


def run_gui_app(args):
    import objc
    from AppKit import (
        NSApp, NSApplication, NSBackingStoreBuffered, NSButton, NSButtonCell,
        NSDragOperationMove, NSFont, NSImage, NSMakeRect, NSMakeSize, NSMenu, NSMenuItem,
        NSScrollView, NSStatusBar, NSTableColumn, NSTableView,
        NSTableViewDropAbove, NSTextField,
        NSSecureTextField, NSVariableStatusItemLength, NSWindow,
        NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
    )

    ON = 1
    OFF = 0
    PAGE_DRAG_TYPE = "com.sidemon.rpizeromon.page"

    class SettingsController(objc.lookUpClass("NSObject")):
        def init(self):
            self = objc.super(SettingsController, self).init()
            if self is None:
                return None
            self.config = apply_runtime_config(config_from_args(args))
            self.service = SenderService(self.config, status_cb=self.threadStatus_)
            self.window = None
            self.status_item = None
            self.status_label = None
            self.toggle_item = None
            self.page_order = list(self.config["pages"])
            for page in DEFAULT_PAGES:
                if page not in self.page_order:
                    self.page_order.append(page)
            self.page_enabled = set(self.config["pages"])
            self.page_table = None
            self.fields = {}
            return self

        def applicationDidFinishLaunching_(self, notification):
            self.buildMenu()
            self.buildWindow()
            self.service.start()
            self.showSettings_(None)

        def applicationShouldTerminateAfterLastWindowClosed_(self, app):
            return False

        def windowShouldClose_(self, sender):
            sender.orderOut_(None)
            return False

        @objc.python_method
        def threadStatus_(self, text):
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusText:", text, False)
            except:
                pass

        def setStatusText_(self, text):
            if self.status_label:
                self.status_label.setStringValue_(str(text))

        @objc.python_method
        def buildMenu(self):
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            button = self.status_item.button()
            button.setTitle_("")
            image = self.loadStatusImage()
            if image:
                image.setSize_((18, 18))
                image.setTemplate_(True)
                button.setImage_(image)
            button.setToolTip_("RpiZeroMon")
            menu = NSMenu.alloc().init()
            show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show Settings", "showSettings:", "")
            show.setTarget_(self); menu.addItem_(show)
            self.toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Stop Sender", "toggleSender:", "")
            self.toggle_item.setTarget_(self); menu.addItem_(self.toggle_item)
            rediscover = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Rediscover Display", "rediscover:", "")
            rediscover.setTarget_(self); menu.addItem_(rediscover)
            menu.addItem_(NSMenuItem.separatorItem())
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "quit:", "")
            quit_item.setTarget_(self); menu.addItem_(quit_item)
            self.status_item.setMenu_(menu)

        @objc.python_method
        def loadStatusImage(self):
            paths = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0]))), "Resources", "icon.icns"),
                os.path.join(os.getcwd(), "assets", "icon-1024.png"),
                os.path.join(os.getcwd(), "assets", "icon.icns"),
            ]
            for path in paths:
                if os.path.exists(path):
                    img = NSImage.alloc().initWithContentsOfFile_(path)
                    if img:
                        return img
            return NSImage.imageNamed_("NSApplicationIcon")

        @objc.python_method
        def label(self, text, x, y, w=140, h=20, size=12, bold=False):
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            field.setStringValue_(text)
            field.setEditable_(False); field.setSelectable_(False)
            field.setBezeled_(False); field.setDrawsBackground_(False)
            field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
            self.window.contentView().addSubview_(field)
            return field

        @objc.python_method
        def textField(self, key, x, y, w=360, secure=False):
            cls = NSSecureTextField if secure else NSTextField
            field = cls.alloc().initWithFrame_(NSMakeRect(x, y, w, 24))
            field.setStringValue_(str(self.config.get(key, "")))
            self.window.contentView().addSubview_(field)
            self.fields[key] = field
            return field

        @objc.python_method
        def button(self, title, action, x, y, w=96, h=28):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            btn.setTitle_(title)
            btn.setTarget_(self)
            btn.setAction_(action)
            self.window.contentView().addSubview_(btn)
            return btn

        @objc.python_method
        def buildWindow(self):
            mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                    NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 680, 520), mask, NSBackingStoreBuffered, False
            )
            self.window.setTitle_("RpiZeroMon Settings")
            self.window.setReleasedWhenClosed_(False)
            self.window.setDelegate_(self)
            self.window.setMinSize_(NSMakeSize(400, 300))
            self.window.center()

            # Bottom bar (fixed, not scrolled)
            self.status_label = self.label("Sender starting...", 24, 14, 400, 20)
            self.button("Save", "saveSettings:", 476, 10, 86)
            self.button("Close", "closeWindow:", 576, 10, 86)

            # Scrollable content area above the bottom bar
            content_h = 460
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 40, 680, 460))
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            scroll.setBorderType_(0)  # NSNoBorder

            inner = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 660, content_h))
            cy = content_h  # y coordinate, counting down

            # -- Display section --
            lbl = self._addLabel(inner, "Display", 10, cy-22, 200, 20, 14, True)
            cy -= 30
            self._addLabel(inner, "IP", 12, cy, 30)
            self._addTextField(inner, "host", 44, cy-2, 180)
            self._addLabel(inner, "Port", 232, cy, 40)
            self._addTextField(inner, "port", 272, cy-2, 60)
            self._addLabel(inner, "Interval", 340, cy, 60)
            self._addTextField(inner, "interval", 400, cy-2, 50)
            self._addButton(inner, "Find", "rediscover:", 460, cy-4, 60)
            cy -= 36

            # -- Pages section --
            lbl = self._addLabel(inner, "Pages", 10, cy-22, 200, 20, 14, True)
            cy -= 28
            self.buildPageTable(inner, 10, cy-140, 640, 140)
            cy -= 150

            # -- API & Data Sources --
            lbl = self._addLabel(inner, "API & Data Sources", 10, cy-22, 240, 20, 14, True)
            cy -= 28
            rows = [
                ("DeepSeek Key", "deepseek_key", True),
                ("MiMo Key", "mimo_key", True),
                ("MiMo Base", "mimo_base", False),
                ("CC Switch DB", "ccswitch_db", False),
                ("Clash Socket", "mihomo_socket", False),
                ("Codex DB", "codex_db", False),
                ("Weather City", "weather_city", False),
                ("oMLX Health", "omlx_health_url", False),
                ("oMLX Stats", "omlx_stats", False),
            ]
            for title, key, secure in rows:
                self._addLabel(inner, title, 12, cy+3, 110)
                self._addTextField(inner, key, 128, cy, 500, secure=secure)
                cy -= 28

            # Trim inner view height to actual content
            actual_h = max(content_h, content_h - cy + 20)
            inner.setFrame_(NSMakeRect(0, 0, 660, actual_h))
            scroll.setDocumentView_(inner)
            self.window.contentView().addSubview_(scroll)
            self._scroll_view = scroll

        @objc.python_method
        def _addLabel(self, parent, text, x, y, w=110, h=18, size=12, bold=False):
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            field.setStringValue_(text)
            field.setEditable_(False); field.setSelectable_(False)
            field.setBezeled_(False); field.setDrawsBackground_(False)
            field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
            parent.addSubview_(field)
            return field

        @objc.python_method
        def _addTextField(self, parent, key, x, y, w=360, secure=False):
            cls = NSSecureTextField if secure else NSTextField
            field = cls.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
            field.setStringValue_(str(self.config.get(key, "")))
            parent.addSubview_(field)
            self.fields[key] = field
            return field

        @objc.python_method
        def _addButton(self, parent, title, action, x, y, w=60, h=24):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            btn.setTitle_(title); btn.setTarget_(self); btn.setAction_(action)
            parent.addSubview_(btn)
            return btn

        @objc.python_method
        def buildPageTable(self, parent, x, y, w, h):
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            scroll.setHasVerticalScroller_(True)
            table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
            enabled_col = NSTableColumn.alloc().initWithIdentifier_("enabled")
            enabled_col.setTitle_("Show")
            enabled_col.setWidth_(72)
            checkbox = NSButtonCell.alloc().init()
            checkbox.setButtonType_(3)
            checkbox.setTitle_("")
            enabled_col.setDataCell_(checkbox)
            table.addTableColumn_(enabled_col)

            page_col = NSTableColumn.alloc().initWithIdentifier_("page")
            page_col.setTitle_("Page")
            page_col.setWidth_(w - 90)
            table.addTableColumn_(page_col)
            table.setDataSource_(self)
            table.setDelegate_(self)
            table.registerForDraggedTypes_([PAGE_DRAG_TYPE])
            table.setDraggingSourceOperationMask_forLocal_(NSDragOperationMove, True)
            table.setAllowsMultipleSelection_(False)
            scroll.setDocumentView_(table)
            parent.addSubview_(scroll)
            self.page_table = table

        def numberOfRowsInTableView_(self, tableView):
            return len(self.page_order)

        def tableView_objectValueForTableColumn_row_(self, tableView, tableColumn, row):
            page = self.page_order[row]
            ident = str(tableColumn.identifier())
            if ident == "enabled":
                return ON if page in self.page_enabled else OFF
            return f"{PAGE_LABELS[page]} ({page})"

        def tableView_setObjectValue_forTableColumn_row_(self, tableView, value, tableColumn, row):
            page = self.page_order[row]
            if str(tableColumn.identifier()) == "enabled":
                if int(value):
                    self.page_enabled.add(page)
                else:
                    self.page_enabled.discard(page)

        def tableView_writeRowsWithIndexes_toPasteboard_(self, tableView, rowIndexes, pasteboard):
            row = rowIndexes.firstIndex()
            if row is None or row < 0:
                return False
            pasteboard.clearContents()
            pasteboard.setString_forType_(str(row), PAGE_DRAG_TYPE)
            return True

        def tableView_validateDrop_proposedRow_proposedDropOperation_(self, tableView, info, row, operation):
            tableView.setDropRow_dropOperation_(row, NSTableViewDropAbove)
            return NSDragOperationMove

        def tableView_acceptDrop_row_dropOperation_(self, tableView, info, row, operation):
            pasteboard = info.draggingPasteboard()
            source = pasteboard.stringForType_(PAGE_DRAG_TYPE)
            if source is None:
                return False
            try:
                source_idx = int(str(source))
            except ValueError:
                return False
            self.page_order = reorder_page_list(self.page_order, source_idx, row)
            tableView.reloadData()
            return True

        @objc.python_method
        def collectConfig(self, validate_pages=True):
            cfg = self.config.copy()
            for key, field in self.fields.items():
                cfg[key] = str(field.stringValue())
            pages = [p for p in self.page_order if p in self.page_enabled]
            if validate_pages and not pages:
                self.alert("At least one page must be enabled.")
                return None
            cfg["pages"] = pages or list(DEFAULT_PAGES)
            return normalize_config(cfg)

        @objc.python_method
        def alert(self, message):
            from AppKit import NSAlert
            alert = NSAlert.alloc().init()
            alert.setMessageText_(message)
            alert.runModal()

        def saveSettings_(self, sender):
            cfg = self.collectConfig()
            if not cfg:
                return
            self.config = save_config(cfg)
            self.service.update_config(self.config)
            self.setStatusText_("Saved. Sender updated.")

        def rediscover_(self, sender):
            cfg = self.collectConfig(validate_pages=False)
            port = cfg["port"] if cfg else self.config["port"]
            ip = _discover_pi(port, wait_secs=3.0)
            if ip:
                self.fields["host"].setStringValue_(ip)
                self.setStatusText_(f"Found display: {ip}")
            else:
                self.setStatusText_("No display found.")

        def toggleSender_(self, sender):
            if self.service.is_running():
                self.service.stop()
                self.toggle_item.setTitle_("Start Sender")
                self.setStatusText_("Sender stopped.")
            else:
                self.service.start()
                self.toggle_item.setTitle_("Stop Sender")
                self.setStatusText_("Sender started.")

        def showSettings_(self, sender):
            self.window.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)

        def closeWindow_(self, sender):
            self.window.orderOut_(None)

        def quit_(self, sender):
            self.service.shutdown()
            NSApp.terminate_(None)

    app = NSApplication.sharedApplication()
    controller = SettingsController.alloc().init()
    app.setDelegate_(controller)
    app.run()


def main():
    args = build_arg_parser().parse_args()
    if args.ui or (is_app_bundle() and not args.no_ui and not args.once):
        run_gui_app(args)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
