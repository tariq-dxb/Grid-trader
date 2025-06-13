# grid_trader/engine/order_manager.py
import uuid
import time
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime, timezone # Added for MT5 sync

from .. import config
from ..utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    class DummyMT5: # type: ignore
        TRADE_ACTION_DEAL = 1; TRADE_ACTION_PENDING = 0; TRADE_ACTION_REMOVE = 2; TRADE_ACTION_MODIFY = 3; TRADE_ACTION_SLTP = 6
        ORDER_TYPE_BUY = 0; ORDER_TYPE_SELL = 1; ORDER_TYPE_BUY_LIMIT = 2; ORDER_TYPE_SELL_LIMIT = 3
        ORDER_TYPE_BUY_STOP = 4; ORDER_TYPE_SELL_STOP = 5
        ORDER_TIME_GTC = 0; ORDER_FILLING_FOK = 1 ; ORDER_FILLING_IOC = 2; ORDER_FILLING_RETURN = 3
        TRADE_RETCODE_DONE = 10009; TRADE_RETCODE_PLACED = 10008
        DEAL_ENTRY_OUT = 1 # Exit deal
        DEAL_REASON_SL = 1; DEAL_REASON_TP = 2; DEAL_REASON_CLIENT = 0 # Simplified
    mt5 = DummyMT5()

logger = get_logger(__name__)

class OrderStatus(Enum):
    PENDING = "PENDING"; ACTIVE = "ACTIVE"; CANCELLED = "CANCELLED"; FILLED = "FILLED"
    STOPPED_OUT = "STOPPED_OUT"; TP_HIT = "TP_HIT"; CLOSED = "CLOSED"

class OrderType(Enum):
    MARKET_BUY = "MARKET_BUY"; MARKET_SELL = "MARKET_SELL"
    BUY_LIMIT = "BUY_LIMIT"; SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"; SELL_STOP = "SELL_STOP"

class Order:
    def __init__(self, symbol: str, order_type: OrderType, entry_price: float,
                 sl_price: float, tp_price: float, lot_size: float,
                 grid_id: Optional[str] = None, original_order_id: Optional[str] = None):
        self.order_id: str = str(uuid.uuid4())
        self.symbol: str = symbol; self.order_type: OrderType = order_type
        self.entry_price: float = entry_price; self.sl_price: float = sl_price
        self.tp_price: float = tp_price; self.lot_size: float = lot_size
        self.status: OrderStatus = OrderStatus.PENDING
        self.grid_id: Optional[str] = grid_id
        self.original_order_id: Optional[str] = original_order_id if original_order_id else self.order_id
        self.creation_time: float = time.time(); self.fill_time: Optional[float] = None
        self.close_time: Optional[float] = None; self.fill_price: Optional[float] = None
        self.close_price: Optional[float] = None; self.pnl: Optional[float] = None
        self.regeneration_attempts: int = 0; self.last_regeneration_time: Optional[float] = None
        self.initial_sl_price = sl_price; self.initial_tp_price = tp_price
        self.broker_order_id: Optional[int] = None

    def __repr__(self):
        return (f"Order({self.order_id[-6:]} | BkrID:{self.broker_order_id or 'N/A'} | {self.symbol} {self.order_type.value} {self.lot_size} lots @ {self.entry_price} "
                f"SL:{self.sl_price} TP:{self.tp_price} | Status: {self.status.value} | GridID: {self.grid_id} | Regen: {self.regeneration_attempts})")

class OrderManager:
    def __init__(self, risk_manager: Any, mt5_connector: Optional[Any] = None):
        self.risk_manager = risk_manager
        self.mt5_connector = mt5_connector
        self.orders: Dict[str, Order] = {}
        self.pending_orders: List[str] = []
        self.active_positions: List[str] = []
        self.regeneration_counts: Dict[str, int] = {}
        self.max_regeneration_attempts = config.DEFAULT_MAX_REGENERATION_ATTEMPTS
        bar_duration_seconds = getattr(config, 'BAR_DURATION_SECONDS', 60)
        self.cooldown_period_seconds = config.DEFAULT_COOLDOWN_PERIOD_BARS * bar_duration_seconds
        mt5_conn_status = False
        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected'):
            mt5_conn_status = self.mt5_connector.is_mt5_connected()
        logger.info(f"OrderManager initialized. Max Regen: {self.max_regeneration_attempts}, Cooldown: {self.cooldown_period_seconds}s, MT5 Connected: {mt5_conn_status}")

    def _add_order_to_collections(self, order: Order):
        self.orders[order.order_id] = order
        if order.status == OrderStatus.PENDING:
            if order.order_id not in self.pending_orders: self.pending_orders.append(order.order_id)
        elif order.status in [OrderStatus.ACTIVE, OrderStatus.FILLED]:
            if order.order_id not in self.active_positions: self.active_positions.append(order.order_id)
            if order.order_id in self.pending_orders: self.pending_orders.remove(order.order_id)

    def _remove_order_from_collections(self, order_id: str, old_status: OrderStatus):
        if old_status == OrderStatus.PENDING and order_id in self.pending_orders: self.pending_orders.remove(order_id)
        elif old_status in [OrderStatus.ACTIVE, OrderStatus.FILLED] and order_id in self.active_positions: self.active_positions.remove(order_id)

    def place_new_order(self, symbol: str, order_type_str: str, entry_price: float,
                        sl_price: float, tp_price: float, lot_size: float,
                        grid_id: Optional[str] = None,
                        original_order_id_for_regen: Optional[str] = None,
                        is_regeneration: bool = False) -> Optional[Order]:
        try: order_type_enum = OrderType[order_type_str.upper()]
        except KeyError: logger.error(f"Invalid order type: {order_type_str}"); return None
        if lot_size <= 0: logger.warning(f"Invalid lot size: {lot_size} for {symbol}"); return None
        if order_type_enum in [OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
            if entry_price <= sl_price: logger.warning(f"Buy SL not below entry: E {entry_price} SL {sl_price}"); return None
            if tp_price != 0 and entry_price >= tp_price: logger.warning(f"Buy TP not above entry: E {entry_price} TP {tp_price}"); return None
        elif order_type_enum in [OrderType.SELL_LIMIT, OrderType.SELL_STOP]:
            if entry_price >= sl_price: logger.warning(f"Sell SL not above entry: E {entry_price} SL {sl_price}"); return None
            if tp_price != 0 and entry_price <= tp_price: logger.warning(f"Sell TP not below entry: E {entry_price} TP {tp_price}"); return None

        internal_order = Order(symbol, order_type_enum, entry_price, sl_price, tp_price, lot_size, grid_id, original_order_id_for_regen)
        if is_regeneration:
            if original_order_id_for_regen:
                self.regeneration_counts[original_order_id_for_regen] = self.regeneration_counts.get(original_order_id_for_regen, 0) + 1
                internal_order.regeneration_attempts = self.regeneration_counts[original_order_id_for_regen]
            internal_order.last_regeneration_time = time.time()

        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected():
            mt5_mapped_order_type = self.mt5_connector.MT5_ORDER_TYPE_MAP.get(order_type_str.upper())
            if mt5_mapped_order_type is None: logger.error(f"OM: Unknown order type '{order_type_str}' for MT5 map."); return None
            sym_props = self.risk_manager.get_symbol_config(symbol); decimals = sym_props.get('decimals', 5)
            filling_type = mt5.ORDER_FILLING_FOK # Default
            raw_props = sym_props.get('mt5_raw_properties', {})
            if raw_props and hasattr(raw_props, 'get') and raw_props.get('filling_modes'): # Check if mt5_raw_properties and filling_modes exist
                # Example: use first available filling mode. Real logic might be more specific.
                if isinstance(raw_props['filling_modes'], tuple) and len(raw_props['filling_modes']) > 0 :
                    filling_type = raw_props['filling_modes'][0]

            request = {"action": mt5.TRADE_ACTION_PENDING, "symbol": symbol, "volume": float(lot_size),
                       "type": mt5_mapped_order_type, "price": round(float(entry_price), decimals),
                       "sl": round(float(sl_price), decimals) if sl_price !=0 else 0.0,
                       "tp": round(float(tp_price), decimals) if tp_price !=0 else 0.0,
                       "magic": getattr(config, "MT5_MAGIC_NUMBER", 123456),
                       "comment": f"{grid_id or 'GRID'}_{internal_order.order_id[-6:]}",
                       "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_type}
            if order_type_enum in [OrderType.MARKET_BUY, OrderType.MARKET_SELL]:
                request["action"] = mt5.TRADE_ACTION_DEAL; del request["price"]

            trade_result = self.mt5_connector.send_mt5_trade_request(request)
            if trade_result and trade_result.retcode in [mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED]:
                internal_order.broker_order_id = trade_result.order
                internal_order.status = OrderStatus.FILLED if trade_result.retcode == mt5.TRADE_RETCODE_DONE else OrderStatus.PENDING
                if internal_order.status == OrderStatus.FILLED: internal_order.fill_price = trade_result.price; internal_order.fill_time = time.time()
                logger.info(f"Order sent to MT5: {internal_order} (Ticket: {internal_order.broker_order_id})")
            else: logger.error(f"Failed to place on MT5: {symbol}. Result: {trade_result}. Req: {request}"); return None
        else: logger.info(f"Order Placed (Simulated): {internal_order}")
        self._add_order_to_collections(internal_order); return internal_order

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id);
        if not order: logger.warning(f"Cancel: Order {order_id} not found."); return False
        if order.status != OrderStatus.PENDING: logger.warning(f"Cannot cancel {order_id}: Status {order.status.value}."); return False
        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected() and order.broker_order_id:
            request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.broker_order_id, "comment": f"Cancel {order.order_id[-6:]}"}
            trade_result = self.mt5_connector.send_mt5_trade_request(request)
            if not (trade_result and trade_result.retcode == mt5.TRADE_RETCODE_DONE):
                logger.error(f"Failed to cancel on MT5 {order.broker_order_id}. Result: {trade_result.comment if trade_result else 'None'}."); return False
            logger.info(f"Order {order.broker_order_id} cancelled on MT5.")
        elif self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and not order.broker_order_id:
             logger.warning(f"Order {order_id} no broker_id, cannot cancel via MT5. Simulating.")
        old_status = order.status; order.status = OrderStatus.CANCELLED; order.close_time = time.time()
        self._remove_order_from_collections(order_id, old_status); logger.info(f"Order Cancelled (Internal): {order}"); return True

    def modify_order_sl_tp(self, order_id: str, new_sl: Optional[float] = None, new_tp: Optional[float] = None) -> bool:
        order = self.orders.get(order_id)
        if not order: logger.warning(f"Modify: Order {order_id} not found."); return False
        if order.status not in [OrderStatus.PENDING, OrderStatus.ACTIVE, OrderStatus.FILLED]: logger.warning(f"Cannot modify {order_id}: Status {order.status.value}."); return False
        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected() and order.broker_order_id:
            sym_props = self.risk_manager.get_symbol_config(order.symbol); decimals = sym_props.get('decimals', 5)
            request = {"symbol": order.symbol, "order": order.broker_order_id,
                       "action": mt5.TRADE_ACTION_SLTP if order.status in [OrderStatus.ACTIVE, OrderStatus.FILLED] else mt5.TRADE_ACTION_MODIFY,
                       "sl": round(new_sl if new_sl is not None else order.sl_price, decimals) if (new_sl is not None or order.sl_price !=0) else 0.0,
                       "tp": round(new_tp if new_tp is not None else order.tp_price, decimals) if (new_tp is not None or order.tp_price !=0) else 0.0}
            if request["action"] == mt5.TRADE_ACTION_MODIFY: request["price"] = round(order.entry_price, decimals)
            trade_result = self.mt5_connector.send_mt5_trade_request(request)
            if not (trade_result and trade_result.retcode == mt5.TRADE_RETCODE_DONE):
                logger.error(f"Failed to modify SL/TP on MT5 for {order.broker_order_id}. Result: {trade_result.comment if trade_result else 'None'}."); return False
            logger.info(f"Order {order.broker_order_id} SL/TP modified on MT5.")
        elif self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and not order.broker_order_id:
            logger.warning(f"Order {order_id} no broker_id, cannot modify via MT5. Simulating.")
        modified_internal = False
        if new_sl is not None: order.sl_price = new_sl; modified_internal = True
        if new_tp is not None: order.tp_price = new_tp; modified_internal = True
        if modified_internal: logger.info(f"Order {order_id} SL/TP modified (Internal): NewSL={order.sl_price}, NewTP={order.tp_price}")
        return True

    def synchronize_orders_with_mt5(self):
        if not self.mt5_connector or not hasattr(self.mt5_connector, 'is_mt5_connected') or not self.mt5_connector.is_mt5_connected(): return
        logger.debug("Synchronizing order states with MT5...")
        live_pending_tickets = {o['ticket']: o for o in self.mt5_connector.get_mt5_pending_orders()}
        live_position_tickets = {p['ticket']: p for p in self.mt5_connector.get_mt5_open_positions()}
        all_internal_order_ids = list(self.orders.keys())

        for internal_id in all_internal_order_ids:
            order = self.orders.get(internal_id)
            if not order or not order.broker_order_id: continue
            mt5_ticket = order.broker_order_id
            if order.status == OrderStatus.PENDING:
                if mt5_ticket not in live_pending_tickets:
                    if mt5_ticket in live_position_tickets:
                        mt5_pos = live_position_tickets[mt5_ticket]
                        order.status = OrderStatus.FILLED; order.fill_price = mt5_pos.get('price_open', order.entry_price)
                        fill_time_epoch = mt5_pos.get('time', time.time())
                        if isinstance(fill_time_epoch, (int, float)): order.fill_time = datetime.fromtimestamp(fill_time_epoch, timezone.utc).timestamp()
                        self._remove_order_from_collections(internal_id, OrderStatus.PENDING); self._add_order_to_collections(order)
                        logger.info(f"MT5 Sync: Pending {internal_id} (Ticket:{mt5_ticket}) filled. New status: {order.status}. Fill: {order.fill_price}")
                    else:
                        order.status = OrderStatus.CANCELLED; order.close_time = time.time()
                        self._remove_order_from_collections(internal_id, OrderStatus.PENDING)
                        logger.info(f"MT5 Sync: Pending {internal_id} (Ticket:{mt5_ticket}) not found. Assumed Cancelled/Rejected.")
            elif order.status in [OrderStatus.ACTIVE, OrderStatus.FILLED]:
                if mt5_ticket not in live_position_tickets:
                    order.close_time = time.time(); deals = self.mt5_connector.get_mt5_order_history_by_ticket(mt5_ticket)
                    final_status = OrderStatus.CLOSED; close_price_from_deal = order.close_price
                    for deal in deals:
                        if deal.get('entry') == mt5.DEAL_ENTRY_OUT:
                            close_price_from_deal = deal.get('price'); reason_code = deal.get('reason')
                            if reason_code == mt5.DEAL_REASON_SL: final_status = OrderStatus.STOPPED_OUT
                            elif reason_code == mt5.DEAL_REASON_TP: final_status = OrderStatus.TP_HIT
                            elif reason_code == mt5.DEAL_REASON_CLIENT: final_status = OrderStatus.CLOSED
                            logger.info(f"MT5 Sync: Deal for closed pos {internal_id} (Ticket:{mt5_ticket}). Reason: {reason_code}, Price: {close_price_from_deal}"); break
                    order.status = final_status
                    if close_price_from_deal is not None: order.close_price = close_price_from_deal
                    self._remove_order_from_collections(internal_id, OrderStatus.FILLED)
                    logger.info(f"MT5 Sync: Active pos {internal_id} (Ticket:{mt5_ticket}) closed. New status: {order.status}. Close: {order.close_price}")

        current_internal_broker_ids = {o.broker_order_id: o for o in self.orders.values() if o.broker_order_id}
        for live_ticket, live_pos_data in live_position_tickets.items():
            if live_ticket in current_internal_broker_ids:
                internal_order_obj = current_internal_broker_ids[live_ticket]
                if internal_order_obj.status == OrderStatus.PENDING:
                    logger.info(f"MT5 Sync: PENDING order {internal_order_obj.order_id} (Ticket:{live_ticket}) is ACTIVE on MT5.")
                    internal_order_obj.status = OrderStatus.FILLED
                    internal_order_obj.fill_price = live_pos_data.get('price_open', internal_order_obj.entry_price)
                    fill_time_epoch = live_pos_data.get('time', time.time())
                    if isinstance(fill_time_epoch, (int, float)): internal_order_obj.fill_time = datetime.fromtimestamp(fill_time_epoch, timezone.utc).timestamp()
                    self._remove_order_from_collections(internal_order_obj.order_id, OrderStatus.PENDING); self._add_order_to_collections(internal_order_obj)

    def check_pending_orders(self, current_market_data: Dict[str, Dict[str, float]]):
        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected(): return []
        logger.debug("Simulating check_pending_orders...")
        filled_order_ids = []
        for order_id in list(self.pending_orders):
            order = self.orders.get(order_id);
            if not order: continue
            symbol_data = current_market_data.get(order.symbol);
            if not symbol_data: continue
            market_high = symbol_data.get('high', order.entry_price); market_low = symbol_data.get('low', order.entry_price); fill_price = None
            if order.order_type == OrderType.BUY_STOP and market_high >= order.entry_price: fill_price = max(order.entry_price, market_low if market_high > order.entry_price else order.entry_price)
            elif order.order_type == OrderType.SELL_STOP and market_low <= order.entry_price: fill_price = min(order.entry_price, market_high if market_low < order.entry_price else order.entry_price)
            elif order.order_type == OrderType.BUY_LIMIT and market_low <= order.entry_price: fill_price = min(order.entry_price, market_high if market_low < order.entry_price else order.entry_price)
            elif order.order_type == OrderType.SELL_LIMIT and market_high >= order.entry_price: fill_price = max(order.entry_price, market_low if market_high > order.entry_price else order.entry_price)
            if fill_price is not None:
                old_status = order.status; order.status = OrderStatus.FILLED; order.fill_time = time.time(); order.fill_price = fill_price
                self._remove_order_from_collections(order_id, old_status); self._add_order_to_collections(order)
                filled_order_ids.append(order_id); logger.info(f"Order Filled (Simulated): {order} at {fill_price}")
        return filled_order_ids

    def check_active_positions(self, current_market_data: Dict[str, Dict[str, float]]):
        if self.mt5_connector and hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected(): return []
        logger.debug("Simulating check_active_positions...")
        closed_order_ids = []
        for order_id in list(self.active_positions):
            order = self.orders.get(order_id)
            if not order or order.fill_price is None: continue
            symbol_data = current_market_data.get(order.symbol);
            if not symbol_data: continue
            market_high = symbol_data.get('high', order.fill_price if order.fill_price else order.entry_price)
            market_low = symbol_data.get('low', order.fill_price if order.fill_price else order.entry_price)
            closed_by = None; close_price = None
            if order.order_type in [OrderType.BUY_LIMIT, OrderType.BUY_STOP, OrderType.MARKET_BUY]:
                if order.sl_price != 0 and market_low <= order.sl_price: closed_by = OrderStatus.STOPPED_OUT; close_price = order.sl_price
                elif order.tp_price != 0 and market_high >= order.tp_price: closed_by = OrderStatus.TP_HIT; close_price = order.tp_price
            elif order.order_type in [OrderType.SELL_LIMIT, OrderType.SELL_STOP, OrderType.MARKET_SELL]:
                if order.sl_price != 0 and market_high >= order.sl_price: closed_by = OrderStatus.STOPPED_OUT; close_price = order.sl_price
                elif order.tp_price != 0 and market_low <= order.tp_price: closed_by = OrderStatus.TP_HIT; close_price = order.tp_price
            if closed_by:
                old_status = order.status; order.status = closed_by; order.close_time = time.time(); order.close_price = close_price
                self._remove_order_from_collections(order_id, old_status); closed_order_ids.append(order_id)
                logger.info(f"Position Closed (Simulated): {order} by {closed_by.value} at {close_price}")
        return closed_order_ids

    def needs_regeneration(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order or order.status != OrderStatus.STOPPED_OUT: return False
        original_id = order.original_order_id
        attempts = self.regeneration_counts.get(original_id, 0)
        if attempts >= self.max_regeneration_attempts: logger.info(f"Order slot {original_id} (last: {order_id}) max regen attempts ({attempts})."); return False
        return True

    def get_order_details_for_regeneration(self, stopped_order_id: str, widen_sltp_factor: Optional[float] = None) -> Optional[Dict[str, Any]]:
        order = self.orders.get(stopped_order_id)
        if not order or order.status != OrderStatus.STOPPED_OUT: return None
        new_sl, new_tp = order.initial_sl_price, order.initial_tp_price
        if widen_sltp_factor and widen_sltp_factor > 1.0:
            sl_dist = abs(order.entry_price - order.initial_sl_price); tp_dist = abs(order.initial_tp_price - order.entry_price)
            try: decimals = self.risk_manager.get_symbol_config(order.symbol).get('decimals', 2)
            except: decimals = 5
            if order.order_type in [OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
                new_sl = round(order.entry_price - (sl_dist * widen_sltp_factor), decimals)
                if order.initial_tp_price != 0: new_tp = round(order.entry_price + (tp_dist * widen_sltp_factor), decimals)
                else: new_tp = 0.0
            else:
                new_sl = round(order.entry_price + (sl_dist * widen_sltp_factor), decimals)
                if order.initial_tp_price != 0: new_tp = round(order.entry_price - (tp_dist * widen_sltp_factor), decimals)
                else: new_tp = 0.0
            logger.info(f"Widening SL/TP for regen of {order.original_order_id}. Factor: {widen_sltp_factor}. SL:{order.sl_price}->{new_sl}, TP:{order.tp_price}->{new_tp}")
        return {"symbol": order.symbol, "order_type_str": order.order_type.value, "entry_price": order.entry_price, "sl_price": new_sl, "tp_price": new_tp, "lot_size": order.lot_size, "grid_id": order.grid_id, "original_order_id_for_regen": order.original_order_id, "is_regeneration": True}

    def get_all_orders(self) -> List[Order]: return list(self.orders.values())
    def get_pending_orders(self) -> List[Order]: return [self.orders[oid] for oid in self.pending_orders if oid in self.orders]
    def get_active_positions(self) -> List[Order]: return [self.orders[oid] for oid in self.active_positions if oid in self.orders]

if __name__ == '__main__':
    class MockRiskManagerOMOM:
        def get_symbol_config(self,symbol): return {"decimals":5, "point_value":0.00001} if "EURUSD" in symbol else {"decimals":3, "point_value":0.001}
    class MockMT5ConnectorOMTest:
        def __init__(self): self._is_connected = False; self.MT5_ORDER_TYPE_MAP = {}
        def is_mt5_connected(self): return self._is_connected
        def connect_to_mt5(self): self._is_connected = True;
            # Simulate map load from actual mt5_connector's global map
            if hasattr(package_config, '_MT5_ORDER_TYPE_MAP_REAL_TEMP'): # If real map was stored
                self.MT5_ORDER_TYPE_MAP = package_config._MT5_ORDER_TYPE_MAP_REAL_TEMP
            elif hasattr(mt5, 'ORDER_TYPE_BUY_STOP'): # Try to build from dummy mt5
                 self.MT5_ORDER_TYPE_MAP = { "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP, "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
                                             "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT, "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP}
            else: # Fallback if no constants are available
                 self.MT5_ORDER_TYPE_MAP = { "BUY_STOP": 4, "SELL_LIMIT": 3, "BUY_LIMIT": 2, "SELL_STOP": 5 }

            return True
        def send_mt5_trade_request(self, req):
            logger.info(f"MockMT5ConnectorOMTest: Received request: {req}")
            class MockTradeResult:
                def __init__(self, retcode, order_id, price=0.0, volume=0.0): self.retcode = retcode; self.order = order_id; self.comment = "MockSuccess"; self.price = price; self.volume = volume
            action = req.get("action")
            retcode = mt5.TRADE_RETCODE_PLACED if action == mt5.TRADE_ACTION_PENDING else mt5.TRADE_RETCODE_DONE
            return MockTradeResult(retcode=retcode, order_id=int(time.time()*1000)%100000 + 100000)
        def get_mt5_symbol_properties(self, symbol): return None
        def get_mt5_pending_orders(self): return [] # For sync test
        def get_mt5_open_positions(self): return [] # For sync test
        def get_mt5_order_history_by_ticket(self, ticket): return [] # For sync test


    class MainConfigOMTest:
        DEFAULT_MAX_REGENERATION_ATTEMPTS = 2; DEFAULT_COOLDOWN_PERIOD_BARS = 1
        BAR_DURATION_SECONDS = 0.0001; DEFAULT_SL_TP_WIDENING_FACTOR = 1.2
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_order_manager_main.log"; MT5_MAGIC_NUMBER = 77777
        def get(self, key, default): return getattr(self, key, default)

    import sys
    mock_config_om_main = MainConfigOMTest()
    sys.modules['grid_trader.config'] = mock_config_om_main
    config = mock_config_om_main

    import grid_trader.utils.logger as util_logger
    util_logger.config = mock_config_om_main
    logger = get_logger(__name__)

    mock_rm_om = MockRiskManagerOMOM()
    mock_mt5_conn = MockMT5ConnectorOMTest()

    logger.info("--- Testing OrderManager in Simulation Mode (mt5_connector=None) ---")
    om_sim = OrderManager(risk_manager=mock_rm_om, mt5_connector=None)
    sim_order = om_sim.place_new_order("EURUSD", "BUY_STOP", 1.1, 1.095, 1.105, 0.01)
    assert sim_order is not None and sim_order.broker_order_id is None, "Sim order failed or has broker ID"
    logger.info(f"Simulated order: {sim_order}")

    logger.info("--- Testing OrderManager with Mocked MT5 Connector ---")
    mock_mt5_conn.connect_to_mt5() # "Connect" the mock and load its map
    om_mt5 = OrderManager(risk_manager=mock_rm_om, mt5_connector=mock_mt5_conn)
    mt5_order = om_mt5.place_new_order("EURUSD", "BUY_STOP", 1.12, 1.115, 1.125, 0.02)
    assert mt5_order is not None and mt5_order.broker_order_id is not None, "MT5 order placement failed or no broker ID"
    logger.info(f"MT5 mocked order: {mt5_order}")

    if mt5_order:
        cancel_success = om_mt5.cancel_order(mt5_order.order_id)
        assert cancel_success, "MT5 order cancel failed"
        assert mt5_order.status == OrderStatus.CANCELLED
        logger.info(f"MT5 mocked order cancelled: {mt5_order}")

    logger.info("--- Testing OM Sync (minimal, ensure no errors) ---")
    om_mt5.synchronize_orders_with_mt5() # Just call to ensure no errors with mock
    logger.info("OM Sync call completed.")

    logger.info("OrderManager __main__ tests complete.")
