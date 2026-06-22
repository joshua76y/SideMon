#!/usr/bin/env python3
"""Apply fix: replace time.sleep(3) with port polling in _start_codex_server()."""
import os

path = os.path.join(os.path.dirname(__file__), "mac", "sidemon.py")
with open(path) as f:
    content = f.read()

old = '''    time.sleep(3)  # Wait for server start'''

new = '''    # Poll port until server responds (up to 7.5s)
    for _ in range(15):
        try:
            ts = socket.socket()
            ts.settimeout(0.5)
            ts.connect(("127.0.0.1", _codex_port))
            ts.close()
            return
        except:
            pass
        time.sleep(0.5)
    if _codex_proc.poll() is not None:
        raise Exception(f"codex app-server exited with code {_codex_proc.returncode}")'''

if old not in content:
    print("ERROR: old text not found, fix already applied?")
    exit(1)

new_content = content.replace(old, new, 1)
with open(path, "w") as f:
    f.write(new_content)
print("OK: _start_codex_server() updated with port polling.")
