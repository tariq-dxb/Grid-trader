# grid_trader/tests/test_risk_engine.py
import unittest
import sys
import math
from typing import Dict # Added

from grid_trader import config as package_config
from grid_trader.engine.risk_manager import RiskManager


# --- Test Configuration for RiskManager Tests ---
class TestConfigRM:
    DEFAULT_RISK_PER_TRADE_USD = 10.0
    MAX_ACCOUNT_RISK_PERCENTAGE = 2.0
    LOG_LEVEL = "ERROR"
    LOG_FILE = "test_risk_engine_temp.log"
    SYMBOL_SETTINGS = {
        "EURUSD": {
            "pip_value_per_lot": 10.0, "min_lot_size": 0.01, "lot_step": 0.01,
            "decimals": 5, "point_value": 0.00001, "contract_size": 100000
        },
        "USDJPY": {
            "pip_value_per_lot": 8.5, "min_lot_size": 0.01, "lot_step": 0.01,
            "decimals": 3, "point_value": 0.001, "contract_size": 100000,
            "base_currency_is_account_currency": True
        },
        "XAUUSD": {
            "pip_value_per_lot": 1.0, "min_lot_size": 0.01, "lot_step": 0.01,
            "decimals": 2, "point_value": 0.01, "contract_size": 100,
            "is_cfd_or_metal": True, "base_currency_is_account_currency": True
        },
         "NODECIMALS": {
            "pip_value_per_lot": 1.0, "min_lot_size": 1.0, "lot_step": 1.0,
            "decimals": 0, "point_value": 1.0, "contract_size": 1,
            "is_cfd_or_metal": True
        }
    }


class TestRiskManager(unittest.TestCase):
    original_config_module_ref = None
    test_config_instance_ref = None

    @classmethod
    def setUpClass(cls):
        cls.original_config_module_ref = sys.modules.get('grid_trader.config')
        cls.test_config_instance_ref = TestConfigRM() # Create an instance
        sys.modules['grid_trader.config'] = cls.test_config_instance_ref

        # Patch config in already imported modules that RiskManager might use (like logger)
        # or RiskManager itself if it caches 'config' at module level.
        # RiskManager imports 'from .. import config' at its top level.
        modules_to_patch = ['grid_trader.engine.risk_manager', 'grid_trader.utils.logger']
        for mod_name in modules_to_patch:
            if mod_name in sys.modules:
                sys.modules[mod_name].config = cls.test_config_instance_ref
                if hasattr(sys.modules[mod_name], 'package_config'): # For grid_manager.py style
                    sys.modules[mod_name].package_config = cls.test_config_instance_ref


    @classmethod
    def tearDownClass(cls):
        if cls.original_config_module_ref:
            sys.modules['grid_trader.config'] = cls.original_config_module_ref
        else:
            if 'grid_trader.config' in sys.modules and isinstance(sys.modules['grid_trader.config'], TestConfigRM):
                 del sys.modules['grid_trader.config']

        modules_to_restore = ['grid_trader.engine.risk_manager', 'grid_trader.utils.logger']
        for mod_name in modules_to_restore:
            if mod_name in sys.modules and cls.original_config_module_ref :
                 sys.modules[mod_name].config = cls.original_config_module_ref
                 if hasattr(sys.modules[mod_name], 'package_config'):
                     sys.modules[mod_name].package_config = cls.original_config_module_ref


    def setUp(self):
        # RiskManager's __init__ reads from the (now mocked) config module for defaults
        self.rm = RiskManager(account_balance=10000, leverage="1:100")

    def test_initialization(self):
        self.assertEqual(self.rm.account_balance, 10000)
        self.assertEqual(self.rm.leverage_ratio, 100)
        self.assertEqual(self.rm.default_risk_per_trade_usd, TestConfigRM.DEFAULT_RISK_PER_TRADE_USD)
        self.assertEqual(self.rm.max_account_risk_percentage, TestConfigRM.MAX_ACCOUNT_RISK_PERCENTAGE)

    def test_parse_leverage(self):
        self.assertEqual(self.rm._parse_leverage("1:50"), 50)
        self.assertEqual(self.rm._parse_leverage("1:200"), 200)
        self.assertEqual(self.rm._parse_leverage("100"), 100)
        self.assertEqual(self.rm._parse_leverage("1:0"), 100)
        self.assertEqual(self.rm._parse_leverage("2:100"), 100)


    def test_get_symbol_config(self):
        eurusd_conf = self.rm.get_symbol_config("EURUSD")
        self.assertEqual(eurusd_conf['pip_value_per_lot'], 10.0)
        with self.assertRaisesRegex(ValueError, "Configuration for symbol NONEXISTENT not found"):
            self.rm.get_symbol_config("NONEXISTENT")

        original_eurusd_settings = TestConfigRM.SYMBOL_SETTINGS["EURUSD"].copy()
        del TestConfigRM.SYMBOL_SETTINGS["EURUSD"]["decimals"] # Intentionally delete from the class attribute
        with self.assertRaisesRegex(ValueError, "Missing required key 'decimals' in SYMBOL_SETTINGS for EURUSD."):
            self.rm.get_symbol_config("EURUSD")
        TestConfigRM.SYMBOL_SETTINGS["EURUSD"] = original_eurusd_settings


    def test_calculate_lot_size_eurusd(self):
        lots = self.rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09500)
        self.assertTrue(math.isclose(lots, 0.02), f"EURUSD lots: {lots}")
        lots_small_risk = self.rm.calculate_lot_size("EURUSD", 1.10000, 1.09500, risk_per_trade_usd=1)
        self.assertTrue(math.isclose(lots_small_risk, 0.00), f"EURUSD small risk lots: {lots_small_risk}")
        lots_tight_sl = self.rm.calculate_lot_size("EURUSD", 1.10000, 1.09990)
        self.assertTrue(math.isclose(lots_tight_sl, 1.00), f"EURUSD tight SL lots: {lots_tight_sl}")

    def test_calculate_lot_size_usdjpy(self):
        lots = self.rm.calculate_lot_size("USDJPY", entry_price=130.000, sl_price=130.500)
        self.assertTrue(math.isclose(lots, 0.02), f"USDJPY lots: {lots}")

    def test_calculate_lot_size_xauusd(self):
        lots = self.rm.calculate_lot_size("XAUUSD", entry_price=1950.00, sl_price=1940.00)
        self.assertTrue(math.isclose(lots, 0.01), f"XAUUSD $10 SL lots: {lots}")
        lots_tight_sl = self.rm.calculate_lot_size("XAUUSD", entry_price=1950.00, sl_price=1949.50)
        self.assertTrue(math.isclose(lots_tight_sl, 0.20), f"XAUUSD $0.50 SL lots: {lots_tight_sl}")

    def test_calculate_lot_size_nodecimals(self):
        lots = self.rm.calculate_lot_size("NODECIMALS", entry_price=3000, sl_price=2990)
        self.assertTrue(math.isclose(lots, 1.0), f"NODECIMALS 10pt SL lots: {lots}")

    def test_lot_size_sl_equals_entry(self):
        lots = self.rm.calculate_lot_size("EURUSD", 1.10000, 1.10000)
        self.assertEqual(lots, 0.0)

    def test_lot_size_zero_risk(self):
        lots = self.rm.calculate_lot_size("EURUSD", 1.10000, 1.09500, risk_per_trade_usd=0)
        self.assertEqual(lots, 0.0)

    def test_margin_check_excessive_trade(self):
        # SL of 0.00001 (0.1 standard pip for EURUSD). Risk $10.
        # sl_pips = 0.1. Value at risk per lot = 0.1 * $10 = $1. Raw lots = $10 / $1 = 10 lots.
        # Margin for 10 lots EURUSD at 1.10 = (10 * 100,000 * 1.10) / 100 = 11000 USD.
        # This is > 0.5 * balance (5000). Should be 0.
        lots = self.rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09999, risk_per_trade_usd=10)
        self.assertEqual(lots, 0.0, f"EURUSD excessive margin trade lots: {lots}")

    def test_account_balance_methods(self):
        self.assertEqual(self.rm.get_account_balance(), 10000)
        self.rm.update_account_balance(12000)
        self.assertEqual(self.rm.get_account_balance(), 12000)

    def test_can_open_trade_placeholder(self):
        self.assertTrue(self.rm.can_open_trade("EURUSD", 0.01, 1.10000))
        self.assertFalse(self.rm.can_open_trade("EURUSD", 0.00, 1.10000))
        self.assertTrue(self.rm.can_open_trade("EURUSD", 7.0, 1.10000)) # Margin = 7700 ( < 0.8 * 10k)
        self.assertFalse(self.rm.can_open_trade("EURUSD", 8.0, 1.10000))# Margin = 8800 ( > 0.8 * 10k)

if __name__ == '__main__':
    # This __main__ block is for running tests directly using `python path/to/this_file.py`
    # The setUpClass and tearDownClass will handle the config mocking.
    # The critical part is that `sys.modules['grid_trader.config']` is replaced *before*
    # unittest.main() starts discovering and loading the TestRiskManager class and its imports.
    # If this file is run via `python -m unittest ...`, this __main__ block won't execute.
    # The subtask runner should use `python -m unittest ...` style.

    # For direct execution, the patching needs to happen before unittest.main()
    # The setUpClass method is the standard unittest way to do this.
    unittest.main(verbosity=2)
