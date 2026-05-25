"""Tests for scripts/sync_campaign_performance_to_supabase.py resolution logic."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path


def _load_script_module():
    path = Path(__file__).resolve().parent / "scripts" / "sync_campaign_performance_to_supabase.py"
    spec = importlib.util.spec_from_file_location("sync_campaign_performance_to_supabase", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResolveLookbackDays(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()
        self.mod = _load_script_module()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_cli_days_wins_over_preset(self) -> None:
        self.assertEqual(self.mod.resolve_lookback_days(10, "weekly"), 10)

    def test_preset_when_no_days(self) -> None:
        self.assertEqual(self.mod.resolve_lookback_days(None, "monthly"), 30)
        self.assertEqual(self.mod.resolve_lookback_days(None, "quarterly"), 90)

    def test_env_when_no_cli(self) -> None:
        os.environ["SYNC_CAMPAIGN_PERFORMANCE_DAYS"] = "12"
        self.assertEqual(self.mod.resolve_lookback_days(None, None), 12)

    def test_default_when_env_invalid(self) -> None:
        os.environ["SYNC_CAMPAIGN_PERFORMANCE_DAYS"] = "not-a-number"
        self.assertEqual(self.mod.resolve_lookback_days(None, None), 30)

    def test_caps_high_values(self) -> None:
        self.assertEqual(self.mod.resolve_lookback_days(9999, None), self.mod._MAX_LOOKBACK)


if __name__ == "__main__":
    unittest.main()
