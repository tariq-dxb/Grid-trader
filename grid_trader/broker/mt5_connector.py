# grid_trader/broker/mt5_connector.py
import MetaTrader5 as mt5
from typing import Optional, Dict, Any, List # Added List
import pandas as pd
from datetime import datetime, timezone
import time

try:
    from ..utils.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

_mt5_initialized: bool = False
MT5_TIMEFRAME_MAP: Dict[str, Any] = {}
MT5_ORDER_TYPE_MAP: Dict[str, Any] = {}

def _initialize_constants_from_mt5():
    global MT5_TIMEFRAME_MAP, MT5_ORDER_TYPE_MAP
    if not MT5_TIMEFRAME_MAP and 'mt5' in globals() and hasattr(mt5, 'TIMEFRAME_M1'):
        MT5_TIMEFRAME_MAP = {
            "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3, "M4": mt5.TIMEFRAME_M4,
            "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6, "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12,
            "M15": mt5.TIMEFRAME_M15, "M20": mt5.TIMEFRAME_M20, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2, "H3": mt5.TIMEFRAME_H3, "H4": mt5.TIMEFRAME_H4,
            "H6": mt5.TIMEFRAME_H6, "H8": mt5.TIMEFRAME_H8, "H12": mt5.TIMEFRAME_H12,
            "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1
        }
    elif not MT5_TIMEFRAME_MAP:
         if 'mt5' in globals() and not hasattr(mt5, 'TIMEFRAME_M1'):
            logger.warning("MT5 module seems imported but TIMEFRAME_M1 missing.")
         elif 'mt5' not in globals():
            logger.warning("MT5 module not available. MT5_TIMEFRAME_MAP empty.")
    if not MT5_ORDER_TYPE_MAP and 'mt5' in globals() and hasattr(mt5, 'ORDER_TYPE_BUY_LIMIT'):
        MT5_ORDER_TYPE_MAP = {
            "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT, "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP, "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP,
            "MARKET_BUY": mt5.ORDER_TYPE_BUY, "MARKET_SELL": mt5.ORDER_TYPE_SELL,
        }
    elif not MT5_ORDER_TYPE_MAP:
        if 'mt5' in globals() and not hasattr(mt5, 'ORDER_TYPE_BUY_LIMIT'):
            logger.warning("MT5 module seems imported but ORDER_TYPE_BUY_LIMIT missing.")
        elif 'mt5' not in globals():
             logger.warning("MT5 module not available. MT5_ORDER_TYPE_MAP empty.")

def connect_to_mt5(path: Optional[str] = None, timeout_ms: int = 5000) -> bool:
    global _mt5_initialized
    if _mt5_initialized: logger.info("MT5 connection already initialized."); return True
    try:
        logger.info("Attempting to initialize MetaTrader 5 connection...")
        if path: initialized = mt5.initialize(path=path, timeout=timeout_ms)
        else: initialized = mt5.initialize(timeout=timeout_ms)
        if not initialized:
            error_code = mt5.last_error(); error_message = str(error_code)
            if isinstance(error_code, tuple) and len(error_code) > 0: error_message = f"Code: {error_code[0]}, Message: {error_code[1]}"
            logger.error(f"MetaTrader 5 initialization failed. Error: {error_message}"); _mt5_initialized = False; return False
        _initialize_constants_from_mt5()
        version = mt5.version(); logger.info(f"MetaTrader 5 connection initialized. Version: {version}")
        terminal_info = mt5.terminal_info()
        if terminal_info: logger.info(f"Connected to MT5: {terminal_info.name} (Build {terminal_info.build}) on {terminal_info.path}")
        else: logger.warning("Could not retrieve MT5 terminal_info.")
        account_info = mt5.account_info()
        if account_info: logger.info(f"Account: {account_info.login}, Server: {account_info.server}, Bal: {account_info.balance} {account_info.currency}")
        else: logger.warning("Could not retrieve MT5 account_info.")
        _mt5_initialized = True; return True
    except Exception as e: logger.error(f"Exception during MT5 initialization: {e}", exc_info=True); _mt5_initialized = False; return False

def disconnect_from_mt5():
    global _mt5_initialized
    if _mt5_initialized:
        try: logger.info("Shutting down MT5 connection..."); mt5.shutdown(); _mt5_initialized = False; logger.info("MT5 connection shut down.")
        except Exception as e: logger.error(f"Exception during MT5 shutdown: {e}", exc_info=True); _mt5_initialized = False
    else: logger.info("MT5 connection not initialized or already shut down.")

def is_mt5_connected() -> bool: return _mt5_initialized

def get_mt5_account_info() -> Optional[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error("Cannot get account info: MT5 not connected."); return None
    try:
        account_info = mt5.account_info()
        if account_info is None:
            error_code = mt5.last_error(); error_message = str(error_code)
            if isinstance(error_code, tuple) and len(error_code) > 0: error_message = f"Code: {error_code[0]}, Message: {error_code[1]}"
            logger.error(f"Failed to retrieve account info. Error: {error_message}"); return None
        account_info_dict = account_info._asdict()
        logger.info(f"Retrieved account information: {account_info_dict}"); return account_info_dict
    except Exception as e: logger.error(f"Exception retrieving account info: {e}", exc_info=True); return None

def get_symbol_tick_info(symbol: str) -> Optional[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error(f"Cannot get tick for {symbol}: MT5 not connected."); return None
    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None: logger.error(f"Symbol {symbol} not found. Err: {mt5.last_error()}"); return None
        if not symbol_info.visible:
            logger.info(f"Symbol {symbol} not visible, selecting.");
            if not mt5.symbol_select(symbol, True): logger.error(f"Failed to select {symbol}. Err: {mt5.last_error()}"); return None
            time.sleep(0.1)
        tick_info = mt5.symbol_info_tick(symbol)
        if tick_info is None: logger.error(f"Failed to get tick for {symbol}. Err: {mt5.last_error()}"); return None
        tick_info_dict = tick_info._asdict()
        if 'time' in tick_info_dict: tick_info_dict['time_dt'] = datetime.fromtimestamp(tick_info_dict['time'], tz=timezone.utc)
        logger.debug(f"Tick for {symbol}: {tick_info_dict}"); return tick_info_dict
    except Exception as e: logger.error(f"Exception retrieving tick for {symbol}: {e}", exc_info=True); return None

def get_historical_bars(symbol: str, timeframe_str: str, num_bars: int, from_pos: int = 0) -> Optional[pd.DataFrame]:
    if not is_mt5_connected(): logger.error(f"Cannot get bars for {symbol}: MT5 not connected."); return None
    if not MT5_TIMEFRAME_MAP: _initialize_constants_from_mt5()
    mt5_timeframe = MT5_TIMEFRAME_MAP.get(timeframe_str.upper())
    if mt5_timeframe is None: logger.error(f"Invalid timeframe: {timeframe_str}. Supported: {list(MT5_TIMEFRAME_MAP.keys())}"); return None
    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None: logger.error(f"Symbol {symbol} not found. Err: {mt5.last_error()}"); return None
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True): logger.error(f"Failed to select {symbol}. Err: {mt5.last_error()}"); return None
            time.sleep(0.1)
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, from_pos, num_bars)
        if rates is None or len(rates) == 0: logger.warning(f"Failed to get rates for {symbol} {timeframe_str}. Err: {mt5.last_error()}"); return None
        rates_df = pd.DataFrame(rates)
        rates_df['Timestamp'] = pd.to_datetime(rates_df['time'], unit='s', utc=True)
        rates_df = rates_df.rename(columns={'time': 'TimeEpoch', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume', 'spread': 'Spread', 'real_volume': 'RealVolume'})
        rates_df = rates_df.set_index('Timestamp')[['Open', 'High', 'Low', 'Close', 'Volume', 'Spread', 'RealVolume']]
        logger.info(f"Retrieved {len(rates_df)} bars for {symbol} {timeframe_str}."); return rates_df
    except Exception as e: logger.error(f"Exception retrieving bars for {symbol} {timeframe_str}: {e}", exc_info=True); return None

def get_mt5_symbol_properties(symbol: str) -> Optional[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error(f"Cannot get props for {symbol}: MT5 not connected."); return None
    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"Failed to get info for {symbol}. Err: {mt5.last_error()}")
            logger.info(f"Attempting select {symbol} & retry...");
            if mt5.symbol_select(symbol, True):
                time.sleep(0.2); symbol_info = mt5.symbol_info(symbol)
                if symbol_info is None: logger.error(f"Still failed for {symbol} after select. Err: {mt5.last_error()}"); return None
            else: logger.error(f"Failed to select {symbol}."); return None
        props_dict = symbol_info._asdict()
        standardized_props = {
            'name': props_dict.get('name'), 'description': props_dict.get('description'),
            'digits': props_dict.get('digits'), 'point': props_dict.get('point'),
            'spread': props_dict.get('spread'), 'contract_size': props_dict.get('trade_contract_size'),
            'volume_min': props_dict.get('volume_min'), 'volume_max': props_dict.get('volume_max'),
            'volume_step': props_dict.get('volume_step'), 'currency_base': props_dict.get('currency_base'),
            'currency_profit': props_dict.get('currency_profit'), 'currency_margin': props_dict.get('currency_margin'),
            'trade_mode_description': props_dict.get('trade_mode_description'),
            'filling_modes_description': properties_dict.get('filling_modes_description'), # Corrected variable name
            'trade_tick_value': props_dict.get('trade_tick_value'), 'trade_tick_size': props_dict.get('trade_tick_size'),
            'raw_mt5_properties': props_dict
        }
        logger.info(f"Props for {symbol}: Digits={standardized_props.get('digits')}, ContractSize={standardized_props.get('contract_size')}")
        return standardized_props
    except Exception as e: logger.error(f"Exception retrieving props for {symbol}: {e}", exc_info=True); return None

def send_mt5_trade_request(request: Dict[str, Any]) -> Optional[Any]:
    if not is_mt5_connected(): logger.error("Cannot send trade request: MT5 not connected."); return None
    try:
        logger.info(f"Sending MT5 trade request: {request}")
        trade_result = mt5.order_send(request)
        if trade_result is None: logger.error(f"mt5.order_send() returned None. Req: {request}. Err: {mt5.last_error()}"); return None
        logger.info(f"MT5 trade_result: retcode={trade_result.retcode}, comment='{trade_result.comment}', ticket={trade_result.order}, vol={trade_result.volume}, price={trade_result.price}")
        return trade_result
    except Exception as e: logger.error(f"Exception during mt5.order_send(): {e}. Req: {request}", exc_info=True); return None

def get_mt5_open_positions(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error("Cannot get open positions: MT5 not connected."); return []
    try:
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions is None: logger.error(f"Failed to retrieve positions. Error: {mt5.last_error()}"); return []
        return [p._asdict() for p in positions]
    except Exception as e: logger.error(f"Exception while retrieving open positions: {e}", exc_info=True); return []

def get_mt5_pending_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error("Cannot get pending orders: MT5 not connected."); return []
    if not MT5_ORDER_TYPE_MAP: _initialize_constants_from_mt5() # Ensure map is populated
    try:
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        if orders is None: logger.error(f"Failed to retrieve pending orders. Error: {mt5.last_error()}"); return []
        # Filter for actual pending order types as mt5.orders_get() can return other states if not filtered by group/symbol sometimes
        pending_types = [MT5_ORDER_TYPE_MAP[k] for k in ["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"] if k in MT5_ORDER_TYPE_MAP]
        return [o._asdict() for o in orders if o.type in pending_types]
    except Exception as e: logger.error(f"Exception while retrieving pending orders: {e}", exc_info=True); return []

def get_mt5_order_history_by_ticket(ticket: int) -> List[Dict[str, Any]]:
    if not is_mt5_connected(): logger.error(f"Cannot get history for ticket {ticket}: MT5 not connected."); return []
    try:
        deals = mt5.history_deals_get(ticket=ticket)
        if deals is None: logger.warning(f"Failed to get deal history for ticket {ticket}. Error: {mt5.last_error()}"); return []
        return [d._asdict() for d in deals]
    except Exception as e: logger.error(f"Exception retrieving deal history for ticket {ticket}: {e}", exc_info=True); return []


if __name__ == '__main__':
    logger.info("--- Testing MT5 Connector (requires running MT5 terminal and installed MetaTrader5 package) ---")
    mt5_path = None
    try:
        if connect_to_mt5(path=mt5_path):
            logger.info("Successfully connected to MT5.")
            account_details = get_mt5_account_info()
            if account_details: logger.info(f"Main test - Account Balance: {account_details.get('balance')} {account_details.get('currency')}, Leverage: {account_details.get('leverage')}")
            else: logger.error("Main test - Failed to get account details.")

            eurusd_props = get_mt5_symbol_properties("EURUSD")
            if eurusd_props: logger.info(f"Main test - EURUSD Props: Digits={eurusd_props.get('digits')}, Point={eurusd_props.get('point')}, Spread={eurusd_props.get('spread')}")
            else: logger.warning("Could not fetch EURUSD properties for main test.")

            eurusd_tick = get_symbol_tick_info("EURUSD")
            if eurusd_tick: logger.info(f"EURUSD Tick: Ask={eurusd_tick.get('ask')}, Bid={eurusd_tick.get('bid')}, Time={eurusd_tick.get('time_dt')}")
            else: logger.warning("Could not fetch EURUSD tick for main test.")

            eurusd_m1_bars = get_historical_bars("EURUSD", "M1", 10)
            if eurusd_m1_bars is not None and not eurusd_m1_bars.empty: logger.info(f"EURUSD M1 Bars (last 2):\n{eurusd_m1_bars.tail(2)}")
            else: logger.warning("Could not fetch EURUSD M1 bars for main test.")

            open_pos = get_mt5_open_positions()
            logger.info(f"Main test - Open Positions: {len(open_pos)}")
            if open_pos: logger.info(f"  First position: {open_pos[0]}")

            pending = get_mt5_pending_orders()
            logger.info(f"Main test - Pending Orders: {len(pending)}")
            if pending:
                logger.info(f"  First pending: {pending[0]}")
                history = get_mt5_order_history_by_ticket(pending[0]['ticket'])
                logger.info(f"    History for pending order {pending[0]['ticket']}: {history}")


            if eurusd_props and MT5_ORDER_TYPE_MAP and eurusd_tick: # Ensure maps and data are available
                test_bl_request = {
                    "action": mt5.TRADE_ACTION_PENDING, "symbol": "EURUSD",
                    "volume": eurusd_props.get('volume_min', 0.01),
                    "type": MT5_ORDER_TYPE_MAP["BUY_LIMIT"],
                    "price": round(eurusd_tick.get('bid') - 0.0050 * (10**eurusd_props.get('digits',5)), eurusd_props.get('digits', 5)), # Using points
                    "sl": round(eurusd_tick.get('bid') - 0.0100 * (10**eurusd_props.get('digits',5)), eurusd_props.get('digits', 5)),
                    "tp": round(eurusd_tick.get('bid') + 0.0100 * (10**eurusd_props.get('digits',5)), eurusd_props.get('digits', 5)),
                    "magic": 12345, "comment": "Test BL via connector",
                    "type_filling": mt5.symbol_info("EURUSD").filling_modes[0], # Use first available filling mode
                    "type_time": mt5.ORDER_TIME_GTC
                }
                bl_result = send_mt5_trade_request(test_bl_request)
                if bl_result:
                    logger.info(f"Main test - BL Order send result: {bl_result.comment}, Ticket: {bl_result.order}")
                    if bl_result.retcode == mt5.TRADE_RETCODE_PLACED and bl_result.order > 0:
                        time.sleep(0.5)
                        cancel_request = {"action": mt5.TRADE_ACTION_REMOVE, "order": bl_result.order, "comment": "Test Cancel"}
                        cancel_result = send_mt5_trade_request(cancel_request)
                        if cancel_result: logger.info(f"Main test - Cancel result: {cancel_result.comment}")

            disconnect_from_mt5()
        else:
            logger.error("Failed to connect to MT5 in test. Ensure terminal is running/accessible.")
    except NameError as ne:
        logger.error(f"MetaTrader5 package not available or import failed: {ne}")
        logger.info("Most MT5 functions in __main__ will be skipped.")
    except Exception as e:
        logger.error(f"An unexpected error occurred during MT5 connection test: {e}", exc_info=True)
    logger.info("--- MT5 Connection Test Finished ---")
