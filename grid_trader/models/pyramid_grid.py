# grid_trader/models/pyramid_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from .. import config

logger = get_logger(__name__)

class PyramidGridModel(BaseGridModel):
    """
    Pyramiding Grid Model: Adds to a position by placing same-direction Buy Stops
    (for a base 'buy' trade) or Sell Stops (for a base 'sell' trade) as price
    moves favorably.
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_pyramid_levels: int = 3,
                 atr_multiplier_spacing: float = 1.0,
                 sl_at_previous_level: bool = True, # If True, SL is at previous order's entry or base_price
                 sl_atr_multiplier: float = 1.0,    # Used if sl_at_previous_level is False
                 tp_atr_multiplier: float = 2.0):
        """
        Args:
            num_pyramid_levels (int): Number of additional orders to place.
            atr_multiplier_spacing (float): ATR multiplier for spacing between pyramid orders.
            sl_at_previous_level (bool): If True, SL for order N is at entry of order N-1.
                                         If False, SL is entry -/+ (ATR * sl_atr_multiplier).
            sl_atr_multiplier (float): ATR multiplier for SL if not using previous level.
            tp_atr_multiplier (float): ATR multiplier for TP distance from entry.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_pyramid_levels = int(max(1, num_pyramid_levels)) # At least 1 pyramid order
        self.atr_multiplier_spacing = float(max(0.1, atr_multiplier_spacing))
        self.sl_at_previous_level = sl_at_previous_level
        self.sl_atr_multiplier = float(max(0.1, sl_atr_multiplier))
        self.tp_atr_multiplier = float(max(0.1, tp_atr_multiplier))

        logger.info(f"PyramidGridModel initialized for {self.symbol} {self.direction}: Levels={self.num_pyramid_levels}, "
                    f"SpacingATR_x{self.atr_multiplier_spacing}, SLPrevLvl={self.sl_at_previous_level}, "
                    f"SL_ATR_x{self.sl_atr_multiplier}, TP_ATR_x{self.tp_atr_multiplier}")

    def _get_decimals(self) -> int:
        decimals = 4
        try:
            # Access config through the imported 'config' module object
            symbol_config = config.SYMBOL_SETTINGS.get(self.symbol, {})
            if 'decimals' in symbol_config: decimals = int(symbol_config['decimals'])
            else:
                base_price_str = str(self.base_price)
                if '.' in base_price_str: decimals = len(base_price_str.split('.')[-1])
                elif "JPY" in self.symbol.upper(): decimals = 2
                if "XAU" in self.symbol.upper() or "XAG" in self.symbol.upper(): decimals = min(decimals, 2)
                elif "JPY" in self.symbol.upper(): decimals = min(decimals, 3)
                else: decimals = min(decimals, 5)
        except Exception as e:
            logger.warning(f"Could not determine decimals for {self.symbol} (PyramidGrid), defaulting to {decimals}. Error: {e}", exc_info=True)
        return decimals

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        if self.current_atr <= 0:
            logger.warning(f"PyramidGrid: ATR is not positive ({self.current_atr}) for {self.symbol}. No orders generated.")
            return []

        decimals = self._get_decimals()
        spacing = self.current_atr * self.atr_multiplier_spacing
        min_pip_spacing = 1 / (10**decimals)
        if spacing < min_pip_spacing / 2: # Spacing less than half a pip
            logger.warning(f"PyramidGrid: Calculated spacing ({spacing:.{decimals+2}f}) is too small for {self.symbol}. No orders generated.")
            return []

        tp_distance = self.current_atr * self.tp_atr_multiplier
        sl_atr_dist = self.current_atr * self.sl_atr_multiplier

        last_entry_price = self.base_price

        for i in range(1, self.num_pyramid_levels + 1):
            if self.direction.lower() == 'buy':
                order_type = 'BUY_STOP'
                entry_price = round(last_entry_price + spacing, decimals)

                if self.sl_at_previous_level:
                    order_sl = round(last_entry_price, decimals)
                else:
                    order_sl = round(entry_price - sl_atr_dist, decimals)

                order_tp = round(entry_price + tp_distance, decimals)

                if entry_price <= order_sl:
                    logger.warning(f"PyramidGrid (Buy): Entry {entry_price} not above SL {order_sl} for level {i}. Skipping subsequent levels.")
                    break
                if entry_price >= order_tp:
                    logger.warning(f"PyramidGrid (Buy): Entry {entry_price} not below TP {order_tp} for level {i}. Skipping subsequent levels.")
                    break

            elif self.direction.lower() == 'sell':
                order_type = 'SELL_STOP'
                entry_price = round(last_entry_price - spacing, decimals)

                if self.sl_at_previous_level:
                    order_sl = round(last_entry_price, decimals)
                else:
                    order_sl = round(entry_price + sl_atr_dist, decimals)

                order_tp = round(entry_price - tp_distance, decimals)

                if entry_price >= order_sl:
                    logger.warning(f"PyramidGrid (Sell): Entry {entry_price} not below SL {order_sl} for level {i}. Skipping subsequent levels.")
                    break
                if entry_price <= order_tp:
                    logger.warning(f"PyramidGrid (Sell): Entry {entry_price} not above TP {order_tp} for level {i}. Skipping subsequent levels.")
                    break
            else:
                logger.error(f"PyramidGrid: Invalid direction '{self.direction}'. Skipping.")
                return []


            lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)

            if lot_size > 0:
                self.grid_orders.append({
                    'symbol': self.symbol, 'order_type': order_type,
                    'entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,
                    'lot_size': lot_size, 'grid_id': f"PG_{self.symbol}_{order_type[0]}{order_type[-1]}_{i}"
                })
                last_entry_price = entry_price
            else:
                logger.warning(f"PyramidGrid: Lot size is zero for level {i} ({self.symbol} {entry_price}). Skipping subsequent levels.")
                break

        logger.info(f"Generated {len(self.grid_orders)} orders for PyramidGridModel ({self.symbol}).")
        return self.grid_orders

if __name__ == '__main__':
    class MockRiskManagerPG:
        def get_account_balance(self): return 50000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0

            cfg = sys.modules.get('grid_trader.config', config)

            symbol_details = cfg.SYMBOL_SETTINGS.get(symbol, {})
            decimals = symbol_details.get('decimals', 4)
            pip_value_per_lot = symbol_details.get('pip_value_per_lot', 10)
            point_value = 10**(-decimals)
            sl_pips = price_diff / point_value

            if sl_pips < 1 and symbol != "XAUUSD": # Allow smaller SL pips for XAUUSD due to its larger price movements
                 # For XAUUSD, 1 pip might be $0.01, so sl_pips can be large even for small price diffs.
                 # Let's adjust this check to be more nuanced or remove if SL can be very small.
                 # If point_value is 0.01 (XAUUSD), price_diff of 0.1 ($1) means 10 pips.
                 # If point_value is 0.00001 (EURUSD), price_diff of 0.0001 (1 pip) means 10 pips.
                 # The sl_pips calculation seems to be relative to the 'point_value'.
                 # A check like `sl_pips < 1` means less than 1 point, which is too small for most.
                 # Re-evaluating: sl_pips is distance in terms of 'points'.
                 # If point is 0.00001, sl_pips=1 means SL is 0.00001 away.
                 # This check `if sl_pips < 1` is probably too aggressive.
                 # Let's say min 0.1 pips for the purpose of lot calculation stability with mock.
                pass # Remove the overly strict check for now, rely on lot_step and min_lot

            if sl_pips <= 0: # Still need to ensure SL pips is positive
                logger.warning(f"MockRM PG: SL pips {sl_pips} is zero or negative for {symbol} E:{entry_price} SL:{sl_price}. Returning 0 lot.")
                return 0.0

            lots = risk_per_trade_usd / (sl_pips * pip_value_per_lot)
            min_lot = symbol_details.get('min_lot_size', 0.01)
            lot_step = symbol_details.get('lot_step', 0.01)
            lots = max(min_lot, round(lots / lot_step) * lot_step if lot_step > 0 else lots)
            logger.debug(f"MockRM PG: Lots for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} -> {lots} lots (SL pips: {sl_pips:.2f})")
            return lots if lots > 0 else 0.0

    class MainConfigPyramidGrid:
        DEFAULT_RISK_PER_TRADE_USD = 50.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_pyramid_grid.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5},
            "USDJPY": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 0.9, "decimals": 3} # Assuming 1 pip = 0.01 JPY value for 1 lot
        }

    import sys
    sys.modules['grid_trader.config'] = MainConfigPyramidGrid
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigPyramidGrid

    logger = get_logger(__name__)

    mock_rm_pg = MockRiskManagerPG()
    hist_data_pg = pd.DataFrame({'Close': [1.10000], 'High': [1.10200], 'Low': [1.09900]})

    logger.info("--- Testing PyramidGridModel EURUSD (BUY) ---")
    base_params_pg_buy = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10150,
        'base_sl': 1.09800, 'base_tp': 1.11000,
        'base_size_lots': 0.1, 'atr': 0.00050
    }

    logger.info("Test 1: SL at previous level")
    pyramid_grid_buy_prev_sl = PyramidGridModel(
        base_params_pg_buy, hist_data_pg, mock_rm_pg,
        num_pyramid_levels=3, atr_multiplier_spacing=1.0,
        sl_at_previous_level=True, tp_atr_multiplier=3.0
    )
    buy_pg_orders_1 = pyramid_grid_buy_prev_sl.generate_grid_orders()
    for order in buy_pg_orders_1: logger.info(f"  {order}")

    logger.info("\nTest 2: SL by ATR multiplier")
    pyramid_grid_buy_atr_sl = PyramidGridModel(
        base_params_pg_buy, hist_data_pg, mock_rm_pg,
        num_pyramid_levels=3, atr_multiplier_spacing=1.0,
        sl_at_previous_level=False, sl_atr_multiplier=1.5,
        tp_atr_multiplier=3.0
    )
    buy_pg_orders_2 = pyramid_grid_buy_atr_sl.generate_grid_orders()
    for order in buy_pg_orders_2: logger.info(f"  {order}")

    logger.info("--- Testing PyramidGridModel USDJPY (SELL) ---")
    base_params_pg_sell_jpy = {
        'symbol': 'USDJPY', 'direction': 'sell', 'base_price': 130.500,
        'base_sl': 131.000, 'base_tp': 129.000,
        'base_size_lots': 0.1, 'atr': 0.150
    }

    logger.info("Test 3: SL at previous level (SELL)")
    pyramid_grid_sell_prev_sl = PyramidGridModel(
        base_params_pg_sell_jpy, hist_data_pg, mock_rm_pg,
        num_pyramid_levels=4, atr_multiplier_spacing=0.8,
        sl_at_previous_level=True, tp_atr_multiplier=2.5
    )
    sell_pg_orders_1 = pyramid_grid_sell_prev_sl.generate_grid_orders()
    for order in sell_pg_orders_1: logger.info(f"  {order}")

    logger.info("\nTest 4: Small ATR leading to small spacing / SL distance")
    base_params_pg_buy_small_atr = base_params_pg_buy.copy()
    base_params_pg_buy_small_atr['atr'] = 0.00002 # 0.2 pips for EURUSD
    pyramid_grid_small_atr = PyramidGridModel(
        base_params_pg_buy_small_atr, hist_data_pg, mock_rm_pg,
        num_pyramid_levels=2, atr_multiplier_spacing=1.0, # spacing = 0.00002
        sl_at_previous_level=True, tp_atr_multiplier=3.0  # SL = 0.00002 away
    )
    small_atr_orders = pyramid_grid_small_atr.generate_grid_orders()
    for order in small_atr_orders: logger.info(f"  {order}")

    logger.info("\nTest 5: Zero ATR")
    base_params_zero_atr = base_params_pg_buy.copy()
    base_params_zero_atr['atr'] = 0.0
    try:
        pyramid_zero_atr = PyramidGridModel(base_params_zero_atr, hist_data_pg, mock_rm_pg)
    except ValueError as e: # BaseGridModel should raise this for ATR=0
        logger.info(f"Caught expected error for Zero ATR during init: {e}")
    # If it didn't raise in init (it should), test generate_grid_orders
    # This part would only run if BaseGridModel's ATR check was removed/failed
    else:
        orders_zero_atr = pyramid_zero_atr.generate_grid_orders()
        logger.info(f"Orders for Zero ATR (if init passed): {len(orders_zero_atr)}")
