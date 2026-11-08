"""Persistence must protect user examples from stale servers and interrupted writes."""
import copy
from concurrent.futures import ThreadPoolExecutor
import threading
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bullseye_vision.config import ConfigConflictError, load_config, save_config


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"

    def test_samples_survive_multiple_saves_and_fresh_load(self):
        cfg = load_config(self.path)
        cfg["palette"]["profiles"]["green"] = [{"center": [60, 200, 40], "scale": [7, 35, 45]}]
        save_config(cfg, self.path)
        cfg["palette"]["profiles"]["green"].append({"center": [85, 200, 100], "scale": [7, 35, 45]})
        save_config(cfg, self.path)
        restored = load_config(self.path)
        self.assertEqual(restored["palette"], cfg["palette"])
        self.assertNotIn("source_digest", json.loads(self.path.read_text()))

    def test_stale_server_cannot_overwrite_another_servers_examples(self):
        save_config(load_config(self.path), self.path)
        earlier, newer = load_config(self.path), load_config(self.path)
        newer["palette"]["profiles"]["red"] = [{"center": [0, 200, 100], "scale": [7, 35, 45]}]
        save_config(newer, self.path)
        earlier["navigation"]["turn_pwm"] = 70
        with self.assertRaisesRegex(ConfigConflictError, "nothing was overwritten"):
            save_config(earlier, self.path)
        self.assertEqual(load_config(self.path), newer)

    def test_simultaneous_writers_only_one_commits_the_same_revision(self):
        save_config(load_config(self.path), self.path)
        barrier = threading.Barrier(2)

        def writer(value):
            cfg = load_config(self.path)
            cfg["navigation"]["turn_pwm"] = value
            barrier.wait(timeout=3)
            try:
                save_config(cfg, self.path)
                return True
            except ConfigConflictError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, [70, 80]))
        self.assertEqual(sorted(results), [False, True])
        self.assertIn(load_config(self.path)["navigation"]["turn_pwm"], [70, 80])

    def test_missing_file_revision_detects_another_writer_creating_it(self):
        first, second = load_config(self.path), load_config(self.path)
        save_config(first, self.path)
        with self.assertRaises(ConfigConflictError):
            save_config(second, self.path)

    def test_checkout_or_external_edit_cannot_be_silently_replaced(self):
        save_config(load_config(self.path), self.path)
        cfg = load_config(self.path)
        self.path.write_text('{"navigation": {"turn_pwm": 85}}\n')
        external = self.path.read_bytes()
        with self.assertRaises(ConfigConflictError):
            save_config(copy.deepcopy(cfg), self.path)
        self.assertEqual(self.path.read_bytes(), external)

    def test_interrupted_replace_preserves_original_file_and_cleans_temporary_file(self):
        cfg = load_config(self.path)
        save_config(cfg, self.path)
        original = self.path.read_bytes()
        cfg["navigation"]["turn_pwm"] = 65
        with patch("bullseye_vision.config.os.replace", side_effect=OSError("disk interrupted")):
            with self.assertRaises(OSError):
                save_config(cfg, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".config-*.json")), [])
        save_config(cfg, self.path)
        self.assertEqual(load_config(self.path)["navigation"]["turn_pwm"], 65)

    def test_loaded_config_can_be_exported_to_a_different_path(self):
        cfg = load_config(self.path)
        target = self.path.with_name("export.json")
        save_config(cfg, target)
        self.assertEqual(load_config(target), cfg)


if __name__ == "__main__":
    unittest.main()
