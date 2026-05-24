from pathlib import Path
import os
import tempfile
import unittest

from invest.config import AppConfig, load_dotenv


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_sets_missing_values_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('ALLOIQ_TEST_ENV="from-file"\nEXISTING=value\n', encoding="utf-8")
            os.environ.pop("ALLOIQ_TEST_ENV", None)
            os.environ["EXISTING"] = "from-env"

            try:
                load_dotenv(path)
                self.assertEqual(os.environ["ALLOIQ_TEST_ENV"], "from-file")
                self.assertEqual(os.environ["EXISTING"], "from-env")
            finally:
                os.environ.pop("ALLOIQ_TEST_ENV", None)
                os.environ.pop("EXISTING", None)

    def test_watchlist_symbols_preserve_config_priority(self):
        config = AppConfig(path=Path("config/invest.toml"), data={"watchlist": {"symbols": ["TSM", "NVDA", "TSM"]}})

        self.assertEqual(config.watchlist_symbols, ["TSM", "NVDA"])

    def test_focus_manager_keys_preserve_config_priority(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={"focus_managers": {"keys": ["altimeter", "coatue", "altimeter"]}},
        )

        self.assertEqual(config.focus_manager_keys, ["altimeter", "coatue"])

    def test_focus_manager_tiers_drive_focus_order(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "focus_managers": {
                    "tier1_keys": ["situational-awareness", "altimeter", "dragoneer"],
                    "tier2_keys": ["coatue", "greenoaks", "coatue"],
                    "keys": ["ignored"],
                }
            },
        )

        self.assertEqual(
            config.focus_manager_keys,
            ["situational-awareness", "altimeter", "dragoneer", "coatue", "greenoaks"],
        )
        self.assertEqual(config.focus_manager_tier_map["altimeter"], "tier_1")
        self.assertEqual(config.focus_manager_tier_map["coatue"], "tier_2")


if __name__ == "__main__":
    unittest.main()
