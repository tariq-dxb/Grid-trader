# grid_trader/main.py
import time
import pandas as pd
import numpy as np

# Ensure this 'config' is the one that gets modified and seen by others
from grid_trader import config as package_config

from grid_trader.utils.logger import get_logger
from grid_trader.utils.indicators import (calculate_atr, calculate_ema,
                                           calculate_adx, calculate_bollinger_bands)
from grid_trader.utils.price_structure import find_swing_highs, find_swing_lows
from grid_trader.engine.risk_manager import RiskManager
from grid_trader.engine.order_manager import OrderManager
from grid_trader.engine.grid_manager import GridManager

# Initial logger using whatever config is loaded first
logger = get_logger("grid_trader_main_module_initial")

def generate_sample_historical_data(num_bars=200, current_config=None) -> pd.DataFrame:
    # Pass current_config to ensure it uses the (potentially mocked) config from __main__
    cfg = current_config if current_config else package_config
    logger.info(f"Generating sample historical data with {num_bars} bars...")
    base_start_price = 1.10000
    price_changes = np.random.normal(0, 0.0002, num_bars)
    price_series = base_start_price + np.cumsum(price_changes)
    for _ in range(num_bars // 20):
        idx = np.random.randint(0, num_bars); event_strength = np.random.normal(0, 0.001)
        price_series[idx:] += event_strength
    timestamps = pd.date_range(start='2023-01-01 00:00:00', periods=num_bars, freq='min')
    data = pd.DataFrame(index=timestamps)
    data['Open'] = np.insert(price_series[:-1], 0, base_start_price + np.random.normal(0, 0.0001))
    data['Close'] = price_series
    data['High'] = np.maximum(data['Open'], data['Close']) + np.abs(np.random.normal(0, 0.0003, num_bars))
    data['Low'] = np.minimum(data['Open'], data['Close']) - np.abs(np.random.normal(0, 0.0003, num_bars))
    data['High'] = data[['High', 'Open', 'Close']].max(axis=1)
    data['Low'] = data[['Low', 'Open', 'Close']].min(axis=1)

    logger.info("Calculating indicators for sample data...")
    data[f'ATR_{cfg.DEFAULT_ATR_PERIOD}'] = calculate_atr(data, period=cfg.DEFAULT_ATR_PERIOD)
    data[f'EMA_{cfg.EMA_SHORT_PERIOD}'] = calculate_ema(data, period=cfg.EMA_SHORT_PERIOD)
    data[f'EMA_{cfg.EMA_LONG_PERIOD}'] = calculate_ema(data, period=cfg.EMA_LONG_PERIOD)
    adx_df = calculate_adx(data, period=cfg.ADX_PERIOD)
    data = pd.concat([data, adx_df], axis=1)
    bb_df = calculate_bollinger_bands(data, period=cfg.BOLLINGER_BANDS_PERIOD, std_dev=cfg.BOLLINGER_BANDS_STD_DEV)
    data = pd.concat([data, bb_df], axis=1)
    swing_n_bars = 5
    data[f'SwingHigh_N{swing_n_bars}'] = find_swing_highs(data, n_bars=swing_n_bars)
    data[f'SwingLow_N{swing_n_bars}'] = find_swing_lows(data, n_bars=swing_n_bars)
    data['SwingHigh'] = data[f'SwingHigh_N{swing_n_bars}']; data['SwingLow'] = data[f'SwingLow_N{swing_n_bars}']
    original_len = len(data); data = data.dropna()
    logger.info(f"Sample historical data generated. Original len: {original_len}, After dropna: {data.shape[0]}")
    if data.empty: raise ValueError("Historical data generation resulted in an empty DataFrame after indicator calculation.")
    return data

def run_example_session(current_config=None):
    cfg = current_config if current_config else package_config
    session_logger = get_logger("grid_trader_session") # Uses config patched by __main__
    session_logger.info("Starting Grid Trader Example Session...")
    account_balance = 10000.00; leverage = "1:100"

    # SYMBOL_SETTINGS should be correctly patched in cfg by __main__
    if "EURUSD" not in cfg.SYMBOL_SETTINGS: # This check might be redundant if __main__ guarantees it
        session_logger.error("EURUSD settings still missing in config despite __main__ setup! Check config patching.")
        # Fallback for safety, though it indicates a deeper problem
        cfg.SYMBOL_SETTINGS["EURUSD"] = {"pip_value_per_lot": 10.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 5, "point_value": 0.00001, "contract_size": 100000}

    risk_mngr = RiskManager(account_balance=account_balance, leverage=leverage)
    order_mngr = OrderManager(risk_manager=risk_mngr)
    grid_mngr = GridManager(risk_manager=risk_mngr, order_manager=order_mngr)

    try: historical_data = generate_sample_historical_data(num_bars=150, current_config=cfg)
    except ValueError as e: session_logger.error(f"Failed to generate historical data: {e}"); return

    current_close_price = historical_data['Close'].iloc[-1]
    current_atr_value = historical_data[f'ATR_{cfg.DEFAULT_ATR_PERIOD}'].iloc[-1]
    if pd.isna(current_atr_value): session_logger.error("Latest ATR is NaN."); return

    eurusd_decimals = cfg.SYMBOL_SETTINGS.get('EURUSD', {}).get('decimals', 5)
    base_trade_params = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': current_close_price,
        'base_sl': round(current_close_price - (current_atr_value * 2), eurusd_decimals),
        'base_tp': round(current_close_price + (current_atr_value * 4), eurusd_decimals),
        'base_size_lots': 0.0, 'atr': current_atr_value
    }
    session_logger.info(f"Base trade parameters for new grid: {base_trade_params}")
    grid_id = grid_mngr.create_new_grid(base_trade_params, historical_data)
    if not grid_id: session_logger.error("Failed to create grid. Session ends."); return

    session_logger.info(f"Grid '{grid_id}' created. Pending orders: {len(order_mngr.get_pending_orders())}")
    for order in order_mngr.get_pending_orders(): session_logger.debug(f"  {order}")

    session_logger.info("Starting basic market simulation loop (5 steps)...")
    for i in range(5):
        last_close = historical_data['Close'].iloc[-1]; sim_atr = base_trade_params['atr'] if base_trade_params['atr'] > 0 else 0.00050
        price_change = np.random.normal(0, sim_atr * 0.3)
        sim_high = last_close + price_change + abs(np.random.normal(0, sim_atr * 0.1))
        sim_low = last_close + price_change - abs(np.random.normal(0, sim_atr * 0.1))
        sim_close = last_close + price_change
        sim_high = max(sim_high, sim_close); sim_low = min(sim_low, sim_close)
        current_market_snapshot = {base_trade_params['symbol']: {"high": round(sim_high, 5), "low": round(sim_low, 5), "close": round(sim_close, 5)}}
        session_logger.info(f"\n--- Market Update {i+1} --- Symbol: {base_trade_params['symbol']}, Low: {current_market_snapshot[base_trade_params['symbol']]['low']}, High: {current_market_snapshot[base_trade_params['symbol']]['high']}, Close: {current_market_snapshot[base_trade_params['symbol']]['close']}")
        grid_mngr.process_market_data_update(current_market_snapshot)
        session_logger.info(f"After update {i+1}: Pending Orders={len(order_mngr.get_pending_orders())}, Active={len(order_mngr.get_active_positions())}")
        if order_mngr.get_pending_orders(): session_logger.debug("Current Pending:")
        for po in order_mngr.get_pending_orders(): session_logger.debug(f"  {po}")
        if order_mngr.get_active_positions(): session_logger.debug("Current Active:")
        for ap in order_mngr.get_active_positions(): session_logger.debug(f"  {ap}")
        new_bar_timestamp = historical_data.index[-1] + pd.Timedelta(minutes=1)
        new_data_row = pd.DataFrame([{'Open': last_close, 'High': sim_high, 'Low': sim_low, 'Close': sim_close}], index=[new_bar_timestamp])
        historical_data = pd.concat([historical_data, new_data_row])
        time.sleep(0.01)
    session_logger.info("Example session finished. Final Orders State:")
    all_orders = order_mngr.get_all_orders()
    if not all_orders:
        session_logger.info(" No orders managed.")
    else:
        for order_obj in all_orders:
            session_logger.info(f"  {order_obj}")

if __name__ == "__main__":
    # 1. Create a mock config object or class (can be MainConfigGM from other tests if suitable)
    class MainPyConfig:
        LOG_LEVEL = "DEBUG"; LOG_FILE = "grid_trader_main.log"
        DEFAULT_ATR_PERIOD = 14; EMA_SHORT_PERIOD = 12; EMA_LONG_PERIOD = 26
        ADX_PERIOD = 14; ADX_TREND_THRESHOLD = 25
        BOLLINGER_BANDS_PERIOD = 20; BOLLINGER_BANDS_STD_DEV = 2
        DEFAULT_MAX_REGENERATION_ATTEMPTS = 3; DEFAULT_COOLDOWN_PERIOD_BARS = 5
        BAR_DURATION_SECONDS = 60; DEFAULT_SL_TP_WIDENING_FACTOR = 1.2
        MAX_ACCOUNT_RISK_PERCENTAGE = 2.0; DEFAULT_RISK_PER_TRADE_USD = 10.0
        SYMBOL_SETTINGS = {
            "EURUSD": {"pip_value_per_lot": 10.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 5, "point_value": 0.00001, "contract_size": 100000}
        }
        ATR_MEDIAN_PERIODS = 50; ATR_HIGH_VOL_FACTOR = 1.5; ATR_LOW_VOL_FACTOR = 0.7
        BB_RANGE_WIDTH_THRESHOLD_PERCENT = 0.03; SWING_PROXIMITY_ATR_MULTIPLIER = 0.5
        VolatilityGridModel_params = {}; StaticGridModel_params = {}; DualGridModel_params = {}
        PyramidGridModel_params = {}; StructureGridModel_params = {}; RangeGridModel_params = {}
        # Add REGEN widen factors if GridManager test showed them from config
        REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_0 = 1.2
        REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_1 = 1.5
        REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_2 = 2.0 # Example for 3rd attempt if max was 3

    mock_config_object = MainPyConfig()

    # 2. Patch sys.modules *before* any local package imports if they depend on grid_trader.config
    import sys
    sys.modules['grid_trader.config'] = mock_config_object

    # 3. Rebind the 'package_config' alias in *this* module (main.py)
    package_config = mock_config_object # Crucial for main.py's direct use of 'package_config'
                                        # And for generate_sample_historical_data if it uses it.

    # 4. Patch 'config' attribute in all already imported modules from this package
    # This ensures that any 'from .. import config' they did at their load time
    # now effectively points to our mock_config_object.
    import grid_trader.utils.logger # Ensure module is loaded to patch it
    grid_trader.utils.logger.config = mock_config_object

    # For engine components (RiskManager, OrderManager, GridManager, SignalRouter)
    # their 'from .. import config' will resolve to sys.modules['grid_trader.config']
    # if they are imported *after* the sys.modules patch.
    # The current structure imports them at the top of main.py. So, we need to patch them.
    import grid_trader.engine.risk_manager
    grid_trader.engine.risk_manager.config = mock_config_object
    import grid_trader.engine.order_manager
    grid_trader.engine.order_manager.config = mock_config_object
    import grid_trader.engine.signal_router
    grid_trader.engine.signal_router.config = mock_config_object
    import grid_trader.engine.grid_manager # GridManager itself
    grid_trader.engine.grid_manager.package_config = mock_config_object # It uses 'package_config' alias
    grid_trader.engine.grid_manager.config = mock_config_object # If it also has a direct 'config' import/use

    # For model files (they also use 'from .. import config')
    import grid_trader.models.base_model
    grid_trader.models.base_model.config = mock_config_object
    import grid_trader.models.volatility_grid
    grid_trader.models.volatility_grid.config = mock_config_object
    import grid_trader.models.dual_grid
    grid_trader.models.dual_grid.config = mock_config_object
    import grid_trader.models.static_grid
    grid_trader.models.static_grid.config = mock_config_object
    import grid_trader.models.pyramid_grid
    grid_trader.models.pyramid_grid.config = mock_config_object
    import grid_trader.models.structure_grid
    grid_trader.models.structure_grid.config = mock_config_object
    import grid_trader.models.range_grid
    grid_trader.models.range_grid.config = mock_config_object

    # 5. Now that all configs are patched, re-initialize the main logger for this script
    logger = get_logger("grid_trader_main")

    run_example_session(current_config=mock_config_object) # Pass the mock config explicitly too
                                                          # for functions like generate_sample_historical_data
                                                          # to ensure they use it if they take it as param.
