# grid_trader/tests/test_order_logic.py
import unittest
import time
import pandas as pd
import sys
from typing import Dict # Added for type hint

from grid_trader import config as package_config # Original config
from grid_trader.engine.order_manager import OrderManager, Order, OrderStatus, OrderType
from grid_trader.engine.risk_manager import RiskManager


# --- Test Configuration for OrderManager Tests ---
class TestConfigOM:
    DEFAULT_MAX_REGENERATION_ATTEMPTS = 2
    DEFAULT_COOLDOWN_PERIOD_BARS = 1
    BAR_DURATION_SECONDS = 0.0001 # Effectively no cooldown for rapid tests
    DEFAULT_SL_TP_WIDENING_FACTOR = 1.2
    LOG_LEVEL = "ERROR"
    LOG_FILE = "test_order_logic_temp.log"
    SYMBOL_SETTINGS = {
        "EURUSD": {"decimals": 5, "point_value": 0.00001},
    }
    def get(self, key, default): # Mock for config.get()
        return getattr(self, key, default)

# --- Mock RiskManager for OrderManager Tests ---
class MockRiskManagerForOM:
    def __init__(self, config_to_use=None):
        self.config = config_to_use if config_to_use else TestConfigOM()

    def calculate_lot_size(self, *args, **kwargs):
        return 0.01
    def get_symbol_config(self, symbol):
        return self.config.SYMBOL_SETTINGS.get(symbol, {"decimals": 5})


class TestOrderManager(unittest.TestCase):

    original_config_module = None
    test_config_instance = None # Store the instance for all tests in this class

    @classmethod
    def setUpClass(cls):
        cls.original_config_module = sys.modules.get('grid_trader.config')
        cls.test_config_instance = TestConfigOM()
        sys.modules['grid_trader.config'] = cls.test_config_instance

        # Patch config in already imported modules that OrderManager might use, or OM itself
        modules_to_patch = ['grid_trader.engine.order_manager', 'grid_trader.utils.logger']
        for mod_name in modules_to_patch:
            if mod_name in sys.modules:
                sys.modules[mod_name].config = cls.test_config_instance

    @classmethod
    def tearDownClass(cls):
        if cls.original_config_module:
            sys.modules['grid_trader.config'] = cls.original_config_module
        else:
            if 'grid_trader.config' in sys.modules and isinstance(sys.modules['grid_trader.config'], TestConfigOM):
                 del sys.modules['grid_trader.config']

        modules_to_restore = ['grid_trader.engine.order_manager', 'grid_trader.utils.logger']
        for mod_name in modules_to_restore:
            if mod_name in sys.modules and cls.original_config_module:
                 sys.modules[mod_name].config = cls.original_config_module


    def setUp(self):
        # RiskManager needs to see the test config for SYMBOL_SETTINGS
        self.mock_risk_manager = MockRiskManagerForOM(config_to_use=self.test_config_instance)
        # OrderManager will be initialized with the patched config active
        self.om = OrderManager(risk_manager=self.mock_risk_manager)


    def test_order_creation_and_placement(self):
        order = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1100, 0.01, "grid1")
        self.assertIsNotNone(order)
        self.assertIn(order.order_id, self.om.orders)
        self.assertIn(order.order_id, self.om.pending_orders)
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.grid_id, "grid1")
        self.assertEqual(order.original_order_id, order.order_id)

    def test_invalid_order_placement_bad_type(self):
        order = self.om.place_new_order("EURUSD", "INVALID_TYPE", 1.1000, 1.0950, 1.1100, 0.01)
        self.assertIsNone(order)

    def test_invalid_order_placement_bad_sltp(self):
        order1 = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.1000, 1.1100, 0.01)
        self.assertIsNone(order1)
        order2 = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1000, 0.01)
        self.assertIsNone(order2)

    def test_cancel_pending_order(self):
        order = self.om.place_new_order("EURUSD", "BUY_LIMIT", 1.0900, 1.0850, 1.1000, 0.01)
        self.assertIsNotNone(order) # Ensure order was placed
        cancelled = self.om.cancel_order(order.order_id)
        self.assertTrue(cancelled)
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertNotIn(order.order_id, self.om.pending_orders)

    def test_cannot_cancel_filled_order(self):
        order = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1100, 0.01)
        self.assertIsNotNone(order)
        market_data = {"EURUSD": {"high": 1.1000, "low": 1.0990}}
        self.om.check_pending_orders(market_data)
        self.assertEqual(order.status, OrderStatus.FILLED)
        cancelled = self.om.cancel_order(order.order_id)
        self.assertFalse(cancelled)
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_modify_order_sltp(self):
        order = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1100, 0.01)
        self.assertIsNotNone(order)
        modified = self.om.modify_order_sl_tp(order.order_id, new_sl=1.0940, new_tp=1.1110)
        self.assertTrue(modified)
        self.assertEqual(order.sl_price, 1.0940)
        self.assertEqual(order.tp_price, 1.1110)

    def test_fill_buy_stop(self):
        order = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1100, 0.01)
        self.assertIsNotNone(order)
        market_data = {"EURUSD": {"high": 1.1005, "low": 1.0995}}
        self.om.check_pending_orders(market_data)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertIn(order.order_id, self.om.active_positions)
        self.assertNotIn(order.order_id, self.om.pending_orders)
        self.assertIsNotNone(order.fill_time)
        self.assertEqual(order.fill_price, 1.1000)

    def test_fill_sell_limit(self):
        order = self.om.place_new_order("EURUSD", "SELL_LIMIT", 1.1000, 1.1050, 1.0900, 0.01)
        self.assertIsNotNone(order)
        market_data = {"EURUSD": {"high": 1.1005, "low": 1.0995}}
        self.om.check_pending_orders(market_data)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.fill_price, 1.1000)

    def test_sl_hit_long_position(self):
        order = self.om.place_new_order("EURUSD", "BUY_STOP", 1.1000, 1.0950, 1.1100, 0.01)
        self.assertIsNotNone(order); self.om.check_pending_orders({"EURUSD": {"high": 1.1000, "low": 1.0990}})
        market_data_sl = {"EURUSD": {"high": 1.0960, "low": 1.0945}}; self.om.check_active_positions(market_data_sl)
        self.assertEqual(order.status, OrderStatus.STOPPED_OUT); self.assertEqual(order.close_price, 1.0950)
        self.assertNotIn(order.order_id, self.om.active_positions)

    def test_tp_hit_short_position(self):
        order = self.om.place_new_order("EURUSD", "SELL_STOP", 1.1000, 1.1050, 1.0900, 0.01)
        self.assertIsNotNone(order); self.om.check_pending_orders({"EURUSD": {"high": 1.1005, "low": 1.0998}})
        market_data_tp = {"EURUSD": {"high": 1.0905, "low": 1.0895}}; self.om.check_active_positions(market_data_tp)
        self.assertEqual(order.status, OrderStatus.TP_HIT); self.assertEqual(order.close_price, 1.0900)

    def test_regeneration_cycle(self):
        params = {"symbol":"EURUSD", "order_type_str":"BUY_LIMIT", "entry_price":1.0900, "sl_price":1.0850, "tp_price":1.1000, "lot_size":0.01, "grid_id":"RegenTest"}
        order1 = self.om.place_new_order(**params); self.assertIsNotNone(order1)
        original_id_slot = order1.original_order_id

        self.om.check_pending_orders({"EURUSD": {"low": 1.0900, "high":1.0905}}); self.assertEqual(order1.status, OrderStatus.FILLED)
        self.om.check_active_positions({"EURUSD": {"low": 1.0845, "high":1.0855}}); self.assertEqual(order1.status, OrderStatus.STOPPED_OUT)

        self.assertTrue(self.om.needs_regeneration(order1.order_id))
        regen_params1 = self.om.get_order_details_for_regeneration(order1.order_id, widen_sltp_factor=1.2)
        self.assertIsNotNone(regen_params1); self.assertNotEqual(regen_params1['sl_price'], order1.initial_sl_price)

        order2 = self.om.place_new_order(**regen_params1); self.assertIsNotNone(order2)
        self.assertEqual(order2.original_order_id, original_id_slot); self.assertEqual(order2.regeneration_attempts, 1)
        self.assertEqual(self.om.regeneration_counts[original_id_slot], 1)

        self.om.check_pending_orders({"EURUSD": {"low": order2.entry_price, "high": order2.entry_price + 0.0001}})
        self.om.check_active_positions({"EURUSD": {"low": order2.sl_price - 0.0001, "high": order2.sl_price + 0.0001}})
        self.assertEqual(order2.status, OrderStatus.STOPPED_OUT)

        self.assertTrue(self.om.needs_regeneration(order2.order_id))
        regen_params2 = self.om.get_order_details_for_regeneration(order2.order_id, widen_sltp_factor=1.5)
        order3 = self.om.place_new_order(**regen_params2); self.assertIsNotNone(order3)
        self.assertEqual(order3.original_order_id, original_id_slot); self.assertEqual(order3.regeneration_attempts, 2)
        self.assertEqual(self.om.regeneration_counts[original_id_slot], 2)

        self.om.check_pending_orders({"EURUSD": {"low": order3.entry_price, "high": order3.entry_price + 0.0001}})
        self.om.check_active_positions({"EURUSD": {"low": order3.sl_price - 0.0001, "high": order3.sl_price + 0.0001}})
        self.assertEqual(order3.status, OrderStatus.STOPPED_OUT)

        self.assertFalse(self.om.needs_regeneration(order3.order_id)) # Max attempts (2) reached

if __name__ == '__main__':
    test_config_instance_main = TestConfigSetup()
    sys.modules['grid_trader.config'] = test_config_instance_main

    modules_to_patch_for_main = ['grid_trader.engine.order_manager', 'grid_trader.utils.logger']
    for mod_name in modules_to_patch_for_main:
        if mod_name in sys.modules:
            sys.modules[mod_name].config = test_config_instance_main
            if hasattr(sys.modules[mod_name], 'package_config'): # For main.py style alias
                 sys.modules[mod_name].package_config = test_config_instance_main

    # Rebind global 'config' in this test file for ModelTestCase.setUpClass if it runs before __main__
    # Not strictly needed here as ModelTestCase is not in this file, but good practice if it were.
    config = test_config_instance_main

    unittest.main(verbosity=2)
