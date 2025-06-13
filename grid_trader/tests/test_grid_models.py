# grid_trader/tests/test_grid_models.py
import unittest
import pandas as pd
import numpy as np
from typing import Dict
import sys

from grid_trader import config as package_config
from grid_trader.engine.risk_manager import RiskManager

from grid_trader.models.base_model import BaseGridModel
from grid_trader.models.volatility_grid import VolatilityGridModel
from grid_trader.models.dual_grid import DualGridModel
from grid_trader.models.static_grid import StaticGridModel
from grid_trader.models.pyramid_grid import PyramidGridModel
from grid_trader.models.structure_grid import StructureGridModel
from grid_trader.models.range_grid import RangeGridModel

from grid_trader.utils.indicators import calculate_bollinger_bands, calculate_atr
from grid_trader.utils.price_structure import find_swing_highs, find_swing_lows

config = package_config

class TestConfigSetup:
    DEFAULT_RISK_PER_TRADE_USD = 10.0; LOG_LEVEL = "ERROR"
    LOG_FILE = "test_grid_models_temp.log"
    SYMBOL_SETTINGS = {
        "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5, "point_value": 0.00001},
        "USDJPY": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 0.9, "decimals": 3, "point_value": 0.001},
        "XAUUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 1.0, "decimals": 2, "point_value": 0.01}
    }
    DEFAULT_ATR_PERIOD = 14; SWING_PROXIMITY_ATR_MULTIPLIER = 0.5
    BOLLINGER_BANDS_PERIOD = 20; BOLLINGER_BANDS_STD_DEV = 2
    ATR_MEDIAN_PERIODS=20; ATR_HIGH_VOL_FACTOR=1.3; ATR_LOW_VOL_FACTOR=0.8
    EMA_SHORT_PERIOD=10; EMA_LONG_PERIOD=20; ADX_PERIOD=14; ADX_TREND_THRESHOLD=20
    BB_RANGE_WIDTH_THRESHOLD_PERCENT = 0.02

class MockRiskManagerTestModels:
    def __init__(self, symbol_settings=None):
        self.symbol_settings = symbol_settings if symbol_settings else TestConfigSetup.SYMBOL_SETTINGS
        self.account_balance = 10000
    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float, risk_per_trade_usd: float = None, account_balance_override: float = None) -> float:
        if abs(entry_price - sl_price) > 1e-9 :
            sym_conf = self.symbol_settings.get(symbol, {"min_lot_size": 0.01})
            return sym_conf.get("min_lot_size", 0.01)
        return 0.0
    def get_symbol_config(self, symbol: str) -> Dict:
        return self.symbol_settings.get(symbol, self.symbol_settings.get("EURUSD"))
    def get_account_balance(self) -> float: return self.account_balance

class ModelTestCase(unittest.TestCase):
    original_config_module_ref = None
    test_config_instance_for_class_patching = None # Renamed for clarity

    @classmethod
    def setUpClass(cls):
        cls.original_config_module_ref = sys.modules.get('grid_trader.config')
        cls.test_config_instance_for_class_patching = TestConfigSetup() # Instance created here
        sys.modules['grid_trader.config'] = cls.test_config_instance_for_class_patching

        global config
        config = cls.test_config_instance_for_class_patching

        modules_to_patch = [
            'grid_trader.models.base_model', 'grid_trader.models.volatility_grid',
            'grid_trader.models.dual_grid', 'grid_trader.models.static_grid',
            'grid_trader.models.pyramid_grid', 'grid_trader.models.structure_grid',
            'grid_trader.models.range_grid', 'grid_trader.utils.logger'
            # Not patching engine components as models should not import them directly
        ]
        for mod_name in modules_to_patch:
            if mod_name in sys.modules:
                sys.modules[mod_name].config = cls.test_config_instance_for_class_patching

    @classmethod
    def tearDownClass(cls):
        if cls.original_config_module_ref:
            sys.modules['grid_trader.config'] = cls.original_config_module_ref
        else:
            if 'grid_trader.config' in sys.modules: del sys.modules['grid_trader.config']

        global config
        config = package_config # Restore original package_config to this module's global 'config'

        modules_to_restore = [
            'grid_trader.models.base_model', 'grid_trader.models.volatility_grid',
            'grid_trader.models.dual_grid', 'grid_trader.models.static_grid',
            'grid_trader.models.pyramid_grid', 'grid_trader.models.structure_grid',
            'grid_trader.models.range_grid', 'grid_trader.utils.logger'
        ]
        for mod_name in modules_to_restore:
            if mod_name in sys.modules and cls.original_config_module_ref:
                 sys.modules[mod_name].config = cls.original_config_module_ref

    def setUp(self):
        # DIAGNOSTIC: Directly instantiate TestConfigSetup here for self.test_config
        # This bypasses potential issues with class attribute inheritance/timing for test_config_instance_ref
        self.test_config = TestConfigSetup()

        self.risk_manager = MockRiskManagerTestModels(symbol_settings=self.test_config.SYMBOL_SETTINGS)
        self.base_params_eurusd = {
            'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10000,
            'base_sl': 1.09000, 'base_tp': 1.12000, 'base_size_lots': 0.1, 'atr': 0.00100
        }
        self.hist_data_eurusd = pd.DataFrame({
            'Open': np.linspace(1.09500, 1.10000, 10),'High': np.linspace(1.09600, 1.10100, 10),
            'Low': np.linspace(1.09400, 1.09900, 10),'Close': np.linspace(1.09550, 1.10050, 10),
        }, index=pd.date_range(start='2023-01-01', periods=10, freq='D'))
        self.hist_data_eurusd[f'ATR_{self.test_config.DEFAULT_ATR_PERIOD}'] = calculate_atr(self.hist_data_eurusd, period=self.test_config.DEFAULT_ATR_PERIOD)

# Test Cases (remain unchanged from previous correct version)

class TestVolatilityGridModel(ModelTestCase):
    def test_generate_orders_buy(self):
        model = VolatilityGridModel(self.base_params_eurusd, self.hist_data_eurusd.copy(), self.risk_manager, num_levels=2, atr_multiplier=1.0)
        orders = model.generate_grid_orders()
        self.assertEqual(len(orders), 4)
        bs_order = next((o for o in orders if o['order_type'] == 'BUY_STOP' and o['entry_price'] > self.base_params_eurusd['base_price']), None)
        self.assertIsNotNone(bs_order); self.assertAlmostEqual(bs_order['entry_price'], 1.10100, 5)
        self.assertAlmostEqual(bs_order['sl'], 1.10000, 5); self.assertAlmostEqual(bs_order['tp'], 1.10200, 5)

    def test_zero_atr(self):
        params = self.base_params_eurusd.copy(); params['atr'] = 0.0
        with self.assertRaisesRegex(ValueError, "Missing critical base_trade_params"):
            VolatilityGridModel(params, self.hist_data_eurusd.copy(), self.risk_manager)

class TestDualGridModel(ModelTestCase):
    def test_generate_orders_both_types(self):
        model = DualGridModel(self.base_params_eurusd, self.hist_data_eurusd.copy(), self.risk_manager,
                              num_breakout_levels=1, num_reversal_levels=1, atr_multiplier_breakout=1.0,
                              atr_multiplier_reversal=0.5, stop_loss_atr_multiplier=1.0, take_profit_atr_multiplier=1.5)
        orders = model.generate_grid_orders()
        self.assertEqual(len(orders), 4)
        bs_order = next((o for o in orders if o['order_type'] == 'BUY_STOP'), None); self.assertIsNotNone(bs_order)
        self.assertAlmostEqual(bs_order['entry_price'], 1.10100, 5); self.assertAlmostEqual(bs_order['sl'], 1.10000, 5)
        self.assertAlmostEqual(bs_order['tp'], 1.10250, 5)
        bl_order = next((o for o in orders if o['order_type'] == 'BUY_LIMIT'), None); self.assertIsNotNone(bl_order)
        self.assertAlmostEqual(bl_order['entry_price'], 1.09950, 5); self.assertAlmostEqual(bl_order['sl'], 1.09850, 5)
        self.assertAlmostEqual(bl_order['tp'], 1.10100, 5)

class TestStaticGridModel(ModelTestCase):
    def test_generate_buy_limits(self):
        params = self.base_params_eurusd.copy(); params['base_price'] = 1.10000; params['base_sl'] = 1.09000
        model = StaticGridModel(params, self.hist_data_eurusd.copy(), self.risk_manager, num_grid_lines=4, use_base_tp_for_all=False, individual_tp_rr_ratio=1.0)
        orders = model.generate_grid_orders()
        self.assertEqual(len(orders), 4)
        entry1 = params['base_price'] - (abs(params['base_price'] - params['base_sl']) / (4+1))
        sl1 = params['base_sl']; tp1 = entry1 + abs(entry1 - sl1)
        order1 = orders[0]; self.assertEqual(order1['order_type'], 'BUY_LIMIT')
        self.assertAlmostEqual(order1['entry_price'], entry1, 5); self.assertAlmostEqual(order1['sl'], sl1, 5); self.assertAlmostEqual(order1['tp'], tp1, 5)

    def test_invalid_setup_buy(self):
        params = self.base_params_eurusd.copy(); params['base_price'] = 1.08900
        with self.assertRaisesRegex(ValueError, "not appropriately positioned"):
            StaticGridModel(params, self.hist_data_eurusd.copy(), self.risk_manager)

class TestPyramidGridModel(ModelTestCase):
    def test_generate_buy_stops_sl_previous(self):
        model = PyramidGridModel(self.base_params_eurusd, self.hist_data_eurusd.copy(), self.risk_manager, num_pyramid_levels=2, atr_multiplier_spacing=1.0, sl_at_previous_level=True, tp_atr_multiplier=2.0)
        orders = model.generate_grid_orders()
        self.assertEqual(len(orders), 2)
        order1 = orders[0]; self.assertEqual(order1['order_type'], 'BUY_STOP')
        self.assertAlmostEqual(order1['entry_price'], 1.10100, 5); self.assertAlmostEqual(order1['sl'], 1.10000, 5); self.assertAlmostEqual(order1['tp'], 1.10300, 5)
        order2 = orders[1]; self.assertAlmostEqual(order2['entry_price'], 1.10200, 5); self.assertAlmostEqual(order2['sl'], 1.10100, 5)

class TestStructureGridModel(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.hist_data_struct = pd.DataFrame({
            'Open':  [1.100, 1.102, 1.098, 1.105, 1.103, 1.100, 1.107, 1.104],'High':  [1.103, 1.106, 1.099, 1.108, 1.105, 1.102, 1.110, 1.106],
            'Low':   [1.098, 1.100, 1.095, 1.102, 1.101, 1.097, 1.105, 1.102],'Close': [1.102, 1.101, 1.097, 1.107, 1.102, 1.099, 1.106, 1.103]
        }, index=pd.date_range(start='2023-01-01', periods=8, freq='D'))
        self.hist_data_struct['SwingHigh'] = find_swing_highs(self.hist_data_struct, n_bars=1)
        self.hist_data_struct['SwingLow'] = find_swing_lows(self.hist_data_struct, n_bars=1)
        self.hist_data_struct[f'ATR_{self.test_config.DEFAULT_ATR_PERIOD}'] = 0.00100
        self.base_params_struct = self.base_params_eurusd.copy(); self.base_params_struct['base_price'] = 1.10400; self.base_params_struct['atr'] = 0.00100

    def test_generate_orders_buy_structure(self):
        model = StructureGridModel(self.base_params_struct, self.hist_data_struct.copy(), self.risk_manager, num_swing_levels_to_consider=2, swing_n_bars=1, entry_buffer_atr_multiplier=0.1, sl_atr_multiplier=1.0, tp_atr_multiplier=1.5)
        orders = model.generate_grid_orders()
        self.assertEqual(len(orders), 4)

class TestRangeGridModel(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.hist_data_range_long = pd.DataFrame({
            'Open': np.linspace(1.09500, 1.10000, 30), 'High': np.linspace(1.09600, 1.10100, 30),
            'Low': np.linspace(1.09400, 1.09900, 30), 'Close': np.linspace(1.09550, 1.10050, 30),
        }, index=pd.date_range(start='2023-01-01', periods=30, freq='D'))
        bb_df = calculate_bollinger_bands(self.hist_data_range_long, period=self.test_config.BOLLINGER_BANDS_PERIOD, std_dev=self.test_config.BOLLINGER_BANDS_STD_DEV)
        self.hist_data_range_long = pd.concat([self.hist_data_range_long, bb_df], axis=1)
        self.hist_data_range_long[f'ATR_{self.test_config.DEFAULT_ATR_PERIOD}'] = 0.00050
        self.hist_data_range_long = self.hist_data_range_long.dropna()

    def test_generate_orders_bollinger(self):
        if self.hist_data_range_long.empty : self.skipTest("Not enough data for BB in RangeGrid test.")
        latest_bb_upper = self.hist_data_range_long[f'BB_Upper_{self.test_config.BOLLINGER_BANDS_PERIOD}_{self.test_config.BOLLINGER_BANDS_STD_DEV}'].iloc[-1]
        self.assertFalse(pd.isna(latest_bb_upper), "Latest BB Upper is NaN")
        params_range = self.base_params_eurusd.copy()
        params_range['base_price'] = self.hist_data_range_long['Close'].iloc[-1]
        params_range['atr'] = self.hist_data_range_long[f'ATR_{self.test_config.DEFAULT_ATR_PERIOD}'].iloc[-1]
        model = RangeGridModel(params_range, self.hist_data_range_long.copy(), self.risk_manager, num_grid_lines_per_side=2, range_definition_method='bollinger', bb_period=self.test_config.BOLLINGER_BANDS_PERIOD, bb_std_dev=self.test_config.BOLLINGER_BANDS_STD_DEV, spacing_as_fraction_of_range=0.2, sl_buffer_atr_multiplier=0.5, tp_target_other_side_of_range=True)
        orders = model.generate_grid_orders()
        self.assertTrue(len(orders) <= 4)
        if orders: self.assertIn(orders[0]['order_type'], ['BUY_LIMIT', 'SELL_LIMIT'])

if __name__ == '__main__':
    test_config_instance_main = TestConfigSetup()
    sys.modules['grid_trader.config'] = test_config_instance_main

    modules_to_patch_for_main = [
        'grid_trader.models.base_model', 'grid_trader.models.volatility_grid',
        'grid_trader.models.dual_grid', 'grid_trader.models.static_grid',
        'grid_trader.models.pyramid_grid', 'grid_trader.models.structure_grid',
        'grid_trader.models.range_grid', 'grid_trader.utils.logger',
        'grid_trader.engine.risk_manager', 'grid_trader.engine.order_manager',
        'grid_trader.engine.grid_manager', 'grid_trader.engine.signal_router',
    ]
    for mod_name in modules_to_patch_for_main:
        if mod_name in sys.modules:
            sys.modules[mod_name].config = test_config_instance_main
            if hasattr(sys.modules[mod_name], 'package_config'):
                 sys.modules[mod_name].package_config = test_config_instance_main
    config = test_config_instance_main
    unittest.main(verbosity=2)
