# grid_trader/engine/signal_router.py
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, Optional, List # Added List
from enum import Enum # Added Enum

from .. import config
from ..utils.logger import get_logger
from ..utils.indicators import calculate_atr, calculate_ema, calculate_adx, calculate_bollinger_bands
from ..utils.price_structure import find_swing_highs, find_swing_lows


logger = get_logger(__name__)

# Removed MarketCondition Enum as it's not directly used in the final logic.
# String descriptions will be used in reasoning.

class SignalRouter:
    """
    Evaluates market conditions using various filters (ATR, EMA, ADX, BB, Structure)
    to select an appropriate grid trading model.
    """
    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame):
        """
        Args:
            base_trade_params (Dict[str, Any]): Base parameters for the trade idea.
                                                Includes 'symbol', 'direction', 'base_price', 'atr'.
            historical_data (pd.DataFrame): DataFrame with price data and pre-calculated indicators.
        """
        self.base_trade_params = base_trade_params
        self.symbol = base_trade_params.get('symbol', 'UNKNOWN')
        self.base_price = base_trade_params.get('base_price', 0.0)
        self.current_atr = base_trade_params.get('atr', 0.0)

        self.historical_data = historical_data
        if self.historical_data.empty:
            logger.warning("SignalRouter initialized with empty historical_data.")

        # Configurable thresholds from config.py
        # Using getattr for config values to allow for easy mocking in tests if config object is replaced
        _config = config # Use the imported config module by default

        self.atr_median_periods = getattr(_config, 'ATR_MEDIAN_PERIODS', 50)
        self.atr_high_vol_factor = getattr(_config, 'ATR_HIGH_VOL_FACTOR', 1.5)
        self.atr_low_vol_factor = getattr(_config, 'ATR_LOW_VOL_FACTOR', 0.7)

        self.ema_short_period = getattr(_config, 'EMA_SHORT_PERIOD', 12)
        self.ema_long_period = getattr(_config, 'EMA_LONG_PERIOD', 26)
        self.adx_period = getattr(_config, 'ADX_PERIOD', 14)
        self.adx_trend_threshold = getattr(_config, 'ADX_TREND_THRESHOLD', 25)

        self.bb_period = getattr(_config, 'BOLLINGER_BANDS_PERIOD', 20)
        self.bb_std_dev = getattr(_config, 'BOLLINGER_BANDS_STD_DEV', 2)
        self.bb_range_width_threshold_percent = getattr(_config, 'BB_RANGE_WIDTH_THRESHOLD_PERCENT', 0.03)

        self.swing_proximity_atr_multiplier = getattr(_config, 'SWING_PROXIMITY_ATR_MULTIPLIER', 0.5)

        logger.info(f"SignalRouter initialized for {self.symbol} at base price {self.base_price} with current ATR {self.current_atr}")


    def _check_data_availability(self, required_cols: List[str]) -> bool:
        if self.historical_data.empty:
            logger.debug("Historical data is empty. Cannot evaluate filters.")
            return False
        missing_cols = [col for col in required_cols if col not in self.historical_data.columns]
        if missing_cols:
            logger.debug(f"SignalRouter: Missing required columns in historical_data: {missing_cols}.")
            return False
        latest_row = self.historical_data.iloc[-1]
        for col in required_cols:
            if pd.isna(latest_row[col]):
                logger.debug(f"SignalRouter: Latest data for required column '{col}' is NaN.")
                return False
        return True

    def evaluate_volatility(self) -> Optional[str]:
        atr_col = f'ATR_{config.DEFAULT_ATR_PERIOD}'
        if not self._check_data_availability([atr_col]): return None

        if len(self.historical_data) < self.atr_median_periods:
            logger.debug(f"Not enough data ({len(self.historical_data)}) for ATR median ({self.atr_median_periods}).")
            if self.current_atr > 0: return "NORMAL"
            return None

        median_atr = self.historical_data[atr_col].rolling(window=self.atr_median_periods, min_periods=max(1,self.atr_median_periods//2)).median().iloc[-1]

        if pd.isna(median_atr) or median_atr == 0:
            logger.debug("Median ATR is NaN or zero. Cannot evaluate volatility vs median.")
            return "NORMAL"

        if self.current_atr > median_atr * self.atr_high_vol_factor: return "HIGH"
        elif self.current_atr < median_atr * self.atr_low_vol_factor: return "LOW"
        return "NORMAL"

    def evaluate_trend(self) -> Tuple[Optional[str], Optional[float]]:
        ema_s_col = f'EMA_{self.ema_short_period}'
        ema_l_col = f'EMA_{self.ema_long_period}'
        adx_col = f'ADX_{self.adx_period}'
        plus_di_col = f'+DI_{self.adx_period}'
        minus_di_col = f'-DI_{self.adx_period}'

        required_cols = [ema_s_col, ema_l_col, adx_col, plus_di_col, minus_di_col, 'Close'] # Added Close for EMA slope check
        if not self._check_data_availability(required_cols): return None, None

        latest = self.historical_data.iloc[-1]
        ema_short = latest[ema_s_col]; ema_long = latest[ema_l_col]
        adx = latest[adx_col]; plus_di = latest[plus_di_col]; minus_di = latest[minus_di_col]

        trend_direction = "NONE"
        if adx > self.adx_trend_threshold:
            if ema_short > ema_long and plus_di > minus_di: trend_direction = "UP"
            elif ema_short < ema_long and minus_di > plus_di: trend_direction = "DOWN"

        if trend_direction == "NONE": # A simpler trend assessment
            ema_diff_threshold = 0.001 # e.g. 0.1% difference
            if ema_short > ema_long and (ema_short - ema_long) / ema_long > ema_diff_threshold :
                 if len(self.historical_data) > 1 and self.historical_data[ema_s_col].iloc[-1] > self.historical_data[ema_s_col].iloc[-2]:
                           trend_direction = "WEAK_UP"
                 elif ema_short > ema_long : trend_direction = "WEAK_UP" # Fallback if no prev bar
            elif ema_short < ema_long and (ema_long - ema_short) / ema_short > ema_diff_threshold :
                 if len(self.historical_data) > 1 and self.historical_data[ema_s_col].iloc[-1] < self.historical_data[ema_s_col].iloc[-2]:
                           trend_direction = "WEAK_DOWN"
                 elif ema_short < ema_long : trend_direction = "WEAK_DOWN"
        return trend_direction, adx

    def evaluate_range(self) -> Optional[bool]:
        bb_upper_col = f'BB_Upper_{self.bb_period}_{self.bb_std_dev}'
        bb_lower_col = f'BB_Lower_{self.bb_period}_{self.bb_std_dev}'
        bb_mid_col = f'BB_Mid_{self.bb_period}_{self.bb_std_dev}'

        if not self._check_data_availability([bb_upper_col, bb_lower_col, bb_mid_col]): return None
        latest = self.historical_data.iloc[-1]
        bb_upper, bb_lower, bb_middle = latest[bb_upper_col], latest[bb_lower_col], latest[bb_mid_col]

        if pd.isna(bb_upper) or pd.isna(bb_lower) or pd.isna(bb_middle) or bb_middle == 0: return None
        bb_width_percent_of_price = (bb_upper - bb_lower) / bb_middle
        return bb_width_percent_of_price < self.bb_range_width_threshold_percent

    def evaluate_price_structure(self) -> Tuple[Optional[float], Optional[float]]:
        swing_high_col_marker, swing_low_col_marker = 'SwingHigh', 'SwingLow'
        temp_sh_created, temp_sl_created = False, False

        if swing_high_col_marker not in self.historical_data.columns:
             found_sh = [col for col in self.historical_data.columns if col.startswith('SwingHigh')]
             if found_sh: swing_high_col_marker = found_sh[0]
             else:
                  if not self._check_data_availability(['High']): return None,None
                  logger.debug("Calculating temporary swing highs for structure evaluation.")
                  self.historical_data['Temp_SH'] = find_swing_highs(self.historical_data, n_bars=5)
                  swing_high_col_marker, temp_sh_created = 'Temp_SH', True
        if swing_low_col_marker not in self.historical_data.columns:
             found_sl = [col for col in self.historical_data.columns if col.startswith('SwingLow')]
             if found_sl: swing_low_col_marker = found_sl[0]
             else:
                  if not self._check_data_availability(['Low']): return None,None
                  logger.debug("Calculating temporary swing lows for structure evaluation.")
                  self.historical_data['Temp_SL'] = find_swing_lows(self.historical_data, n_bars=5)
                  swing_low_col_marker, temp_sl_created = 'Temp_SL', True

        if not self._check_data_availability([swing_high_col_marker, swing_low_col_marker, 'High', 'Low']):
            if temp_sh_created and 'Temp_SH' in self.historical_data.columns: del self.historical_data['Temp_SH']
            if temp_sl_created and 'Temp_SL' in self.historical_data.columns: del self.historical_data['Temp_SL']
            return None, None

        recent_data = self.historical_data.tail(50)
        swing_highs_above = recent_data[(recent_data[swing_high_col_marker]) & (recent_data['High'] > self.base_price)]['High']
        nearest_sh = swing_highs_above.min() if not swing_highs_above.empty else None
        swing_lows_below = recent_data[(recent_data[swing_low_col_marker]) & (recent_data['Low'] < self.base_price)]['Low']
        nearest_sl = swing_lows_below.max() if not swing_lows_below.empty else None

        proximity_threshold = self.current_atr * self.swing_proximity_atr_multiplier
        final_nearest_sh = nearest_sh if nearest_sh is not None and abs(nearest_sh - self.base_price) < proximity_threshold else None
        final_nearest_sl = nearest_sl if nearest_sl is not None and abs(self.base_price - nearest_sl) < proximity_threshold else None

        if temp_sh_created and 'Temp_SH' in self.historical_data.columns: del self.historical_data['Temp_SH']
        if temp_sl_created and 'Temp_SL' in self.historical_data.columns: del self.historical_data['Temp_SL']
        return final_nearest_sh, final_nearest_sl

    def select_grid_model(self) -> Tuple[str, str]:
        volatility = self.evaluate_volatility()
        trend_dir, trend_strength_adx = self.evaluate_trend()
        is_ranging = self.evaluate_range()
        near_sh, near_sl = self.evaluate_price_structure()

        adx_val_str = f"{trend_strength_adx:.0f}" if trend_strength_adx is not None else 'N/A'
        reason = f"Vol:{volatility}, Trend:{trend_dir}(ADX:{adx_val_str}), Range:{is_ranging}, NearSH:{near_sh}, NearSL:{near_sl}"
        logger.info(f"SignalRouter Evaluation for {self.symbol}: {reason}")

        if is_ranging: return "RangeGridModel", f"Ranging (BB Width narrow). {reason}"
        if near_sh and self.base_trade_params.get('direction','').lower() == 'sell': return "StructureGridModel", f"Near Resistance ({near_sh}). {reason}"
        if near_sl and self.base_trade_params.get('direction','').lower() == 'buy': return "StructureGridModel", f"Near Support ({near_sl}). {reason}"

        # Stronger trend check, ADX threshold might be from config or a bit higher
        if trend_dir in ["UP", "DOWN"] and trend_strength_adx is not None and trend_strength_adx > self.adx_trend_threshold + 5:
            if volatility == "HIGH": return "VolatilityGridModel", f"Strong Trend ({trend_dir}) with High Volatility. {reason}"
            return "PyramidGridModel", f"Strong Trend ({trend_dir}). {reason}"

        if trend_dir in ["WEAK_UP", "WEAK_DOWN"] and volatility != "HIGH":
            if (trend_dir == "WEAK_UP" and self.base_trade_params.get('direction','').lower() == 'buy') or \
               (trend_dir == "WEAK_DOWN" and self.base_trade_params.get('direction','').lower() == 'sell'):
                return "StaticGridModel", f"Weak Trend ({trend_dir}) matching base direction. {reason}"

        if volatility == "HIGH": return "VolatilityGridModel", f"High Volatility. {reason}"
        if volatility == "LOW" and not is_ranging : return "DualGridModel", f"Low Volatility, potential breakout. {reason}"
        if self.base_trade_params.get('direction','').lower() in ['buy', 'sell']:
            return "StaticGridModel", f"Default (Static for {self.base_trade_params.get('direction')}). {reason}"
        return "VolatilityGridModel", f"Default (Volatility as fallback). {reason}"

if __name__ == '__main__':
    # Mock config for testing SignalRouter
    class MainConfigSR:
        DEFAULT_ATR_PERIOD = 14; ATR_MEDIAN_PERIODS = 20; ATR_HIGH_VOL_FACTOR = 1.3; ATR_LOW_VOL_FACTOR = 0.8
        EMA_SHORT_PERIOD = 10; EMA_LONG_PERIOD = 20; ADX_PERIOD = 14; ADX_TREND_THRESHOLD = 20
        BOLLINGER_BANDS_PERIOD = 20; BOLLINGER_BANDS_STD_DEV = 2; BB_RANGE_WIDTH_THRESHOLD_PERCENT = 0.02
        SWING_PROXIMITY_ATR_MULTIPLIER = 0.75
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_signal_router.log"
        SYMBOL_SETTINGS = {} # Not used by SignalRouter directly
        # Mock for config.get() used in __init__
        def __init__(self): # Make attributes directly accessible
            self.ATR_MEDIAN_PERIODS = self.ATR_MEDIAN_PERIODS # Ensure they are instance attributes
            self.ATR_HIGH_VOL_FACTOR = self.ATR_HIGH_VOL_FACTOR
            self.ATR_LOW_VOL_FACTOR = self.ATR_LOW_VOL_FACTOR
            self.BB_RANGE_WIDTH_THRESHOLD_PERCENT = self.BB_RANGE_WIDTH_THRESHOLD_PERCENT
            self.SWING_PROXIMITY_ATR_MULTIPLIER = self.SWING_PROXIMITY_ATR_MULTIPLIER
        def get(self, key, default): return getattr(self, key, default)

    import sys
    # Instance needed if config attributes are instance-based, or class if class-based
    mock_config_sr = MainConfigSR()
    sys.modules['grid_trader.config'] = mock_config_sr
    config = mock_config_sr # Rebind for current module scope

    import grid_trader.utils.logger as util_logger
    util_logger.config = mock_config_sr
    logger = get_logger(__name__)

    np.random.seed(42); num_bars = 200 # Increased num_bars
    prices = 1.1000 + np.cumsum(np.random.normal(0, 0.0005, num_bars))
    data = pd.DataFrame({'Open': prices - np.random.uniform(0,0.0003,num_bars), 'High': prices + np.random.uniform(0,0.0005,num_bars),
                         'Low': prices - np.random.uniform(0,0.0005,num_bars), 'Close': prices})
    data.index = pd.date_range(start='2023-01-01 00:00:00', periods=num_bars, freq='min') # Changed 'T' to 'min'

    data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'] = calculate_atr(data, period=mock_config_sr.DEFAULT_ATR_PERIOD)
    data[f'EMA_{mock_config_sr.EMA_SHORT_PERIOD}'] = calculate_ema(data, period=mock_config_sr.EMA_SHORT_PERIOD)
    data[f'EMA_{mock_config_sr.EMA_LONG_PERIOD}'] = calculate_ema(data, period=mock_config_sr.EMA_LONG_PERIOD)
    adx_df = calculate_adx(data, period=mock_config_sr.ADX_PERIOD)
    data = pd.concat([data, adx_df], axis=1)
    bb_df = calculate_bollinger_bands(data, period=mock_config_sr.BOLLINGER_BANDS_PERIOD, std_dev=mock_config_sr.BOLLINGER_BANDS_STD_DEV)
    data = pd.concat([data, bb_df], axis=1)
    data['SwingHigh'] = find_swing_highs(data, n_bars=5); data['SwingLow'] = find_swing_lows(data, n_bars=5)
    data = data.dropna()

    if data.empty: logger.error("Test setup: Hist data empty post-dropna.")
    else:
        logger.info(f"Test setup: Hist data ready with {len(data)} bars, latest close {data['Close'].iloc[-1]:.5f}")

        trend_data = data.copy()
        trend_data.loc[trend_data.index[-1], f'EMA_{mock_config_sr.EMA_SHORT_PERIOD}'] = trend_data[f'EMA_{mock_config_sr.EMA_LONG_PERIOD}'].iloc[-1] + 0.0010
        trend_data.loc[trend_data.index[-1], f'ADX_{mock_config_sr.ADX_PERIOD}'] = mock_config_sr.ADX_TREND_THRESHOLD + 10
        trend_data.loc[trend_data.index[-1], f'+DI_{mock_config_sr.ADX_PERIOD}'] = 30
        trend_data.loc[trend_data.index[-1], f'-DI_{mock_config_sr.ADX_PERIOD}'] = 15
        median_atr_val = trend_data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'].rolling(window=mock_config_sr.ATR_MEDIAN_PERIODS).median().iloc[-1]
        trend_data.loc[trend_data.index[-1], f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'] = median_atr_val * mock_config_sr.ATR_HIGH_VOL_FACTOR

        base_params_trend = {'symbol': 'EURUSD', 'direction': 'buy', 'base_price': trend_data['Close'].iloc[-1], 'atr': trend_data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'].iloc[-1]}
        sr_trend = SignalRouter(base_params_trend, trend_data)
        model_trend, reason_trend = sr_trend.select_grid_model()
        logger.info(f"Trending Scenario: Selected={model_trend}, Reason={reason_trend}")

        range_data = data.copy()
        mid_price = range_data['Close'].iloc[-20:].mean()
        range_data.loc[range_data.index[-1], f'BB_Upper_{mock_config_sr.BOLLINGER_BANDS_PERIOD}_{mock_config_sr.BOLLINGER_BANDS_STD_DEV}'] = mid_price + (mid_price * mock_config_sr.BB_RANGE_WIDTH_THRESHOLD_PERCENT * 0.3)
        range_data.loc[range_data.index[-1], f'BB_Lower_{mock_config_sr.BOLLINGER_BANDS_PERIOD}_{mock_config_sr.BOLLINGER_BANDS_STD_DEV}'] = mid_price - (mid_price * mock_config_sr.BB_RANGE_WIDTH_THRESHOLD_PERCENT * 0.3)
        range_data.loc[range_data.index[-1], f'BB_Mid_{mock_config_sr.BOLLINGER_BANDS_PERIOD}_{mock_config_sr.BOLLINGER_BANDS_STD_DEV}'] = mid_price
        range_data.loc[range_data.index[-1], f'ADX_{mock_config_sr.ADX_PERIOD}'] = mock_config_sr.ADX_TREND_THRESHOLD - 5

        base_params_range = {'symbol': 'EURUSD', 'direction': 'buy', 'base_price': range_data['Close'].iloc[-1], 'atr': range_data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'].iloc[-1]}
        sr_range = SignalRouter(base_params_range, range_data)
        model_range, reason_range = sr_range.select_grid_model()
        logger.info(f"Ranging Scenario: Selected={model_range}, Reason={reason_range}")

        struct_data = data.copy()
        struct_data.loc[struct_data.index[-5], 'SwingLow'] = True
        struct_data.loc[struct_data.index[-5], 'Low'] = struct_data['Close'].iloc[-1] - (struct_data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'].iloc[-1] * (mock_config_sr.SWING_PROXIMITY_ATR_MULTIPLIER * 0.5))

        base_params_struct = {'symbol': 'EURUSD', 'direction': 'buy', 'base_price': struct_data['Close'].iloc[-1], 'atr': struct_data[f'ATR_{mock_config_sr.DEFAULT_ATR_PERIOD}'].iloc[-1]}
        sr_struct = SignalRouter(base_params_struct, struct_data)
        model_struct, reason_struct = sr_struct.select_grid_model()
        logger.info(f"Near Structure Scenario: Selected={model_struct}, Reason={reason_struct}")

        logger.info("SignalRouter tests complete.")
