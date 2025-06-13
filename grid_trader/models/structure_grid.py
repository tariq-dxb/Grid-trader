# grid_trader/models/structure_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from ..utils.price_structure import find_swing_highs, find_swing_lows # Assuming this utility is available
from .. import config

logger = get_logger(__name__)

class StructureGridModel(BaseGridModel):
    """
    Structure-Based Grid Model: Aligns orders to recent swing highs and lows.
    - For a 'buy' direction: Buy Stops above swing highs, Buy Limits at swing lows.
    - For a 'sell' direction: Sell Stops below swing lows, Sell Limits at swing highs.
    Fibonacci levels are a future consideration.
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_swing_levels_to_consider: int = 3, # Consider N most recent swing H/L
                 entry_buffer_atr_multiplier: float = 0.1, # Buffer from swing level for entry (e.g. 0.1 * ATR)
                 sl_atr_multiplier: float = 1.0,
                 tp_atr_multiplier: float = 1.5,
                 swing_n_bars: int = 5): # Parameter for find_swing_highs/lows if not pre-calculated
        """
        Args:
            num_swing_levels_to_consider (int): How many recent swing points to use.
            entry_buffer_atr_multiplier (float): Multiplier of ATR to set entry slightly off the swing level.
            sl_atr_multiplier (float): ATR multiplier for SL from entry.
            tp_atr_multiplier (float): ATR multiplier for TP from entry.
            swing_n_bars (int): N-bars parameter for swing detection if not pre-calculated.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_swing_levels = int(max(1, num_swing_levels_to_consider))
        # Calculate actual buffer value using current_atr
        self.entry_buffer_price = float(max(0, entry_buffer_atr_multiplier)) * self.current_atr
        self.sl_atr_multiplier = float(max(0.1, sl_atr_multiplier))
        self.tp_atr_multiplier = float(max(0.1, tp_atr_multiplier))
        self.swing_n_bars = swing_n_bars

        # Check for existing swing columns or generate them
        # Standard names first
        sh_col_name = 'SwingHigh'
        sl_col_name = 'SwingLow'
        # Fallback to n-bar specific names
        sh_col_n_bar_name = f'SwingHigh_N{self.swing_n_bars}'
        sl_col_n_bar_name = f'SwingLow_N{self.swing_n_bars}'

        if sh_col_name not in self.historical_data.columns and sh_col_n_bar_name not in self.historical_data.columns:
            logger.info(f"StructureGrid: '{sh_col_name}' or '{sh_col_n_bar_name}' not in historical_data. Calculating with n_bars={self.swing_n_bars}.")
            self.historical_data[sh_col_n_bar_name] = find_swing_highs(self.historical_data, n_bars=self.swing_n_bars)
            self.swing_high_col = sh_col_n_bar_name
        elif sh_col_name in self.historical_data.columns:
            self.swing_high_col = sh_col_name
        else: # sh_col_n_bar_name must exist
            self.swing_high_col = sh_col_n_bar_name

        if sl_col_name not in self.historical_data.columns and sl_col_n_bar_name not in self.historical_data.columns:
            logger.info(f"StructureGrid: '{sl_col_name}' or '{sl_col_n_bar_name}' not in historical_data. Calculating with n_bars={self.swing_n_bars}.")
            self.historical_data[sl_col_n_bar_name] = find_swing_lows(self.historical_data, n_bars=self.swing_n_bars)
            self.swing_low_col = sl_col_n_bar_name
        elif sl_col_name in self.historical_data.columns:
            self.swing_low_col = sl_col_name
        else: # sl_col_n_bar_name must exist
            self.swing_low_col = sl_col_n_bar_name

        logger.info(f"StructureGridModel initialized for {self.symbol} {self.direction}: "
                    f"SwingLevels={self.num_swing_levels}, EntryBufferATRMultiplier={entry_buffer_atr_multiplier} (BufferPrice={self.entry_buffer_price:.5f}), "
                    f"SL_ATR_x{self.sl_atr_multiplier}, TP_ATR_x{self.tp_atr_multiplier}, SwingNBarsUsed={self.swing_n_bars}, "
                    f"SHcol='{self.swing_high_col}', SLcol='{self.swing_low_col}'")

    def _get_decimals(self) -> int:
        decimals = 4
        try:
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
            logger.warning(f"Could not determine decimals for {self.symbol} (StructureGrid), defaulting to {decimals}. Error: {e}", exc_info=True)
        return decimals

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        # Check ATR if it's needed for buffer, SL, or TP
        if self.current_atr <= 0 and (self.entry_buffer_price > 0 or self.sl_atr_multiplier > 0 or self.tp_atr_multiplier > 0) :
            logger.warning(f"StructureGrid: ATR is not positive ({self.current_atr}) but used for buffer/SL/TP. No orders generated.")
            return []

        if self.historical_data.empty:
            logger.warning("StructureGrid: Historical data is empty. Cannot find swing levels.")
            return []

        if self.swing_high_col not in self.historical_data.columns or self.swing_low_col not in self.historical_data.columns:
            logger.error(f"StructureGrid: Critical - Swing columns '{self.swing_high_col}' or '{self.swing_low_col}' missing. Cannot generate orders.")
            return []

        decimals = self._get_decimals()
        sl_dist = self.current_atr * self.sl_atr_multiplier
        tp_dist = self.current_atr * self.tp_atr_multiplier

        recent_data = self.historical_data.sort_index(ascending=True)

        swing_high_prices = recent_data[recent_data[self.swing_high_col]]['High'].tail(self.num_swing_levels).tolist()
        swing_low_prices = recent_data[recent_data[self.swing_low_col]]['Low'].tail(self.num_swing_levels).tolist()

        logger.debug(f"Recent swing highs for {self.symbol} (latest {self.num_swing_levels}): {swing_high_prices}")
        logger.debug(f"Recent swing lows for {self.symbol} (latest {self.num_swing_levels}): {swing_low_prices}")

        processed_levels = set()

        if self.direction.lower() == 'buy':
            # Buy Stops above swing highs
            for sh_price in sorted(list(set(swing_high_prices))):
                if sh_price <= self.base_price: continue
                entry_price = round(sh_price + self.entry_buffer_price, decimals)
                if entry_price in processed_levels: continue
                order_sl = round(entry_price - sl_dist, decimals)
                order_tp = round(entry_price + tp_dist, decimals)
                if entry_price <= order_sl or entry_price >= order_tp : continue
                lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)
                if lot_size > 0:
                    self.grid_orders.append({'symbol': self.symbol, 'order_type': 'BUY_STOP','entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,'lot_size': lot_size, 'grid_id': f"STG_{self.symbol}_BS_SH@{sh_price:.{decimals}f}"})
                    processed_levels.add(entry_price)

            # Buy Limits at swing lows
            for sl_price in sorted(list(set(swing_low_prices)), reverse=True):
                if sl_price >= self.base_price: continue
                entry_price = round(sl_price - self.entry_buffer_price, decimals)
                if entry_price in processed_levels: continue
                order_sl = round(entry_price - sl_dist, decimals)
                order_tp = round(entry_price + tp_dist, decimals)
                if entry_price <= order_sl or entry_price >= order_tp: continue
                lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)
                if lot_size > 0:
                    self.grid_orders.append({'symbol': self.symbol, 'order_type': 'BUY_LIMIT','entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,'lot_size': lot_size, 'grid_id': f"STG_{self.symbol}_BL_SL@{sl_price:.{decimals}f}"})
                    processed_levels.add(entry_price)

        elif self.direction.lower() == 'sell':
            # Sell Stops below swing lows
            for sl_price in sorted(list(set(swing_low_prices)), reverse=True):
                if sl_price >= self.base_price: continue
                entry_price = round(sl_price - self.entry_buffer_price, decimals)
                if entry_price in processed_levels: continue
                order_sl = round(entry_price + sl_dist, decimals)
                order_tp = round(entry_price - tp_dist, decimals)
                if entry_price >= order_sl or entry_price <= order_tp: continue
                lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)
                if lot_size > 0:
                    self.grid_orders.append({'symbol': self.symbol, 'order_type': 'SELL_STOP','entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,'lot_size': lot_size, 'grid_id': f"STG_{self.symbol}_SS_SL@{sl_price:.{decimals}f}"})
                    processed_levels.add(entry_price)

            # Sell Limits at swing highs
            for sh_price in sorted(list(set(swing_high_prices))):
                if sh_price <= self.base_price: continue
                entry_price = round(sh_price + self.entry_buffer_price, decimals)
                if entry_price in processed_levels: continue
                order_sl = round(entry_price + sl_dist, decimals)
                order_tp = round(entry_price - tp_dist, decimals)
                if entry_price >= order_sl or entry_price <= order_tp: continue
                lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)
                if lot_size > 0:
                    self.grid_orders.append({'symbol': self.symbol, 'order_type': 'SELL_LIMIT','entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,'lot_size': lot_size, 'grid_id': f"STG_{self.symbol}_SLIM_SH@{sh_price:.{decimals}f}"})
                    processed_levels.add(entry_price)

        logger.info(f"Generated {len(self.grid_orders)} orders for StructureGridModel ({self.symbol}).")
        return self.grid_orders

if __name__ == '__main__':
    class MockRiskManagerSTG:
        def get_account_balance(self): return 25000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0
            cfg = sys.modules.get('grid_trader.config', config)
            symbol_details = cfg.SYMBOL_SETTINGS.get(symbol, {})
            decimals = symbol_details.get('decimals', 4)
            pip_value_per_lot = symbol_details.get('pip_value_per_lot', 10)
            point_value = 10**(-decimals)
            sl_pips = price_diff / point_value
            if sl_pips < 0.5 : return 0.0
            lots = risk_per_trade_usd / (sl_pips * pip_value_per_lot)
            min_lot = symbol_details.get('min_lot_size', 0.01)
            lot_step = symbol_details.get('lot_step', 0.01)
            lots = max(min_lot, round(lots / lot_step) * lot_step if lot_step > 0 else lots)
            logger.debug(f"MockRM STG: Lots for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} -> {lots} lots (SL pips: {sl_pips:.2f})")
            return lots if lots > 0 else 0.0

    class MainConfigStructureGrid:
        DEFAULT_RISK_PER_TRADE_USD = 15.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_structure_grid.log"
        SYMBOL_SETTINGS = {"EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5}}

    import sys
    sys.modules['grid_trader.config'] = MainConfigStructureGrid
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigStructureGrid
    logger = get_logger(__name__)

    mock_rm_stg = MockRiskManagerSTG()
    raw_data = {
        'Timestamp': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03', '2023-01-04', '2023-01-05',
                                     '2023-01-06', '2023-01-07', '2023-01-08', '2023-01-09', '2023-01-10',
                                     '2023-01-11', '2023-01-12', '2023-01-13', '2023-01-14', '2023-01-15']),
        'Open':  [1.100, 1.102, 1.101, 1.105, 1.103, 1.107, 1.109, 1.106, 1.108, 1.110, 1.112, 1.109, 1.111, 1.113, 1.110],
        'High':  [1.103, 1.106, 1.104, 1.108, 1.105, 1.110, 1.112, 1.109, 1.111, 1.114, 1.115, 1.112, 1.116, 1.115, 1.113],
        'Low':   [1.098, 1.100, 1.099, 1.102, 1.101, 1.105, 1.107, 1.104, 1.106, 1.108, 1.109, 1.107, 1.110, 1.111, 1.108],
        'Close': [1.102, 1.101, 1.103, 1.104, 1.102, 1.108, 1.107, 1.108, 1.110, 1.112, 1.109, 1.111, 1.113, 1.110, 1.111]
    }
    hist_data_stg = pd.DataFrame(raw_data).set_index('Timestamp')

    logger.info("--- Testing StructureGridModel EURUSD (BUY direction) ---")
    base_params_stg_buy = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.11050,
        'base_sl': 1.10000, 'base_tp': 1.12000,
        'base_size_lots': 0.1, 'atr': 0.00100
    }
    structure_grid_buy = StructureGridModel(
        base_params_stg_buy, hist_data_stg.copy(), mock_rm_stg,
        num_swing_levels_to_consider=3, swing_n_bars=2,
        entry_buffer_atr_multiplier=0.05,
        sl_atr_multiplier=1.0, tp_atr_multiplier=1.5
    )
    buy_stg_orders = structure_grid_buy.generate_grid_orders()
    for order in buy_stg_orders: logger.info(f"  {order}")

    logger.info("--- Testing StructureGridModel EURUSD (SELL direction) ---")
    base_params_stg_sell = {
        'symbol': 'EURUSD', 'direction': 'sell', 'base_price': 1.10500,
        'base_sl': 1.11500, 'base_tp': 1.09500,
        'base_size_lots': 0.1, 'atr': 0.00080
    }
    structure_grid_sell = StructureGridModel(
        base_params_stg_sell, hist_data_stg.copy(), mock_rm_stg,
        num_swing_levels_to_consider=4, swing_n_bars=3,
        entry_buffer_atr_multiplier=0.1,
        sl_atr_multiplier=1.2, tp_atr_multiplier=2.0
    )
    sell_stg_orders = structure_grid_sell.generate_grid_orders()
    for order in sell_stg_orders: logger.info(f"  {order}")

    logger.info("--- Testing StructureGridModel with no ATR (buffers/sl/tp rely on it) ---")
    base_params_no_atr = base_params_stg_buy.copy()
    base_params_no_atr['atr'] = 0.0
    structure_grid_no_atr = StructureGridModel(base_params_no_atr, hist_data_stg.copy(), mock_rm_stg)
    no_atr_orders = structure_grid_no_atr.generate_grid_orders()
    logger.info(f"Orders with no ATR: {len(no_atr_orders)}")
