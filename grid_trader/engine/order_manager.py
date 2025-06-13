# grid_trader/engine/order_manager.py
import uuid
import time
from typing import List, Dict, Any, Optional
from enum import Enum

from .. import config
from ..utils.logger import get_logger

logger = get_logger(__name__)

class OrderStatus(Enum):
    PENDING = "PENDING"        # Order placed but not yet active (e.g., STOP or LIMIT not hit)
    ACTIVE = "ACTIVE"          # Order filled, position is open
    CANCELLED = "CANCELLED"    # Order cancelled before activation
    FILLED = "FILLED"          # Alias for ACTIVE, position opened.
    STOPPED_OUT = "STOPPED_OUT"  # Position closed by Stop Loss
    TP_HIT = "TP_HIT"          # Position closed by Take Profit
    CLOSED = "CLOSED"          # Position closed manually or by other means (generic)

class OrderType(Enum):
    # Basic market orders (not used by grid generator directly but good to have)
    MARKET_BUY = "MARKET_BUY"
    MARKET_SELL = "MARKET_SELL"
    # Pending Orders
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"

class Order:
    """ Represents a single trading order and its state. """
    def __init__(self, symbol: str, order_type: OrderType, entry_price: float,
                 sl_price: float, tp_price: float, lot_size: float,
                 grid_id: Optional[str] = None, original_order_id: Optional[str] = None):
        self.order_id: str = str(uuid.uuid4())
        self.symbol: str = symbol
        self.order_type: OrderType = order_type
        self.entry_price: float = entry_price
        self.sl_price: float = sl_price
        self.tp_price: float = tp_price
        self.lot_size: float = lot_size
        self.status: OrderStatus = OrderStatus.PENDING

        self.grid_id: Optional[str] = grid_id
        self.original_order_id: Optional[str] = original_order_id if original_order_id else self.order_id

        self.creation_time: float = time.time()
        self.fill_time: Optional[float] = None
        self.close_time: Optional[float] = None
        self.fill_price: Optional[float] = None
        self.close_price: Optional[float] = None
        self.pnl: Optional[float] = None

        self.regeneration_attempts: int = 0
        self.last_regeneration_time: Optional[float] = None

        self.initial_sl_price = sl_price
        self.initial_tp_price = tp_price

    def __repr__(self):
        return (f"Order({self.order_id[-6:]} | {self.symbol} {self.order_type.value} {self.lot_size} lots @ {self.entry_price} "
                f"SL:{self.sl_price} TP:{self.tp_price} | Status: {self.status.value} | GridID: {self.grid_id} | Regen: {self.regeneration_attempts})")


class OrderManager:
    """
    Manages the state of all orders, simulates broker interactions,
    and handles order regeneration logic.
    """
    def __init__(self, risk_manager: Any):
        self.risk_manager = risk_manager
        self.orders: Dict[str, Order] = {}
        self.pending_orders: List[str] = []
        self.active_positions: List[str] = []

        self.regeneration_counts: Dict[str, int] = {}
        self.max_regeneration_attempts = config.DEFAULT_MAX_REGENERATION_ATTEMPTS
        # DEFAULT_COOLDOWN_PERIOD_BARS is in bars, convert to seconds. Assume 1 bar = 1 minute for default.
        # This should ideally be configurable per symbol/timeframe.
        bar_duration_seconds = getattr(config, 'BAR_DURATION_SECONDS', 60)
        self.cooldown_period_seconds = config.DEFAULT_COOLDOWN_PERIOD_BARS * bar_duration_seconds

        logger.info(f"OrderManager initialized. Max Regen Attempts: {self.max_regeneration_attempts}, Cooldown: {self.cooldown_period_seconds}s")

    def _add_order_to_collections(self, order: Order):
        self.orders[order.order_id] = order
        if order.status == OrderStatus.PENDING:
            if order.order_id not in self.pending_orders:
                self.pending_orders.append(order.order_id)
        elif order.status in [OrderStatus.ACTIVE, OrderStatus.FILLED]:
            if order.order_id not in self.active_positions:
                self.active_positions.append(order.order_id)
            if order.order_id in self.pending_orders:
                self.pending_orders.remove(order.order_id)

    def _remove_order_from_collections(self, order_id: str, old_status: OrderStatus):
        if old_status == OrderStatus.PENDING:
            if order_id in self.pending_orders:
                self.pending_orders.remove(order_id)
        elif old_status in [OrderStatus.ACTIVE, OrderStatus.FILLED]:
            if order_id in self.active_positions:
                self.active_positions.remove(order_id)

    def place_new_order(self, symbol: str, order_type_str: str, entry_price: float,
                        sl_price: float, tp_price: float, lot_size: float,
                        grid_id: Optional[str] = None,
                        original_order_id_for_regen: Optional[str] = None,
                        is_regeneration: bool = False) -> Optional[Order]:
        try:
            order_type_enum = OrderType[order_type_str.upper()]
        except KeyError:
            logger.error(f"Invalid order type string: {order_type_str}")
            return None

        if lot_size <= 0:
            logger.warning(f"Attempted to place order with invalid lot size: {lot_size} for {symbol}. Order rejected.")
            return None

        if order_type_enum in [OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
            if entry_price <= sl_price:
                logger.warning(f"Buy order for {symbol} has SL {sl_price} not below entry {entry_price}. Order rejected.")
                return None
            if entry_price >= tp_price and tp_price != 0: # tp_price = 0 might mean no TP
                logger.warning(f"Buy order for {symbol} has TP {tp_price} not above entry {entry_price}. Order rejected.")
                return None
        elif order_type_enum in [OrderType.SELL_LIMIT, OrderType.SELL_STOP]:
            if entry_price >= sl_price:
                logger.warning(f"Sell order for {symbol} has SL {sl_price} not above entry {entry_price}. Order rejected.")
                return None
            if entry_price <= tp_price and tp_price != 0:
                logger.warning(f"Sell order for {symbol} has TP {tp_price} not below entry {entry_price}. Order rejected.")
                return None

        order = Order(symbol, order_type_enum, entry_price, sl_price, tp_price, lot_size, grid_id, original_order_id_for_regen)

        if is_regeneration:
            if original_order_id_for_regen:
                self.regeneration_counts[original_order_id_for_regen] = self.regeneration_counts.get(original_order_id_for_regen, 0) + 1
                order.regeneration_attempts = self.regeneration_counts[original_order_id_for_regen]
            else:
                logger.warning(f"Regenerated order {order.order_id} is missing original_order_id_for_regen link.")
            order.last_regeneration_time = time.time()

        self._add_order_to_collections(order)
        logger.info(f"Order Placed (Simulated): {order}")
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order:
            logger.warning(f"Attempted to cancel non-existent order ID: {order_id}")
            return False
        if order.status == OrderStatus.PENDING:
            old_status = order.status
            order.status = OrderStatus.CANCELLED
            order.close_time = time.time()
            self._remove_order_from_collections(order_id, old_status)
            logger.info(f"Order Cancelled (Simulated): {order}")
            return True
        else:
            logger.warning(f"Cannot cancel order {order_id}: Status is {order.status.value} (not PENDING).")
            return False

    def modify_order_sl_tp(self, order_id: str, new_sl: Optional[float] = None, new_tp: Optional[float] = None) -> bool:
        order = self.orders.get(order_id)
        if not order: return False
        if order.status not in [OrderStatus.PENDING, OrderStatus.ACTIVE, OrderStatus.FILLED]: return False
        modified = False
        if new_sl is not None: order.sl_price = new_sl; modified = True; logger.info(f"Order {order_id} SL modified to {new_sl}")
        if new_tp is not None: order.tp_price = new_tp; modified = True; logger.info(f"Order {order_id} TP modified to {new_tp}")
        return modified

    def check_pending_orders(self, current_market_data: Dict[str, Dict[str, float]]):
        filled_order_ids = []
        for order_id in list(self.pending_orders):
            order = self.orders.get(order_id)
            if not order: continue
            symbol_data = current_market_data.get(order.symbol)
            if not symbol_data: continue

            market_high = symbol_data.get('high', order.entry_price)
            market_low = symbol_data.get('low', order.entry_price)
            fill_price = None
            if order.order_type == OrderType.BUY_STOP and market_high >= order.entry_price:
                fill_price = max(order.entry_price, market_low if market_high > order.entry_price else order.entry_price)
            elif order.order_type == OrderType.SELL_STOP and market_low <= order.entry_price:
                fill_price = min(order.entry_price, market_high if market_low < order.entry_price else order.entry_price)
            elif order.order_type == OrderType.BUY_LIMIT and market_low <= order.entry_price:
                fill_price = min(order.entry_price, market_high if market_low < order.entry_price else order.entry_price)
            elif order.order_type == OrderType.SELL_LIMIT and market_high >= order.entry_price:
                fill_price = max(order.entry_price, market_low if market_high > order.entry_price else order.entry_price)

            if fill_price is not None:
                old_status = order.status
                order.status = OrderStatus.FILLED
                order.fill_time = time.time()
                order.fill_price = fill_price
                self._remove_order_from_collections(order_id, old_status)
                self._add_order_to_collections(order)
                filled_order_ids.append(order_id)
                logger.info(f"Order Filled (Simulated): {order} at {fill_price}")
        return filled_order_ids

    def check_active_positions(self, current_market_data: Dict[str, Dict[str, float]]):
        closed_order_ids = []
        for order_id in list(self.active_positions):
            order = self.orders.get(order_id)
            if not order or order.fill_price is None: continue # Ensure it was filled
            symbol_data = current_market_data.get(order.symbol)
            if not symbol_data: continue

            market_high = symbol_data.get('high', order.fill_price)
            market_low = symbol_data.get('low', order.fill_price)
            closed_by = None; close_price = None

            if order.order_type in [OrderType.BUY_LIMIT, OrderType.BUY_STOP, OrderType.MARKET_BUY]:
                if order.sl_price != 0 and market_low <= order.sl_price: closed_by = OrderStatus.STOPPED_OUT; close_price = order.sl_price
                elif order.tp_price != 0 and market_high >= order.tp_price: closed_by = OrderStatus.TP_HIT; close_price = order.tp_price
            elif order.order_type in [OrderType.SELL_LIMIT, OrderType.SELL_STOP, OrderType.MARKET_SELL]:
                if order.sl_price != 0 and market_high >= order.sl_price: closed_by = OrderStatus.STOPPED_OUT; close_price = order.sl_price
                elif order.tp_price != 0 and market_low <= order.tp_price: closed_by = OrderStatus.TP_HIT; close_price = order.tp_price

            if closed_by:
                old_status = order.status
                order.status = closed_by; order.close_time = time.time(); order.close_price = close_price
                self._remove_order_from_collections(order_id, old_status)
                closed_order_ids.append(order_id)
                logger.info(f"Position Closed (Simulated): {order} by {closed_by.value} at {close_price}")
        return closed_order_ids

    def needs_regeneration(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order or order.status != OrderStatus.STOPPED_OUT: return False
        original_id = order.original_order_id
        attempts = self.regeneration_counts.get(original_id, 0)
        if attempts >= self.max_regeneration_attempts:
            logger.info(f"Order slot {original_id} (last attempt {order_id}) reached max regen attempts ({attempts}).")
            return False
        return True

    def get_order_details_for_regeneration(self, stopped_order_id: str, widen_sltp_factor: Optional[float] = None) -> Optional[Dict[str, Any]]:
        order = self.orders.get(stopped_order_id)
        if not order or order.status != OrderStatus.STOPPED_OUT: return None
        new_sl, new_tp = order.initial_sl_price, order.initial_tp_price
        if widen_sltp_factor and widen_sltp_factor > 1.0:
            sl_dist = abs(order.entry_price - order.initial_sl_price)
            tp_dist = abs(order.initial_tp_price - order.entry_price)
            try:
                decimals = str(order.entry_price)[::-1].find('.')
                if decimals == -1: decimals = self.risk_manager.get_symbol_config(order.symbol).get('decimals', 2)
            except: decimals = 2 # Fallback

            if order.order_type in [OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
                new_sl = round(order.entry_price - (sl_dist * widen_sltp_factor), decimals)
                if order.initial_tp_price != 0: new_tp = round(order.entry_price + (tp_dist * widen_sltp_factor), decimals)
                else: new_tp = 0 # No TP
            else:
                new_sl = round(order.entry_price + (sl_dist * widen_sltp_factor), decimals)
                if order.initial_tp_price != 0: new_tp = round(order.entry_price - (tp_dist * widen_sltp_factor), decimals)
                else: new_tp = 0 # No TP
            logger.info(f"Widening SL/TP for regen of {order.original_order_id}. Factor: {widen_sltp_factor}. SL: {order.sl_price}->{new_sl}, TP: {order.tp_price}->{new_tp}")
        return {"symbol": order.symbol, "order_type_str": order.order_type.value, "entry_price": order.entry_price,
                "sl_price": new_sl, "tp_price": new_tp, "lot_size": order.lot_size, "grid_id": order.grid_id,
                "original_order_id_for_regen": order.original_order_id, "is_regeneration": True}

    def get_all_orders(self) -> List[Order]: return list(self.orders.values())
    def get_pending_orders(self) -> List[Order]: return [self.orders[oid] for oid in self.pending_orders if oid in self.orders]
    def get_active_positions(self) -> List[Order]: return [self.orders[oid] for oid in self.active_positions if oid in self.orders]

if __name__ == '__main__':
    class MockRiskManagerOM:
        def calculate_lot_size(self, *args, **kwargs): return 0.01
        def get_symbol_config(self,symbol):
             return {"decimals":5, "point_value":0.00001} if "EURUSD" in symbol else {"decimals":3, "point_value":0.001}

    class MainConfigOM:
        DEFAULT_MAX_REGENERATION_ATTEMPTS = 2
        DEFAULT_COOLDOWN_PERIOD_BARS = 1
        BAR_DURATION_SECONDS = 60 # For cooldown calculation
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_order_manager.log"

    import sys
    sys.modules['grid_trader.config'] = MainConfigOM # For any *new* imports

    # Rebind this module's 'config' global to use MainConfigOM directly
    config = MainConfigOM

    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigOM # Ensure logger used by get_logger also uses this test config
    logger = get_logger(__name__) # Re-initialize this module's logger with the new config

    om = OrderManager(risk_manager=MockRiskManagerOM())

    logger.info("--- Testing Order Placement ---")
    o1_params = {"symbol":"EURUSD", "order_type_str":"BUY_STOP", "entry_price":1.1000, "sl_price":1.0950, "tp_price":1.1100, "lot_size":0.01, "grid_id":"TestGrid1"}
    o1 = om.place_new_order(**o1_params)
    assert o1 is not None and o1.status == OrderStatus.PENDING, f"o1 status: {o1.status if o1 else 'None'}"
    assert len(om.pending_orders) == 1

    o2_params = {"symbol":"USDJPY", "order_type_str":"SELL_LIMIT", "entry_price":130.00, "sl_price":130.50, "tp_price":129.00, "lot_size":0.02}
    o2 = om.place_new_order(**o2_params)
    assert o2 is not None and o2.status == OrderStatus.PENDING, f"o2 status: {o2.status if o2 else 'None'}"
    assert len(om.pending_orders) == 2

    logger.info("--- Testing Order Cancellation ---")
    om.cancel_order(o1.order_id)
    assert o1.status == OrderStatus.CANCELLED, f"o1 status after cancel: {o1.status}"
    assert len(om.pending_orders) == 1

    logger.info("--- Testing Order Fill (Simulated) ---")
    market_now = {"USDJPY": {"high": 130.050, "low": 129.950, "close": 130.020}}
    om.check_pending_orders(market_now)
    assert o2.status == OrderStatus.FILLED, f"o2 status after fill: {o2.status}"
    assert o2.fill_price is not None
    assert len(om.pending_orders) == 0
    assert len(om.active_positions) == 1

    logger.info("--- Testing SL/TP Hit (Simulated) ---")
    market_tp_hit = {"USDJPY": {"high": 129.100, "low": 128.950, "close": 129.050}}
    om.check_active_positions(market_tp_hit)
    assert o2.status == OrderStatus.TP_HIT, f"o2 status after TP: {o2.status}"
    assert o2.close_price == 129.00
    assert len(om.active_positions) == 0

    logger.info("--- Testing Regeneration Logic ---")
    o3_params = {"symbol":"EURUSD", "order_type_str":"BUY_LIMIT", "entry_price":1.0900, "sl_price":1.0850, "tp_price":1.1000, "lot_size":0.03, "grid_id":"RegenGrid"}
    o3 = om.place_new_order(**o3_params)

    om.check_pending_orders({"EURUSD": {"high": 1.0900, "low": 1.0895}})
    assert o3.status == OrderStatus.FILLED, f"o3 status: {o3.status}"
    om.check_active_positions({"EURUSD": {"high": 1.0860, "low": 1.0845}})
    assert o3.status == OrderStatus.STOPPED_OUT, f"o3 status: {o3.status}"

    can_regen_o3 = om.needs_regeneration(o3.order_id)
    logger.info(f"Order o3 ({o3.order_id[-6:]}) needs regen: {can_regen_o3} (Attempts for slot {o3.original_order_id[-6:]}: {om.regeneration_counts.get(o3.original_order_id,0)})")
    assert can_regen_o3

    regen_params_o3 = om.get_order_details_for_regeneration(o3.order_id, widen_sltp_factor=1.2)
    assert regen_params_o3 is not None
    logger.info(f"Regeneration params for o3: SL {regen_params_o3['sl_price']}, TP {regen_params_o3['tp_price']}")

    o3_regen1 = om.place_new_order(**regen_params_o3)
    assert o3_regen1 is not None; assert o3_regen1.original_order_id == o3.original_order_id
    assert o3_regen1.regeneration_attempts == 1
    logger.info(f"Regenerated order 1: {o3_regen1}")

    om.check_pending_orders({"EURUSD": {"high": o3_regen1.entry_price, "low": o3_regen1.entry_price - 0.0001}})
    om.check_active_positions({"EURUSD": {"high": o3_regen1.entry_price + 0.0001, "low": o3_regen1.sl_price - 0.0001 }})
    assert o3_regen1.status == OrderStatus.STOPPED_OUT, f"o3_regen1 status: {o3_regen1.status}"

    can_regen_o3_again = om.needs_regeneration(o3_regen1.order_id)
    logger.info(f"Order o3_regen1 ({o3_regen1.order_id[-6:]}) needs regen: {can_regen_o3_again} (Attempts for slot {o3_regen1.original_order_id[-6:]}: {om.regeneration_counts.get(o3_regen1.original_order_id,0)})")
    assert can_regen_o3_again

    regen_params_o3_2 = om.get_order_details_for_regeneration(o3_regen1.order_id, widen_sltp_factor=1.5)
    o3_regen2 = om.place_new_order(**regen_params_o3_2)
    assert o3_regen2 is not None; assert o3_regen2.regeneration_attempts == 2
    logger.info(f"Regenerated order 2: {o3_regen2}")

    om.check_pending_orders({"EURUSD": {"high": o3_regen2.entry_price, "low": o3_regen2.entry_price - 0.0001}})
    om.check_active_positions({"EURUSD": {"high": o3_regen2.entry_price + 0.0001, "low": o3_regen2.sl_price - 0.0001 }})
    assert o3_regen2.status == OrderStatus.STOPPED_OUT, f"o3_regen2 status: {o3_regen2.status}"

    cannot_regen_o3_now = om.needs_regeneration(o3_regen2.order_id)
    logger.info(f"Order o3_regen2 ({o3_regen2.order_id[-6:]}) needs regen: {cannot_regen_o3_now} (Attempts for slot {o3_regen2.original_order_id[-6:]}: {om.regeneration_counts.get(o3_regen2.original_order_id,0)})")
    assert not cannot_regen_o3_now

    logger.info("Order Manager Tests Complete.")
