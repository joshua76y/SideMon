#!/usr/bin/env python3
"""SideMon Mac sender — system / proxy / clash / codex / weather → Pi Zero W."""

import argparse, base64, copy, datetime, json, os, re, socket, sqlite3, subprocess, sys, struct, threading, time

try: import psutil, requests
except ImportError: print("pip3 install psutil requests"); sys.exit(1)

APP_NAME = "RpiZeroMon"
CONFIG_DIR = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_PAGES = ["system", "ccswitch", "clash", "codex", "weather", "datetime", "omlx"]
PAGE_LABELS = {
    "system": "System",
    "ccswitch": "API Usage",
    "clash": "Clash",
    "codex": "Codex",
    "weather": "Weather",
    "datetime": "DateTime",
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
# Codex usage caps — tunable in settings UI
CODEX_CAP_5H = 5_000_000_000     # 5-hour rolling cap
CODEX_CAP_7D = 35_000_000_000    # 7-day rolling cap


def default_config():
    return {
        "hosts": [],
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
        "codex_cap_5h": 5_000_000_000,
        "codex_cap_7d": 35_000_000_000,
        "page_interval": 15,
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
    # Backward compat: migrate old host/port to hosts list
    hosts = cfg.get("hosts", None)
    if not hosts and cfg.get("host"):
        hosts = [{"host": str(cfg.get("host", "")).strip(), "port": _coerce_int(cfg.get("port"), 9877)}]
    elif isinstance(hosts, list) and hosts and isinstance(hosts[0], str):
        hosts = [{"host": h.strip(), "port": _coerce_int(cfg.get("port"), 9877)} for h in hosts if h.strip()]
    if not hosts or not isinstance(hosts, list):
        hosts = [{"host": "192.168.1.37", "port": 9877}]
    cfg["hosts"] = []
    for h in hosts:
        if isinstance(h, dict) and h.get("host", "").strip():
            cfg["hosts"].append({
                "host": str(h["host"]).strip(),
                "port": max(1, min(65535, _coerce_int(h.get("port"), 9877))),
            })
    if not cfg["hosts"]:
        cfg["hosts"] = [{"host": "192.168.1.37", "port": 9877}]
    cfg["interval"] = max(0.2, _coerce_float(cfg.get("interval"), default_config()["interval"]))
    cfg["page_interval"] = max(3, _coerce_int(cfg.get("page_interval"), default_config()["page_interval"]))
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


_last_sk = [""]
def apply_runtime_config(cfg):
    global MIHOMO, CODEX_DB, CCSWITCH_DB, DEEPSEEK_KEY, MINIMI_KEY, MINIMI_BASE
    global WEATHER_CITY, OMLX_HEALTH_URL, OMLX_STATS, CODEX_CAP_5H, CODEX_CAP_7D
    global _ccswitch_cache, _clash_cache, _codex_cache
    cfg = normalize_config(cfg)
    # Clear caches if API config changed
    new_sk = f"{cfg.get('deepseek_key','')}|{cfg.get('mimo_key','')}|{cfg.get('mimo_base','')}"
    if new_sk != _last_sk[0]:
        _ccswitch_cache = {"ts": 0, "data": None}
        _clash_cache = {"ts": 0, "data": None}
        _codex_cache = {"ts": 0, "data": None}
        _last_sk[0] = new_sk
    MIHOMO = os.path.expanduser(cfg["mihomo_socket"])
    CODEX_DB = os.path.expanduser(cfg["codex_db"])
    CCSWITCH_DB = os.path.expanduser(cfg["ccswitch_db"])
    DEEPSEEK_KEY = cfg["deepseek_key"] or os.environ.get("DEEPSEEK_KEY", "")
    MINIMI_KEY = cfg["mimo_key"] or os.environ.get("MINIMI_KEY", "")
    MINIMI_BASE = cfg["mimo_base"] or "https://api.xiaomimimo.com"
    WEATHER_CITY = cfg["weather_city"] or "Guangzhou"
    OMLX_HEALTH_URL = cfg["omlx_health_url"] or "http://127.0.0.1:9876/health"
    OMLX_STATS = os.path.expanduser(cfg["omlx_stats"])
    # Codex usage caps
    CODEX_CAP_5H = max(1, _coerce_int(cfg.get("codex_cap_5h"), 5_000_000_000))
    CODEX_CAP_7D = max(1, _coerce_int(cfg.get("codex_cap_7d"), 35_000_000_000))
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
        "datetime": get_datetime,
        "omlx": get_omlx,
    }


def build_payload(cfg, collectors=None):
    pages = cfg.get("pages", list(DEFAULT_PAGES))
    ordered = normalize_page_list(pages)
    collectors = collectors or default_collectors()
    pi = max(2, int(cfg.get("interval", 5)))
    payload = {"_control": {"pages": ordered, "page_interval": pi}}
    for page in ordered:
        collector = collectors.get(page)
        if collector:
            try:
                payload[page] = collector()
            except Exception as e:
                print(f"Collector {page}: {e}", file=sys.stderr)
                payload[page] = None
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



def get_datetime():
    return {"timestamp": int(time.time())}

def get_apis():
    global _ccswitch_cache
    now = time.time()
    if now - _ccswitch_cache["ts"] < 15 and _ccswitch_cache["data"]:
        return _ccswitch_cache["data"]

    data = {
        "ds_balance": "?", "ds_currency": "CNY",
        "mm_balance": "?", "mm_currency": "",
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

    # MiniMi stats — token plan usage percentage (with calibration offset)
    MM_TOKEN_PLAN = 4_100_000_000    # 4.1B tokens
    MM_CALIB_OFFSET = 3022180996     # real_used(3,032,234,857) - db_seen at 2026-06-18
    try:
        db3 = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True, timeout=2)
        row = db3.execute(
                "SELECT COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) "
                "FROM proxy_request_logs WHERE model LIKE '%mimo%'").fetchone()
        db_used = row[0] if row else 0
        used = db_used + MM_CALIB_OFFSET
        pct = round(used / MM_TOKEN_PLAN * 100, 1) if MM_TOKEN_PLAN > 0 else 0
        data["mm_balance"] = str(pct)  # percentage used
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
        data["cache_hit_rate"] = min(round(cache_r / inp * 100, 1), 100.0) if inp > 0 else 0
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
    """Query Mihomo/Clash REST API via Unix socket directly."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(MIHOMO)
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        buf = b""
        while True:
            try:
                chunk = s.recv(65536)
                if not chunk: break
                buf += chunk
            except socket.timeout:
                break
        s.close()
        body = buf.split(b"\r\n\r\n", 1)
        if len(body) > 1 and body[1].strip():
            return json.loads(body[1])
        return None
    except Exception:
        return None

def _mihomo_node():
    """Get current node: GLOBAL if proxied, else fall back to Proxies group."""
    px = _mihomo("/proxies")
    if not px: return None
    proxies = px.get("proxies", {})
    g = proxies.get("GLOBAL", {})
    now = g.get("now", None)
    # If GLOBAL is DIRECT/REJECT, look at Proxies or other groups
    if now in (None, "DIRECT", "REJECT"):
        # Try Proxies group first (common parent proxy group)
        for grp_name in ("Proxies", "HK", "US", "SG", "JP", "TW"):
            grp = proxies.get(grp_name)
            if grp and grp.get("now") and grp["now"] not in ("DIRECT", "REJECT"):
                return grp["now"]
    return now

_clash_cache = {"ts": 0, "data": None}

def get_clash():
    global _clash_cache
    now = time.time()
    if now - _clash_cache["ts"] < 30 and _clash_cache["data"]:
        return _clash_cache["data"]

    d = {"running": False, "version": "", "mode": "Rule",
         "current_node": "Unknown", "traffic_used": "", "traffic_total": "",
         "expire_date": "", "upload_total": 0, "download_total": 0,
         "active_connections": 0, "update_time": ""}

    ver = _mihomo("/version")
    if not ver:
        return d
    d["running"] = True
    d["version"] = ver.get("version", "")

    cfg = _mihomo("/configs")
    if cfg:
        d["mode"] = cfg.get("mode", "Rule")

    d["current_node"] = _mihomo_node() or "Unknown"

    g = _mihomo("/proxies/GLOBAL")
    if g:
        for name in g.get("all", []):
            if not isinstance(name, str):
                continue
            if name.startswith("Traffic:"):
                payload = name[len("Traffic:"):].strip()
                if "/" in payload:
                    d["traffic_used"], d["traffic_total"] = [x.strip() for x in payload.split("/", 1)]
            elif name.startswith("Expire:"):
                d["expire_date"] = name[len("Expire:"):].strip()

    conns = _mihomo("/connections")
    if conns:
        conn_list = conns.get("connections") or []
        if isinstance(conn_list, list):
            d["active_connections"] = len(conn_list)
            d["download_total"] = conns.get("downloadTotal", d["download_total"]) or 0
            d["upload_total"] = conns.get("uploadTotal", d["upload_total"]) or 0

    d["update_time"] = datetime.datetime.now().strftime("%H:%M:%S")
    _clash_cache = {"ts": now, "data": d}
    return d

# ══════════════════════════════════════════════════════════════════════
# Codex usage
# ══════════════════════════════════════════════════════════════════════

# Codex usage via app-server WebSocket (accurate rate limits from Codex backend)
import struct as _struct

_codex_proc = None
_codex_port = 0
_codex_cache = {"ts": 0, "data": None}

def _ws_handshake(s, port):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk: break
        resp += chunk
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise Exception("WebSocket handshake failed")

def _ws_send_json(s, data):
    payload = json.dumps(data).encode()
    frame = bytearray([0x81])
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(length.to_bytes(2, 'big'))
    else:
        frame.append(0x80 | 127)
        frame.extend(length.to_bytes(8, 'big'))
    mask_key = os.urandom(4)
    frame.extend(mask_key)
    masked = bytearray(payload)
    for i in range(length):
        masked[i] ^= mask_key[i % 4]
    frame.extend(masked)
    s.sendall(bytes(frame))

def _ws_recv_json(s):
    hdr = s.recv(2)
    if len(hdr) < 2: return {}
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(s.recv(2), 'big')
    elif length == 127:
        length = int.from_bytes(s.recv(8), 'big')
    data = bytearray()
    while len(data) < length:
        chunk = s.recv(length - len(data))
        if not chunk: break
        data.extend(chunk)
    return json.loads(data.decode()) if data else {}

def _start_codex_server():
    global _codex_proc, _codex_port
    if _codex_proc and _codex_proc.poll() is None:
        return  # Already running
    # Find codex binary
    codex_bin = None
    for path in [
        "/Applications/Codex.app/Contents/Resources/codex",
        os.path.expanduser("~/Library/Application Support/QClaw/npm-global/bin/codex"),
    ]:
        if os.path.exists(path):
            codex_bin = path
            break
    if not codex_bin:
        raise Exception("codex binary not found")
    # Find free port
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    _codex_port = s.getsockname()[1]
    s.close()
    _codex_proc = subprocess.Popen(
        [codex_bin, "app-server", "--listen", f"ws://127.0.0.1:{_codex_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # Wait for server start

def _query_codex_limits():
    global _codex_port
    try:
        _start_codex_server()
        with socket.create_connection(("127.0.0.1", _codex_port), timeout=10) as s:
            _ws_handshake(s, _codex_port)
            _ws_send_json(s, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "clientInfo": {"name": "sidemon", "title": "SideMon", "version": "1"},
                    "capabilities": {"experimentalApi": True, "requestAttestation": False},
                },
            })
            _ws_recv_json(s)  # init response
            _ws_send_json(s, {
                "jsonrpc": "2.0", "id": 2,
                "method": "account/rateLimits/read",
                "params": None,
            })
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                s.settimeout(max(0.1, deadline - time.monotonic()))
                msg = _ws_recv_json(s)
                if msg.get("id") == 2:
                    if "error" in msg:
                        raise Exception(str(msg["error"]))
                    result = msg.get("result", {})
                    rl = result.get("rateLimits", result)
                    return rl
            raise Exception("rate limits timeout")
    except Exception as e:
        print(f"Codex WS error: {e}", file=sys.stderr)
        return None

def get_codex():
    global _codex_cache
    now = time.time()
    if now - _codex_cache["ts"] < 30 and _codex_cache["data"]:
        return _codex_cache["data"]

    result = {
        "pct_5h": 0, "pct_7d": 0,
        "tokens_5h": 0, "tokens_7d": 0,
        "reset_5h": "", "reset_7d": "",
        "status": "offline",
        "model": "codex",
        "plan": "plus",
    }

    rl = _query_codex_limits()
    if rl:
        primary = rl.get("primary", {})
        secondary = rl.get("secondary", {})
        result["status"] = "online"
        result["pct_5h"] = primary.get("usedPercent", 0)
        result["pct_7d"] = secondary.get("usedPercent", 0)
        result["plan"] = rl.get("planType", "plus")

        from datetime import datetime, timezone
        if primary.get("resetsAt"):
            dt = datetime.fromtimestamp(primary["resetsAt"], tz=timezone.utc)
            result["reset_5h"] = dt.astimezone().strftime("%H:%M")
        if secondary.get("resetsAt"):
            dt = datetime.fromtimestamp(secondary["resetsAt"], tz=timezone.utc)
            result["reset_7d"] = dt.astimezone().strftime("%m/%d %H:%M")

        result["tokens_5h"] = primary.get("usedPercent", 0)
        result["tokens_7d"] = secondary.get("usedPercent", 0)

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
    data = {"city": city, "temp_c": 0, "feels_like_c": 0, "humidity": 0,
            "condition": "?", "wind_kph": 0, "wind_dir": "",
            "date": "", "day_of_week": "",
            "forecast": [], "sunrise": "?", "sunset": "?", "uv_index": "?"}
    try:
        r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
        if r.ok:
            j = r.json()
            cc = j["current_condition"][0]
            data["temp_c"] = int(cc.get("temp_C", 0))
            data["feels_like_c"] = int(cc.get("FeelsLikeC", 0))
            data["humidity"] = int(cc.get("humidity", 0))
            data["condition"] = cc.get("weatherDesc", [{}])[0].get("value", "?")
            data["wind_kph"] = int(cc.get("windspeedKmph", 0))
            data["wind_dir"] = cc.get("winddir16Point", "")

            # Date info
            today = j.get("weather", [{}])[0]
            data["date"] = today.get("date", "")
            # Day of week from date
            from datetime import datetime
            try:
                dt = datetime.strptime(data["date"], "%Y-%m-%d")
                data["day_of_week"] = dt.strftime("%a")
            except: pass

            # Forecast (3 days)
            for wday in j.get("weather", [])[:3]:
                fc = {"day": ""}
                try:
                    fdt = datetime.strptime(wday.get("date",""), "%Y-%m-%d")
                    fc["day"] = fdt.strftime("%a")
                except: fc["day"] = wday.get("date","")[-5:]
                fc["high_c"] = wday.get("maxtempC", "?")
                fc["low_c"] = wday.get("mintempC", "?")
                fc["condition"] = wday.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "?") if len(wday.get("hourly",[])) > 4 else "?"
                data["forecast"].append(fc)

            # Sunrise/sunset
            astro = today.get("astronomy", [{}])[0] if today.get("astronomy") else {}
            data["sunrise"] = astro.get("sunrise", "?").lstrip("0")
            data["sunset"] = astro.get("sunset", "?").lstrip("0")

            # UV index
            uv_vals = [int(w.get("uvIndex", 0)) for w in j.get("weather", []) if w.get("uvIndex")]
            data["uv_index"] = max(uv_vals) if uv_vals else "?"
    except Exception as e:
        print(f"Weather error: {e}", file=sys.stderr)

    _weather_cache = {"ts": now, "data": data}
    return data

# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# UDP Auto-Discovery
# ══════════════════════════════════════════════════════════════════════

DISCOVERY_PORT = 9878

def _discover_all(tcp_port=9877, wait_secs=8.0):
    """Listen for SideMon UDP broadcasts; return list of {host,port} for all displays."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", DISCOVERY_PORT))
    except OSError:
        s.close()
        print("Discovery port busy, skipping auto-discovery")
        return []
    s.settimeout(wait_secs)
    found = {}  # ip -> {"host": ip, "port": port}
    deadline = time.time() + wait_secs
    try:
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "sidemon":
                    ip = addr[0]
                    port = msg.get("port", tcp_port)
                    if ip not in found:
                        found[ip] = {"host": ip, "port": port}
                        print(f"Discovered SideMon display at {ip}:{port}")
            except socket.timeout:
                break
    except Exception as e:
        print(f"Discovery error: {e}")
    finally:
        s.close()
    return list(found.values())

def send_payload(payload, sock):
    """Send payload over existing socket. Returns True on success."""
    try:
        js = json.dumps(payload, ensure_ascii=False) + "\n"
        sock.sendall(js.encode("utf-8"))
        return True
    except:
        return False

def connect_payload(host, port, timeout=5):
    """Create a persistent TCP connection. Returns socket or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        return s
    except:
        return None


_socks = {}

class SenderService:
    def __init__(self, config, status_cb=None):
        self._config = normalize_config(config)
        self._status_cb = status_cb
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._thread = None
        global _socks
        _socks = {}

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
        for s in _socks.values():
            try:
                if s: s.close()
            except: pass
        _socks.clear()

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
        found = _discover_all(wait_secs=wait_secs)
        if found:
            cfg["hosts"] = found
            self.update_config(cfg)
        return [h["host"] for h in found]

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
                cfg = apply_runtime_config(load_config())  # reload from disk so GUI changes take effect immediately
                now = time.time()
                c = psutil.net_io_counters()
                if prev_net and prev_t:
                    ela = now - prev_t
                    nr = (c.bytes_recv - prev_net[0]) / ela if ela > 0 else 0
                    nt = (c.bytes_sent - prev_net[1]) / ela if ela > 0 else 0
                else:
                    nr, nt = 0, 0
                prev_net = (c.bytes_recv, c.bytes_sent); prev_t = now

                payload = build_payload(cfg, default_collectors(nr, nt))
                hosts = cfg.get("hosts", [{"host": cfg.get("host", "127.0.0.1"), "port": 9877}])
                ok_list = []
                for h in hosts:
                    # Maintain persistent connections per host
                    hkey = f"{h['host']}:{h['port']}"
                    if hkey not in _socks or not _socks[hkey]:
                        _socks[hkey] = connect_payload(h['host'], h['port'])
                    sock = _socks.get(hkey)
                    if sock:
                        ok = send_payload(payload, sock)
                        if not ok:
                            try: sock.close()
                            except: pass
                            _socks[hkey] = None
                        ok_list.append(f"{'OK' if ok else 'ERR'}:{h['host']}")
                    else:
                        ok_list.append(f"ERR:{h['host']}")
                node = payload.get("clash", {}).get("current_node", "?")[:25]
                balance = payload.get("ccswitch", {}).get("ds_balance", "?")
                self._emit_status(
                    f"{' '.join(ok_list)} pages:{len(cfg.get('pages') or [])} "
                    f"node:{node} ds:{balance}"
                )
                time.sleep(cfg["interval"])
            except Exception as e:
                self._emit_status(f"Err: {e}")
                time.sleep(2)


def build_arg_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--host", "-H", action="append")
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
    if args.host:
        if isinstance(args.host, list):
            cfg["hosts"] = [{"host": h.strip(), "port": cfg["hosts"][0]["port"] if cfg["hosts"] else 9877} for h in args.host if h.strip()]
        else:
            cfg["hosts"] = [{"host": str(args.host).strip(), "port": 9877}]
    if args.port:
        for h in cfg.get("hosts", []):
            h["port"] = args.port
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
    found = _discover_all()
    if found:
        cfg["hosts"] = found
        for h in found:
            print(f"Discovered display at {h['host']}:{h['port']}")
    elif not cfg.get("hosts"):
        print("No displays found and no hosts configured."); sys.exit(1)

    if args.once:
        payload = build_payload(cfg, default_collectors(0, 0))
        for h in cfg["hosts"]:
            sock = connect_payload(h["host"], h["port"])
            if sock:
                ok = send_payload(payload, sock)
                sock.close()
                print(f"{'Sent to' if ok else 'ERR'} {h['host']}:{h['port']}")
            else:
                print(f"ERR connecting to {h['host']}:{h['port']}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    targets = ', '.join(f"{h['host']}:{h['port']}" for h in cfg["hosts"])
    print(f"SideMon -> {targets}  every {cfg['interval']}s", flush=True)
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
            # Try template icons first (for clean B&W menu bar appearance)
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))
            base_dir = os.path.dirname(app_dir)
            paths = [
                os.path.join(base_dir, "assets", "template-icon.png"),
                os.path.join(app_dir, "Resources", "template-icon.png"),
                os.path.join(os.getcwd(), "assets", "template-icon.png"),
                os.path.join(app_dir, "Resources", "icon.icns"),
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
                NSMakeRect(0, 0, 520, 600), mask, NSBackingStoreBuffered, False
            )
            self.window.setTitle_("RpiZeroMon Settings")
            self.window.setReleasedWhenClosed_(False)
            self.window.setDelegate_(self)
            self.window.setMinSize_(NSMakeSize(480, 580))
            self.window.center()

            cv = self.window.contentView()
            WW = 520
            CH = 600  # content view height

            def cy(top_y):
                return CH - top_y

            ty = 10  # top-down y cursor

            # ── Display section ──
            self.addLabel(cv, "Displays (up to 5)", 16, cy(ty), 200, 18, 13, True)
            self.addButton(cv, "Find All", "rediscover:", WW - 120, cy(ty) - 3, 100)
            ty += 20

            # 5 IP input rows
            for idx, ip_field_key in enumerate(["host_1", "host_2", "host_3", "host_4", "host_5"]):
                display_no = idx + 1
                label_y = cy(ty) + 3
                field_y = cy(ty) - 1
                self.addLabel(cv, f"Display {display_no}", 16, label_y, 72, 16, 11)
                self.addTextField(cv, ip_field_key, 92, field_y, 160)
                ty += 28

            ty += 2

            # Shared Port / Interval row
            self.addLabel(cv, "Port", 16, cy(ty) + 3, 32, 16, 11)
            self.addTextField(cv, "port", 52, cy(ty) - 1, 60)
            self.addLabel(cv, "Interval(s)", 126, cy(ty) + 3, 72, 16, 11)
            self.addTextField(cv, "interval", 200, cy(ty) - 1, 52)
            ty += 30

            ty += 4

            # ── Pages section ──
            self.addLabel(cv, "Pages", 16, cy(ty), 100, 18, 13, True)
            ty += 2
            table_h = 200
            self.buildPageTable(cv, 16, cy(ty + table_h), WW - 32, table_h)
            ty += table_h + 8

            # ── API & Data Sources ──
            self.addLabel(cv, "API & Data Sources", 16, cy(ty), 200, 18, 13, True)
            ty += 20
            for title, key, secure in [
                ("DeepSeek Key", "deepseek_key", True),
                ("MiMo Key", "mimo_key", True),
                ("MiMo Base", "mimo_base", False),
            ]:
                self.addLabel(cv, title, 16, cy(ty) + 3, 100)
                self.addTextField(cv, key, 120, cy(ty), 380, secure=secure)
                ty += 24

            # ── Bottom bar ──
            self.status_label = self.addLabel(cv, "Sender starting...", 16, 12, 280, 18, 11)
            self.addButton(cv, "Save", "saveSettings:", WW - 172, 8, 72)
            self.addButton(cv, "Close", "closeWindow:", WW - 92, 8, 72)

        @objc.python_method
        def addLabel(self, parent, text, x, y, w=110, h=18, size=12, bold=False):
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            field.setStringValue_(text)
            field.setEditable_(False); field.setSelectable_(False)
            field.setBezeled_(False); field.setDrawsBackground_(False)
            field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
            parent.addSubview_(field)
            return field

        @objc.python_method
        def addTextField(self, parent, key, x, y, w=360, secure=False):
            cls = NSSecureTextField if secure else NSTextField
            field = cls.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
            field.setStringValue_(str(self.config.get(key, "")))
            parent.addSubview_(field)
            self.fields[key] = field
            return field

        @objc.python_method
        def addButton(self, parent, title, action, x, y, w=60, h=24):
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
            # Build hosts from all 5 IP fields
            port = _coerce_int(cfg.get("port"), 9877)
            new_hosts = []
            seen = set()
            for idx in range(1, 6):
                key = f"host_{idx}"
                ip = cfg.get(key, "").strip()
                if ip and ip not in seen:
                    new_hosts.append({"host": ip, "port": port})
                    seen.add(ip)
            cfg["hosts"] = new_hosts or [{"host": "192.168.1.37", "port": 9877}]
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
            found = _discover_all(wait_secs=5.0)
            if found:
                self.config["hosts"] = found
                # Fill primary port from first discovered
                if found[0].get("port"):
                    self.fields["port"].setStringValue_(str(found[0]["port"]))
                # Fill all 5 IP fields
                for idx in range(1, 6):
                    key = f"host_{idx}"
                    if idx <= len(found):
                        self.fields[key].setStringValue_(found[idx - 1]["host"])
                    else:
                        self.fields[key].setStringValue_("")
                self.setStatusText_(f"Found {len(found)} display(s)")
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
