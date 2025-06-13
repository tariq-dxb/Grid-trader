# grid_trader/models/dual_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from .. import config

logger = get_logger(__name__)

class DualGridModel(BaseGridModel):
    """
    Dual-Sided Grid Model: Generates both breakout and reversal pending orders.
    - Breakout: Buy Stops above base_price, Sell Stops below base_price.
    - Reversal: Sell Limits above base_price, Buy Limits below base_price.
    Spacing is based on ATR.
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_breakout_levels: int = 2, num_reversal_levels: int = 2,
                 atr_multiplier_breakout: float = 1.0, atr_multiplier_reversal: float = 0.75,
                 stop_loss_atr_multiplier: float = 1.0, take_profit_atr_multiplier: float = 1.5):
        """
        Args:
            num_breakout_levels (int): Number of breakout grid levels on each side.
            num_reversal_levels (int): Number of reversal grid levels on each side.
            atr_multiplier_breakout (float): ATR multiplier for spacing breakout orders.
            atr_multiplier_reversal (float): ATR multiplier for spacing reversal orders.
            stop_loss_atr_multiplier (float): ATR multiplier for SL distance from entry.
            take_profit_atr_multiplier (float): ATR multiplier for TP distance from entry.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_breakout_levels = int(max(0, num_breakout_levels)) # Can be 0 if no breakout orders desired
        self.num_reversal_levels = int(max(0, num_reversal_levels)) # Can be 0 if no reversal orders desired
        self.atr_multiplier_breakout = float(max(0.1, atr_multiplier_breakout))
        self.atr_multiplier_reversal = float(max(0.1, atr_multiplier_reversal))
        self.sl_atr_multiplier = float(max(0.1, stop_loss_atr_multiplier))
        self.tp_atr_multiplier = float(max(0.1, take_profit_atr_multiplier))

        logger.info(f"DualGridModel initialized for {self.symbol}: Breakout Levels={self.num_breakout_levels} (ATR x{self.atr_multiplier_breakout}), "
                    f"Reversal Levels={self.num_reversal_levels} (ATR x{self.atr_multiplier_reversal}), "
                    f"SL ATR x{self.sl_atr_multiplier}, TP ATR x{self.tp_atr_multiplier}")

    def _get_decimals(self) -> int:
        """ Helper to determine decimal places for price rounding. """
        decimals = 4 # Default
        try:
            # Access config through the imported 'config' module object
            symbol_config = config.SYMBOL_SETTINGS.get(self.symbol, {})
            if 'decimals' in symbol_config:
                decimals = int(symbol_config['decimals'])
            else:
                base_price_str = str(self.base_price)
                if '.' in base_price_str: decimals = len(base_price_str.split('.')[-1])
                elif "JPY" in self.symbol.upper(): decimals = 2

                if "XAU" in self.symbol.upper() or "XAG" in self.symbol.upper(): decimals = min(decimals, 2)
                elif "JPY" in self.symbol.upper(): decimals = min(decimals, 3)
                else: decimals = min(decimals, 5)
        except Exception as e:
            logger.warning(f"Could not determine decimals for {self.symbol} accurately, defaulting to {decimals}. Error: {e}", exc_info=True)
        return decimals

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        if self.current_atr <= 0:
            logger.warning(f"ATR is not positive ({self.current_atr}) for {self.symbol}. Cannot generate Dual Grid.")
            return []

        decimals = self._get_decimals()

        sl_distance = self.current_atr * self.sl_atr_multiplier
        tp_distance = self.current_atr * self.tp_atr_multiplier

        if sl_distance <= 0 or tp_distance <= 0:
            logger.warning(f"SL distance ({sl_distance}) or TP distance ({tp_distance}) is not positive. Cannot generate orders.")
            return []

        # --- Generate Breakout Orders ---
        if self.num_breakout_levels > 0:
            spacing_breakout = self.current_atr * self.atr_multiplier_breakout
            if spacing_breakout <= 0:
                logger.warning(f"Breakout spacing is not positive ({spacing_breakout}). Skipping breakout orders.")
            else:
                for i in range(1, self.num_breakout_levels + 1):
                    # Buy Stop (above base_price)
                    entry_bs = round(self.base_price + (i * spacing_breakout), decimals)
                    sl_bs = round(entry_bs - sl_distance, decimals)
                    tp_bs = round(entry_bs + tp_distance, decimals)
                    lot_bs = self._calculate_lot_size(entry_price=entry_bs, sl_price=sl_bs)
                    if lot_bs > 0 and entry_bs > sl_bs and entry_bs < tp_bs : # Basic validation
                        self.grid_orders.append({
                            'symbol': self.symbol, 'order_type': 'BUY_STOP',
                            'entry_price': entry_bs, 'sl': sl_bs, 'tp': tp_bs,
                            'lot_size': lot_bs, 'grid_id': f"DG_{self.symbol}_BS_{i}"
                        })

                    # Sell Stop (below base_price)
                    entry_ss = round(self.base_price - (i * spacing_breakout), decimals)
                    sl_ss = round(entry_ss + sl_distance, decimals)
                    tp_ss = round(entry_ss - tp_distance, decimals)
                    lot_ss = self._calculate_lot_size(entry_price=entry_ss, sl_price=sl_ss)
                    if lot_ss > 0 and entry_ss < sl_ss and entry_ss > tp_ss: # Basic validation
                        self.grid_orders.append({
                            'symbol': self.symbol, 'order_type': 'SELL_STOP',
                            'entry_price': entry_ss, 'sl': sl_ss, 'tp': tp_ss,
                            'lot_size': lot_ss, 'grid_id': f"DG_{self.symbol}_SS_{i}"
                        })

        # --- Generate Reversal Orders ---
        if self.num_reversal_levels > 0:
            spacing_reversal = self.current_atr * self.atr_multiplier_reversal
            if spacing_reversal <= 0:
                logger.warning(f"Reversal spacing is not positive ({spacing_reversal}). Skipping reversal orders.")
            else:
                for i in range(1, self.num_reversal_levels + 1):
                    # Sell Limit (above base_price, expecting price to reverse downwards)
                    entry_sl = round(self.base_price + (i * spacing_reversal), decimals)
                    sl_sl = round(entry_sl + sl_distance, decimals) # SL for Sell Limit is above entry
                    tp_sl = round(entry_sl - tp_distance, decimals) # TP for Sell Limit is below entry
                    lot_sl = self._calculate_lot_size(entry_price=entry_sl, sl_price=sl_sl)
                    if lot_sl > 0 and entry_sl < sl_sl and entry_sl > tp_sl: # Basic validation
                        self.grid_orders.append({
                            'symbol': self.symbol, 'order_type': 'SELL_LIMIT',
                            'entry_price': entry_sl, 'sl': sl_sl, 'tp': tp_sl,
                            'lot_size': lot_sl, 'grid_id': f"DG_{self.symbol}_SLIM_{i}"
                        })

                    # Buy Limit (below base_price, expecting price to reverse upwards)
                    entry_bl = round(self.base_price - (i * spacing_reversal), decimals)
                    sl_bl = round(entry_bl - sl_distance, decimals) # SL for Buy Limit is below entry
                    tp_bl = round(entry_bl + tp_distance, decimals) # TP for Buy Limit is above entry
                    lot_bl = self._calculate_lot_size(entry_price=entry_bl, sl_price=sl_bl)
                    if lot_bl > 0 and entry_bl > sl_bl and entry_bl < tp_bl: # Basic validation
                        self.grid_orders.append({
                            'symbol': self.symbol, 'order_type': 'BUY_LIMIT',
                            'entry_price': entry_bl, 'sl': sl_bl, 'tp': tp_bl,
                            'lot_size': lot_bl, 'grid_id': f"DG_{self.symbol}_BLIM_{i}"
                        })

        logger.info(f"Generated {len(self.grid_orders)} orders for DualGridModel ({self.symbol}).")
        if self.grid_orders:
             unique_orders = []
             seen_entries = set()
             for order in self.grid_orders:
                 order_key = (order['order_type'], order['entry_price'])
                 if order_key not in seen_entries:
                     unique_orders.append(order)
                     seen_entries.add(order_key)
             if len(unique_orders) != len(self.grid_orders):
                 logger.warning(f"DualGrid: Removed duplicate orders. Original: {len(self.grid_orders)}, Unique: {len(unique_orders)}")
                 self.grid_orders = unique_orders
        return self.grid_orders

if __name__ == '__main__':
    class MockRiskManagerDG:
        def get_account_balance(self): return 20000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0

            cfg = sys.modules.get('grid_trader.config', config) # Use potentially mocked config

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
            logger.debug(f"MockRM DG: Lots for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} -> {lots} lots (SL pips: {sl_pips})")
            return lots if lots > 0 else 0.0

    class MainConfigDualGrid:
        DEFAULT_RISK_PER_TRADE_USD = 10.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_dual_grid.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5},
            "XAUUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 1.0, "decimals": 2}
        }

    import sys
    sys.modules['grid_trader.config'] = MainConfigDualGrid
    import grid_trader.utils.logger as util_logger # Import the logger module
    util_logger.config = MainConfigDualGrid # Patch its 'config' attribute

    logger = get_logger(__name__) # Re-initialize logger for this __main__ scope

    mock_rm_dg = MockRiskManagerDG()
    hist_data_dg = pd.DataFrame({'Close': [1.10000], 'High': [1.10200], 'Low': [1.09900]})

    logger.info("--- Testing DualGridModel EURUSD (BUY direction context) ---")
    base_params_dg_eurusd = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10150,
        'base_sl': 1.09000, 'base_tp': 1.12000, 'base_size_lots': 0.1, 'atr': 0.00100
    }
    dual_grid_eurusd = DualGridModel(
        base_params_dg_eurusd, hist_data_dg, mock_rm_dg,
        num_breakout_levels=2, num_reversal_levels=2,
        atr_multiplier_breakout=1.0, atr_multiplier_reversal=0.5,
        stop_loss_atr_multiplier=0.75, take_profit_atr_multiplier=1.5
    )
    eurusd_dg_orders = dual_grid_eurusd.generate_grid_orders()
    for order in eurusd_dg_orders: logger.info(f"  {order}")

    logger.info("--- Testing DualGridModel XAUUSD (SELL direction context, only reversal) ---")
    base_params_dg_xauusd = {
        'symbol': 'XAUUSD', 'direction': 'sell', 'base_price': 1950.00,
        'base_sl': 1900.00, 'base_tp': 2000.00, 'base_size_lots': 0.1, 'atr': 10.00
    }
    dual_grid_xauusd_rev = DualGridModel(
        base_params_dg_xauusd, hist_data_dg, mock_rm_dg,
        num_breakout_levels=0, num_reversal_levels=3,
        atr_multiplier_reversal=0.6,
        stop_loss_atr_multiplier=0.8, take_profit_atr_multiplier=2.0
    )
    xauusd_dg_orders_rev = dual_grid_xauusd_rev.generate_grid_orders()
    for order in xauusd_dg_orders_rev: logger.info(f"  {order}")

    logger.info("--- Testing DualGridModel EURUSD (only breakout) ---")
    dual_grid_eurusd_bo = DualGridModel(
        base_params_dg_eurusd, hist_data_dg, mock_rm_dg,
        num_breakout_levels=3, num_reversal_levels=0,
        atr_multiplier_breakout=0.8,
        stop_loss_atr_multiplier=1.0, take_profit_atr_multiplier=1.0
    )
    eurusd_dg_orders_bo = dual_grid_eurusd_bo.generate_grid_orders()
    for order in eurusd_dg_orders_bo: logger.info(f"  {order}")
