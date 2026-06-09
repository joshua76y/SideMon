import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mac import sidemon


def load_pi_module():
    path = ROOT / "pirecv" / "sidemon-pil.py"
    spec = importlib.util.spec_from_file_location("sidemon_pil", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigTests(unittest.TestCase):
    def test_default_config_has_all_pages_and_no_hardcoded_keys(self):
        cfg = sidemon.default_config()
        self.assertEqual(cfg["pages"], sidemon.DEFAULT_PAGES)
        self.assertEqual(cfg["deepseek_key"], "")
        self.assertEqual(cfg["mimo_key"], "")

    def test_normalize_config_filters_duplicate_and_unknown_pages(self):
        cfg = sidemon.normalize_config({
            "pages": ["weather", "bad", "system", "weather"],
            "host": "",
            "port": "9877",
            "interval": "1.5",
        })
        self.assertEqual(cfg["pages"], ["weather", "system"])
        self.assertEqual(cfg["host"], "192.168.1.37")
        self.assertEqual(cfg["port"], 9877)
        self.assertEqual(cfg["interval"], 1.5)

    def test_build_payload_only_collects_enabled_pages_and_adds_control(self):
        calls = []

        def fake(name, value):
            def inner():
                calls.append(name)
                return value
            return inner

        collectors = {
            "system": fake("system", {"cpu": 1}),
            "ccswitch": fake("ccswitch", {"ds_balance": "1"}),
            "clash": fake("clash", {"current_node": "A"}),
            "codex": fake("codex", {"tokens_5h": 1}),
            "weather": fake("weather", {"temp": "1"}),
            "omlx": fake("omlx", {"running": True}),
        }
        payload = sidemon.build_payload(["weather", "system"], collectors=collectors)
        self.assertEqual(payload["_control"]["pages"], ["weather", "system"])
        self.assertEqual(set(payload), {"_control", "weather", "system"})
        self.assertEqual(calls, ["weather", "system"])

    def test_reorder_page_list_uses_table_drop_row_semantics(self):
        pages = ["system", "ccswitch", "clash", "codex"]
        self.assertEqual(sidemon.reorder_page_list(pages, 0, 3),
                         ["ccswitch", "clash", "system", "codex"])
        self.assertEqual(sidemon.reorder_page_list(pages, 3, 0),
                         ["codex", "system", "ccswitch", "clash"])


class PiPageControlTests(unittest.TestCase):
    def test_normalize_page_order_filters_unknowns_and_falls_back(self):
        mod = load_pi_module()
        self.assertEqual(mod.normalize_page_order(["weather", "bad", "system", "weather"]),
                         ["weather", "system"])
        self.assertEqual(mod.normalize_page_order([]), mod.DEFAULT_ORDER)
        self.assertEqual(mod.normalize_page_order(["bad"]), mod.DEFAULT_ORDER)

    def test_waiting_ip_text_prefers_real_ip(self):
        mod = load_pi_module()
        self.assertEqual(mod.waiting_ip_text(["127.0.0.1", "192.168.1.37"]), "IP 192.168.1.37")
        self.assertEqual(mod.waiting_ip_text([]), "IP waiting for WiFi")


if __name__ == "__main__":
    unittest.main()
