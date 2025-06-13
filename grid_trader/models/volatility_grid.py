# grid_trader/models/volatility_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd # Added pandas import
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from .. import config # Assuming config.py is one level up

logger = get_logger(__name__)

class VolatilityGridModel(BaseGridModel):
    """
    Volatility Grid Model: Generates opposite-direction pending orders spaced by ATR.
    If base direction is 'buy', it generates Sell Stop orders below base_price
    and Buy Stop orders above base_price.
    If base direction is 'sell', it generates Buy Stop orders above base_price
    and Sell Stop orders below base_price.
    This creates a channel of stop orders for breakouts.
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_levels: int = 3, atr_multiplier: float = 1.0):
        """
        Args:
            num_levels (int): Number of grid levels to generate on each side of the base_price.
            atr_multiplier (float): Multiplier for ATR to determine spacing.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_levels = int(max(1, num_levels))
        self.atr_multiplier = float(max(0.1, atr_multiplier))
        logger.info(f"VolatilityGridModel initialized for {self.symbol} with {self.num_levels} levels, ATR multiplier {self.atr_multiplier}")

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        if self.current_atr <= 0: # Changed from == 0 to <= 0
            logger.warning(f"ATR is not positive ({self.current_atr}) for {self.symbol}. Cannot generate Volatility Grid.")
            return []

        spacing = self.current_atr * self.atr_multiplier
        if spacing <= 0: # Changed from == 0 to <= 0
            logger.warning(f"Grid spacing is not positive (ATR: {self.current_atr}, Multiplier: {self.atr_multiplier}). Cannot generate grid.")
            return []

        # Determine decimal places for rounding prices
        # Priority: 1. From config, 2. Heuristic from base_price, 3. Default
        decimals = 4 # Default
        try:
            # Access config through the imported 'config' module object
            symbol_config = config.SYMBOL_SETTINGS.get(self.symbol, {})
            if 'decimals' in symbol_config:
                decimals = int(symbol_config['decimals'])
            else:
                # Heuristic based on base_price string representation
                base_price_str = str(self.base_price)
                if '.' in base_price_str:
                    decimals = len(base_price_str.split('.')[-1])
                elif "JPY" in self.symbol.upper(): # JPY pairs might not have decimals if e.g. 130
                    decimals = 2
                # Cap decimals for sanity, e.g. max 5 for FX, 2 for XAU
                if "XAU" in self.symbol.upper() or "XAG" in self.symbol.upper():
                    decimals = min(decimals, 2) # Gold typically 2
                elif "JPY" in self.symbol.upper():
                     decimals = min(decimals, 3) # JPY pairs typically 2 or 3
                else:
                    decimals = min(decimals, 5) # Most other FX 4 or 5
        except Exception as e:
            logger.warning(f"Could not determine decimals for {self.symbol} accurately, defaulting to {decimals}. Error: {e}")


        for i in range(1, self.num_levels + 1):
            price_offset = i * spacing

            # Orders above base_price
            entry_above = round(self.base_price + price_offset, decimals)
            sl_above = round(entry_above - spacing, decimals) # SL is one grid level below
            tp_above = round(entry_above + spacing, decimals) # TP is one grid level above

            # Orders below base_price
            entry_below = round(self.base_price - price_offset, decimals)
            sl_below = round(entry_below + spacing, decimals) # SL is one grid level above
            tp_below = round(entry_below - spacing, decimals) # TP is one grid level below

            lot_size_above = self._calculate_lot_size(entry_price=entry_above, sl_price=sl_above)
            lot_size_below = self._calculate_lot_size(entry_price=entry_below, sl_price=sl_below)

            order_type_above = 'BUY_STOP'
            order_type_below = 'SELL_STOP'

            if lot_size_above > 0:
                self.grid_orders.append({
                    'symbol': self.symbol, 'order_type': order_type_above,
                    'entry_price': entry_above, 'sl': sl_above, 'tp': tp_above,
                    'lot_size': lot_size_above, 'grid_id': f"VG_{self.symbol}_{order_type_above}_{i}"
                })

            if lot_size_below > 0:
                self.grid_orders.append({
                    'symbol': self.symbol, 'order_type': order_type_below,
                    'entry_price': entry_below, 'sl': sl_below, 'tp': tp_below,
                    'lot_size': lot_size_below, 'grid_id': f"VG_{self.symbol}_{order_type_below}_{i}"
                })

        logger.info(f"Generated {len(self.grid_orders)} orders for VolatilityGridModel ({self.symbol}).")
        if self.grid_orders:
             unique_orders = []
             seen_entries = set()
             for order in self.grid_orders:
                 order_key = (order['order_type'], order['entry_price'])
                 if order_key not in seen_entries:
                     unique_orders.append(order)
                     seen_entries.add(order_key)
             if len(unique_orders) != len(self.grid_orders):
                 logger.warning(f"Removed duplicate orders. Original: {len(self.grid_orders)}, Unique: {len(unique_orders)}")
                 self.grid_orders = unique_orders

        return self.grid_orders

if __name__ == '__main__':
    # Mocking environment for testing
    class MockRiskManager:
        def get_account_balance(self): return 10000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0

            cfg = sys.modules.get('grid_trader.config', config) # Use the potentially mocked config

            symbol_details = cfg.SYMBOL_SETTINGS.get(symbol, {})
            decimals = symbol_details.get('decimals', 4)
            pip_value_per_lot = symbol_details.get('pip_value_per_lot', 10)

            point_value = 10**(-decimals)
            sl_pips = price_diff / point_value

            if sl_pips <= 0: return 0.0 # SL pips must be positive

            lots = risk_per_trade_usd / (sl_pips * pip_value_per_lot)
            min_lot = symbol_details.get('min_lot_size', 0.01)
            lot_step = symbol_details.get('lot_step', 0.01)

            lots = max(min_lot, round(lots / lot_step) * lot_step if lot_step > 0 else lots)
            logger.debug(f"MockRM VGrid: Lots for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} -> {lots} lots (SL pips: {sl_pips})")
            return lots if lots > 0 else 0.0

    class MainConfigVolGrid:
        DEFAULT_RISK_PER_TRADE_USD = 10.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_volatility_grid.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5},
            "USDJPY": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 0.9, "decimals": 3}, # Assuming 1 pip = 0.01 for JPY, so 0.9 for 0.01 lots
            "XAUUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 1.0, "decimals": 2} # Assuming 1 pip = $0.01 for XAU, so 1.0 for 0.01 lots
        }

    import sys
    # Mock grid_trader.config for this test run
    sys.modules['grid_trader.config'] = MainConfigVolGrid
    # Also explicitly patch the 'config' object already imported by 'grid_trader.utils.logger'.
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigVolGrid

    # Re-initialize logger for this __main__ scope to use the mocked config
    logger = get_logger(__name__)

    mock_rm = MockRiskManager()
    hist_data = pd.DataFrame({'Close': [1.10000, 1.10100], 'High': [1.10200, 1.10300], 'Low': [1.09900, 1.10000]})

    logger.info("--- Testing EURUSD (BUY example) ---")
    base_params_buy_eurusd = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10150,
        'base_sl': 1.09000, 'base_tp': 1.12000, 'base_size_lots': 0.1, 'atr': 0.00100
    }
    vol_grid_buy_eurusd = VolatilityGridModel(base_params_buy_eurusd, hist_data, mock_rm, num_levels=3, atr_multiplier=1.0)
    eurusd_orders = vol_grid_buy_eurusd.generate_grid_orders()
    for order in eurusd_orders: logger.info(f"  {order}")

    logger.info("--- Testing USDJPY (SELL example) ---")
    base_params_sell_jpy = {
        'symbol': 'USDJPY', 'direction': 'sell', 'base_price': 130.500,
        'base_sl': 131.500, 'base_tp': 128.000, 'base_size_lots': 0.1, 'atr': 0.200
    }
    vol_grid_sell_jpy = VolatilityGridModel(base_params_sell_jpy, hist_data, mock_rm, num_levels=2, atr_multiplier=0.5)
    jpy_orders = vol_grid_sell_jpy.generate_grid_orders()
    for order in jpy_orders: logger.info(f"  {order}")

    logger.info("--- Testing XAUUSD (BUY example) ---")
    base_params_buy_xauusd = {
        'symbol': 'XAUUSD', 'direction': 'buy', 'base_price': 1950.50,
        'base_sl': 1940.00, 'base_tp': 1980.00, 'base_size_lots': 0.1, 'atr': 5.00
    }
    vol_grid_buy_xauusd = VolatilityGridModel(base_params_buy_xauusd, hist_data, mock_rm, num_levels=3, atr_multiplier=1.0)
    xauusd_orders = vol_grid_buy_xauusd.generate_grid_orders()
    for order in xauusd_orders: logger.info(f"  {order}")

    logger.info("--- Testing Zero ATR ---")
    base_params_zero_atr = base_params_buy_eurusd.copy()
    base_params_zero_atr['atr'] = 0.0
    try:
        vol_grid_zero_atr = VolatilityGridModel(base_params_zero_atr, hist_data, mock_rm)
        # The following lines likely won't be reached if ValueError is raised in __init__
        zero_atr_orders = vol_grid_zero_atr.generate_grid_orders()
        logger.info(f"Zero ATR orders count: {len(zero_atr_orders)}")
    except ValueError as e:
        logger.info(f"Caught expected ValueError for zero ATR: {e}")

    logger.info("--- Testing with ATR that might cause overlapping prices if not careful ---")
    base_params_overlap_atr = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10150,
        'base_sl': 1.09000, 'base_tp': 1.12000, 'base_size_lots': 0.1, 'atr': 0.00001
    }
    vol_grid_overlap = VolatilityGridModel(base_params_overlap_atr, hist_data, mock_rm, num_levels=1, atr_multiplier=1.0)
    overlap_orders = vol_grid_overlap.generate_grid_orders()
    for order in overlap_orders: logger.info(f"  {order}")
