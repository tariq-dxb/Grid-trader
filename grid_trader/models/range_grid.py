# grid_trader/models/range_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from ..utils.indicators import calculate_bollinger_bands # If needed and not pre-calculated
from .. import config

logger = get_logger(__name__)

class RangeGridModel(BaseGridModel):
    """
    Range-Reactive Grid Model: Places a tight, bi-directional grid of LIMIT orders
    within a perceived consolidation zone, typically defined by Bollinger Bands.
    - Buy Limits near the lower band (or range bottom).
    - Sell Limits near the upper band (or range top).
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_grid_lines_per_side: int = 3, # Number of buy limits and sell limits
                 range_definition_method: str = 'bollinger', # 'bollinger' or 'recent_high_low'
                 bb_period: int = 20, bb_std_dev: int = 2, # For Bollinger Bands
                 recent_hl_period: int = 20, # For recent_high_low method
                 spacing_as_fraction_of_range: float = 0.2, # e.g., 0.2 means 5 lines fill the range per side
                 sl_buffer_atr_multiplier: float = 0.5, # SL outside range by this ATR multiple
                 tp_target_other_side_of_range: bool = True,
                 tp_atr_multiplier: float = 1.5 # If not targeting other side
                 ):
        """
        Args:
            num_grid_lines_per_side (int): Number of buy limit and sell limit orders.
            range_definition_method (str): 'bollinger' or 'recent_high_low'.
            bb_period (int), bb_std_dev (int): Params if using Bollinger Bands.
            recent_hl_period (int): Lookback period if using recent high/low.
            spacing_as_fraction_of_range (float): Determines spacing relative to range width.
            sl_buffer_atr_multiplier (float): SL placed ATR*multiplier outside the identified range.
            tp_target_other_side_of_range (bool): If True, TP aims for opposite side of range.
            tp_atr_multiplier (float): Used for TP if not targeting other side.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_grid_lines = int(max(1, num_grid_lines_per_side))
        self.range_method = range_definition_method.lower()
        self.bb_period = bb_period
        self.bb_std_dev = bb_std_dev
        self.recent_hl_period = recent_hl_period
        self.spacing_fraction = float(max(0.05, min(0.5, spacing_as_fraction_of_range))) # Ensure 0.05 to 0.5
        self.sl_buffer_atr = self.current_atr * float(max(0.1, sl_buffer_atr_multiplier))
        self.tp_target_other_side = tp_target_other_side_of_range
        self.tp_atr_multiplier = float(max(0.1, tp_atr_multiplier))

        self.range_low = None
        self.range_high = None

        logger.info(f"RangeGridModel initialized for {self.symbol}: Method='{self.range_method}', LinesPerSide={self.num_grid_lines}, "
                    f"SpacingFraction={self.spacing_fraction}, SL_ATR_Buffer={sl_buffer_atr_multiplier}x, TPtargetOtherSide={self.tp_target_other_side}")

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
            logger.warning(f"Could not determine decimals for {self.symbol} (RangeGrid), defaulting to {decimals}. Error: {e}", exc_info=True)
        return decimals

    def _define_range(self) -> bool:
        if self.historical_data.empty:
            logger.warning("RangeGrid: Historical data is empty. Cannot define range.")
            return False

        # Ensure data is sorted by time for .iloc[-1] to be latest
        # BaseGridModel doesn't enforce this, so good to do it here if not done by caller.
        # self.historical_data = self.historical_data.sort_index(ascending=True) # Might be too slow if large df

        latest_data_point = self.historical_data.iloc[-1]

        if self.range_method == 'bollinger':
            bb_upper_col = f'BB_Upper_{self.bb_period}_{self.bb_std_dev}'
            bb_lower_col = f'BB_Lower_{self.bb_period}_{self.bb_std_dev}'

            # Check if columns exist and latest values are valid
            if bb_upper_col in self.historical_data.columns and bb_lower_col in self.historical_data.columns and \
               not pd.isna(latest_data_point.get(bb_upper_col)) and not pd.isna(latest_data_point.get(bb_lower_col)):
                self.range_high = latest_data_point[bb_upper_col]
                self.range_low = latest_data_point[bb_lower_col]
            else: # Calculate if missing or latest is NaN
                logger.info(f"RangeGrid: Bollinger Bands columns not found or latest values are NaN. Calculating BB({self.bb_period}, {self.bb_std_dev}).")
                # Pass a copy to avoid modifying the original DataFrame in indicators if it does that
                bb_df = calculate_bollinger_bands(self.historical_data.copy(), period=self.bb_period, std_dev=self.bb_std_dev)
                if not bb_df.empty and not pd.isna(bb_df[bb_upper_col].iloc[-1]) and not pd.isna(bb_df[bb_lower_col].iloc[-1]):
                    self.range_high = bb_df[bb_upper_col].iloc[-1]
                    self.range_low = bb_df[bb_lower_col].iloc[-1]
                    # Optionally, update self.historical_data with these calculated BBs if they might be reused
                    # self.historical_data[bb_upper_col] = bb_df[bb_upper_col]
                    # self.historical_data[bb_lower_col] = bb_df[bb_lower_col]
                else:
                    logger.warning("RangeGrid: Failed to calculate valid Bollinger Bands.")
                    return False

        elif self.range_method == 'recent_high_low':
            if len(self.historical_data) < self.recent_hl_period:
                logger.warning(f"RangeGrid: Not enough data ({len(self.historical_data)}) for recent_high_low period ({self.recent_hl_period}).")
                return False
            relevant_data = self.historical_data.tail(self.recent_hl_period)
            self.range_high = relevant_data['High'].max()
            self.range_low = relevant_data['Low'].min()
        else:
            logger.error(f"RangeGrid: Unknown range_definition_method: {self.range_method}")
            return False

        if self.range_high is None or self.range_low is None or pd.isna(self.range_high) or pd.isna(self.range_low):
            logger.warning(f"RangeGrid: Failed to define valid range. High: {self.range_high}, Low: {self.range_low}")
            return False

        if self.range_low >= self.range_high:
            logger.warning(f"RangeGrid: Initial range invalid (Low {self.range_low} >= High {self.range_high}).")
            min_atr_range = self.current_atr * 0.5
            if self.current_atr > 0 and (self.range_high - self.range_low) < min_atr_range : # Check if it's just too narrow
                 logger.warning(f"RangeGrid: Range width too small or inverted. Expanding slightly around midpoint using 0.5 * ATR ({self.current_atr}).")
                 mid_point = (self.range_high + self.range_low) / 2.0 # Ensure float division
                 self.range_high = mid_point + min_atr_range / 2.0
                 self.range_low = mid_point - min_atr_range / 2.0
                 if self.range_low >= self.range_high:
                      logger.error(f"RangeGrid: Still invalid range after ATR adjustment. Low {self.range_low}, High {self.range_high}")
                      return False
            else:
                 logger.error(f"RangeGrid: Invalid range (Low {self.range_low} >= High {self.range_high}) and cannot adjust (ATR={self.current_atr}).")
                 return False

        logger.info(f"RangeGrid: Defined range for {self.symbol}: Low={self.range_low:.{self._get_decimals()}f}, High={self.range_high:.{self._get_decimals()}f}")
        return True

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        if not self._define_range(): # Defines self.range_low and self.range_high
            return []

        decimals = self._get_decimals()
        range_width = self.range_high - self.range_low

        min_practical_range = (1 / (10**decimals)) * 2 # e.g., 2 pips for 5-decimal, 2 points for 2-decimal
        if range_width < min_practical_range:
            logger.warning(f"RangeGrid: Range width {range_width:.{decimals+1}f} is too small for {self.symbol} (min: {min_practical_range:.{decimals}f}). No orders generated.")
            return []

        line_spacing = range_width * self.spacing_fraction
        if line_spacing < (1 / (10**decimals)): # Spacing less than 1 pip/point
            logger.warning(f"RangeGrid: Line spacing {line_spacing:.{decimals+1}f} is too small. Min 1 pip/point required. No orders generated.")
            return []


        for i in range(self.num_grid_lines):
            # Sell Limits from range_high downwards
            # Entry is band - i * spacing. If i=0, entry is range_high.
            sell_entry = round(self.range_high - (i * line_spacing), decimals)

            if i == 0 and sell_entry <= self.base_price: # First sell limit should be above current price ideally
                logger.debug(f"RangeGrid: First Sell Limit entry {sell_entry} is at or below base_price {self.base_price}. May fill immediately or be invalid.")
            if sell_entry <= self.range_low + line_spacing/2 : # Avoid orders too deep into the other side
                logger.debug(f"RangeGrid: Sell Limit entry {sell_entry} too close to range low {self.range_low}. Stopping sell limit generation.")
                break

            sell_sl = round(self.range_high + self.sl_buffer_atr, decimals)
            if self.tp_target_other_side:
                sell_tp = round(self.range_low, decimals)
            else:
                sell_tp = round(sell_entry - (self.current_atr * self.tp_atr_multiplier), decimals)

            if sell_entry >= sell_sl or sell_entry <= sell_tp:
                logger.warning(f"RangeGrid: Invalid SL/TP for Sell Limit at {sell_entry} (SL:{sell_sl}, TP:{sell_tp}). Skipping.")
                continue

            lot_sell = self._calculate_lot_size(entry_price=sell_entry, sl_price=sell_sl)
            if lot_sell > 0:
                self.grid_orders.append({'symbol': self.symbol, 'order_type': 'SELL_LIMIT','entry_price': sell_entry, 'sl': sell_sl, 'tp': sell_tp,'lot_size': lot_sell, 'grid_id': f"RG_{self.symbol}_SLIM_{i}"})

        for i in range(self.num_grid_lines):
            # Buy Limits from range_low upwards
            buy_entry = round(self.range_low + (i * line_spacing), decimals)

            if i == 0 and buy_entry >= self.base_price:
                 logger.debug(f"RangeGrid: First Buy Limit entry {buy_entry} is at or above base_price {self.base_price}. May fill immediately or be invalid.")
            if buy_entry >= self.range_high - line_spacing/2:
                 logger.debug(f"RangeGrid: Buy Limit entry {buy_entry} too close to range high {self.range_high}. Stopping buy limit generation.")
                 break

            buy_sl = round(self.range_low - self.sl_buffer_atr, decimals)
            if self.tp_target_other_side:
                buy_tp = round(self.range_high, decimals)
            else:
                buy_tp = round(buy_entry + (self.current_atr * self.tp_atr_multiplier), decimals)

            if buy_entry <= buy_sl or buy_entry >= buy_tp:
                logger.warning(f"RangeGrid: Invalid SL/TP for Buy Limit at {buy_entry} (SL:{buy_sl}, TP:{buy_tp}). Skipping.")
                continue

            lot_buy = self._calculate_lot_size(entry_price=buy_entry, sl_price=buy_sl)
            if lot_buy > 0:
                self.grid_orders.append({'symbol': self.symbol, 'order_type': 'BUY_LIMIT','entry_price': buy_entry, 'sl': buy_sl, 'tp': buy_tp,'lot_size': lot_buy, 'grid_id': f"RG_{self.symbol}_BLIM_{i}"})

        if self.grid_orders:
             unique_orders = []; seen_entries = set()
             for order in self.grid_orders:
                 order_key = (order['order_type'], order['entry_price'])
                 if order_key not in seen_entries: unique_orders.append(order); seen_entries.add(order_key)
             if len(unique_orders) != len(self.grid_orders):
                 logger.warning(f"RangeGrid: Removed duplicate orders. Original: {len(self.grid_orders)}, Unique: {len(unique_orders)}")
                 self.grid_orders = unique_orders

        logger.info(f"Generated {len(self.grid_orders)} orders for RangeGridModel ({self.symbol}).")
        return self.grid_orders

if __name__ == '__main__':
    class MockRiskManagerRG: # Specific to RangeGrid tests
        def get_account_balance(self): return 10000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0
            cfg = sys.modules.get('grid_trader.config', config) # Use potentially mocked config
            symbol_details = cfg.SYMBOL_SETTINGS.get(symbol, {})
            decimals = symbol_details.get('decimals', 4); pip_value_per_lot = symbol_details.get('pip_value_per_lot', 10)
            point_value = 10**(-decimals); sl_pips = price_diff / point_value
            if sl_pips < 0.5: # Min 0.5 pips/points SL for lot calculation
                logger.debug(f"MockRM RG: SL pips {sl_pips:.2f} too small. Returning 0 lots.")
                return 0.0
            lots = risk_per_trade_usd / (sl_pips * pip_value_per_lot)
            min_lot = symbol_details.get('min_lot_size', 0.01); lot_step = symbol_details.get('lot_step', 0.01)
            lots = max(min_lot, round(lots / lot_step) * lot_step if lot_step > 0 else lots)
            logger.debug(f"MockRM RG: Lots {lots:.2f} for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} Pips:{sl_pips:.2f}")
            return lots if lots > 0 else 0.0

    class MainConfigRangeGrid:
        DEFAULT_RISK_PER_TRADE_USD = 5.0
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_range_grid.log"
        SYMBOL_SETTINGS = {"EURUSD": {"min_lot_size":0.01, "lot_step":0.01, "pip_value_per_lot":10.0, "decimals":5}}

    import sys
    sys.modules['grid_trader.config'] = MainConfigRangeGrid
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigRangeGrid
    logger = get_logger(__name__)

    mock_rm_rg = MockRiskManagerRG()

    data = {'Timestamp': pd.to_datetime([f'2023-01-{d:02d} {h}:00:00' for d in range(1, 3) for h in range(24)]),} # 48 hours of data
    base_val = 1.10000
    data['Open'] = base_val + np.random.randn(len(data['Timestamp'])) * 0.001
    data['High'] = data['Open'] + np.abs(np.random.randn(len(data['Timestamp'])) * 0.0005)
    data['Low'] = data['Open'] - np.abs(np.random.randn(len(data['Timestamp'])) * 0.0005)
    data['Close'] = data['Open'] + (data['High'] - data['Low']) * (np.random.rand(len(data['Timestamp'])) - 0.5)
    hist_data_rg = pd.DataFrame(data).set_index('Timestamp')

    # Calculate BBs for the entire dataset to ensure they are available
    bb_data_rg = calculate_bollinger_bands(hist_data_rg.copy(), period=20, std_dev=2)
    hist_data_rg[f'BB_Upper_{20}_{2}'] = bb_data_rg[f'BB_Upper_{20}_{2}']
    hist_data_rg[f'BB_Lower_{20}_{2}'] = bb_data_rg[f'BB_Lower_{20}_{2}']

    _base_price_for_test = hist_data_rg['Close'].iloc[-1]
    _atr_for_test = 0.00080 # Matching the original 'atr' value
    _decimals_for_test = MainConfigRangeGrid.SYMBOL_SETTINGS.get('EURUSD', {}).get('decimals', 5)

    base_params_rg = {
        'symbol': 'EURUSD', 'direction': 'buy',
        'base_price': _base_price_for_test,
        'base_sl': round(_base_price_for_test - 3 * _atr_for_test, _decimals_for_test),
        'base_tp': round(_base_price_for_test + 3 * _atr_for_test, _decimals_for_test),
        'base_size_lots': 0.01, # Provide a nominal valid lot size
        'atr': _atr_for_test
    }

    logger.info("--- Testing RangeGridModel (Bollinger Bands) ---")
    range_grid_bb = RangeGridModel(
        base_params_rg, hist_data_rg.copy(), mock_rm_rg,
        num_grid_lines_per_side=3, range_definition_method='bollinger',
        bb_period=20, bb_std_dev=2, spacing_as_fraction_of_range=0.2,
        sl_buffer_atr_multiplier=0.5, tp_target_other_side_of_range=True
    )
    orders_bb = range_grid_bb.generate_grid_orders()
    for order in orders_bb: logger.info(f"  {order}")

    logger.info("--- Testing RangeGridModel (Recent High/Low) ---")
    hist_data_hl = hist_data_rg.copy() # Use the same data, method will pick recent part
    range_grid_hl = RangeGridModel(
        base_params_rg, hist_data_hl, mock_rm_rg,
        num_grid_lines_per_side=2, range_definition_method='recent_high_low',
        recent_hl_period=20, spacing_as_fraction_of_range=0.25,
        sl_buffer_atr_multiplier=0.3, tp_target_other_side_of_range=False, tp_atr_multiplier=2.0
    )
    orders_hl = range_grid_hl.generate_grid_orders()
    for order in orders_hl: logger.info(f"  {order}")

    logger.info("--- Testing RangeGridModel (Invalid Range - Low >= High initially by manual override) ---")
    hist_data_invalid = hist_data_rg.copy()
    # Ensure BB columns exist before trying to override them
    bb_upper_col_inv = f'BB_Upper_{20}_{2}'
    bb_lower_col_inv = f'BB_Lower_{20}_{2}'
    if bb_upper_col_inv not in hist_data_invalid.columns : hist_data_invalid[bb_upper_col_inv] = 0
    if bb_lower_col_inv not in hist_data_invalid.columns : hist_data_invalid[bb_lower_col_inv] = 0

    hist_data_invalid.at[hist_data_invalid.index[-1], bb_upper_col_inv] = 1.10000
    hist_data_invalid.at[hist_data_invalid.index[-1], bb_lower_col_inv] = 1.10100

    range_grid_invalid = RangeGridModel(base_params_rg, hist_data_invalid, mock_rm_rg, bb_period=20, bb_std_dev=2)
    orders_invalid = range_grid_invalid.generate_grid_orders()
    logger.info(f"Orders from invalid initial range: {len(orders_invalid)}")
