# grid_trader/models/static_grid.py
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .base_model import BaseGridModel
from ..utils.logger import get_logger
from .. import config

logger = get_logger(__name__)

class StaticGridModel(BaseGridModel):
    """
    Static Grid Model: Generates orders evenly spaced from base_price towards base_sl.
    - If base direction is 'buy', it generates Buy Limit orders.
    - If base direction is 'sell', it generates Sell Limit orders.
    All orders in the grid share the same stop-loss level, which is base_sl.
    """

    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any,
                 num_grid_lines: int = 5,
                 use_base_tp_for_all: bool = True,
                 individual_tp_rr_ratio: float = 1.0): # Risk:Reward ratio for individual TPs
        """
        Args:
            num_grid_lines (int): Number of pending orders to generate between base_price and base_sl.
            use_base_tp_for_all (bool): If True, all grid orders use base_trade_params['base_tp'].
                                       If False, TP is calculated using individual_tp_rr_ratio.
            individual_tp_rr_ratio (float): Risk:Reward ratio if not using base_tp.
                                            TP_distance = SL_distance * ratio.
        """
        super().__init__(base_trade_params, historical_data, risk_manager)
        self.num_grid_lines = int(max(1, num_grid_lines))
        self.use_base_tp_for_all = use_base_tp_for_all
        self.individual_tp_rr_ratio = float(max(0.1, individual_tp_rr_ratio))

        if (self.direction.lower() == 'buy' and self.base_price <= self.base_sl) or \
           (self.direction.lower() == 'sell' and self.base_price >= self.base_sl):
            msg = f"StaticGrid: Base price ({self.base_price}) is not appropriately positioned relative to Base SL ({self.base_sl}) for direction '{self.direction}'."
            logger.error(msg)
            raise ValueError(msg)

        logger.info(f"StaticGridModel initialized for {self.symbol} {self.direction}: NumLines={self.num_grid_lines}, "
                    f"BasePrice={self.base_price}, BaseSL={self.base_sl}, BaseTP={self.base_tp}, "
                    f"UseBaseTP={self.use_base_tp_for_all}, IndivTP_RR={self.individual_tp_rr_ratio if not use_base_tp_for_all else 'N/A'}")

    def _get_decimals(self) -> int:
        decimals = 4
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
            logger.warning(f"Could not determine decimals for {self.symbol} accurately for StaticGrid, defaulting to {decimals}. Error: {e}", exc_info=True)
        return decimals

    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        self.grid_orders.clear()
        decimals = self._get_decimals()

        total_range = abs(self.base_price - self.base_sl)
        if total_range <= 0:
            logger.warning(f"StaticGrid: Total range between base_price and base_sl is zero for {self.symbol}. No orders generated.")
            return []

        spacing = total_range / (self.num_grid_lines + 1)
        min_pip_spacing = 1 / (10**decimals)
        if spacing < min_pip_spacing / 2: # If spacing is less than half a pip, it's too small
             logger.warning(f"StaticGrid: Calculated spacing ({spacing:.{decimals+2}f}) is too small for {self.symbol} with {decimals} decimals. Min practical spacing: {min_pip_spacing:.{decimals}f}. No orders generated.")
             return []

        for i in range(1, self.num_grid_lines + 1):
            price_offset = i * spacing
            order_sl = self.base_sl

            if self.direction.lower() == 'buy':
                entry_price = round(self.base_price - price_offset, decimals)
                if entry_price <= self.base_sl:
                    logger.debug(f"StaticGrid: Skipping Buy Limit for {self.symbol} as entry {entry_price} is at or below SL {self.base_sl}")
                    continue
                if entry_price >= self.base_price:
                    logger.debug(f"StaticGrid: Skipping Buy Limit for {self.symbol} as entry {entry_price} is at or above base_price {self.base_price}")
                    continue

                order_type = 'BUY_LIMIT'
                sl_distance = abs(entry_price - order_sl)
                if self.use_base_tp_for_all:
                    order_tp = self.base_tp
                else:
                    order_tp = round(entry_price + (sl_distance * self.individual_tp_rr_ratio), decimals)

                if order_tp <= entry_price:
                    logger.warning(f"StaticGrid: Buy Limit for {self.symbol} at {entry_price} has invalid TP {order_tp} (not above entry). Skipping.")
                    continue

            elif self.direction.lower() == 'sell':
                entry_price = round(self.base_price + price_offset, decimals)
                if entry_price >= self.base_sl:
                    logger.debug(f"StaticGrid: Skipping Sell Limit for {self.symbol} as entry {entry_price} is at or above SL {self.base_sl}")
                    continue
                if entry_price <= self.base_price:
                    logger.debug(f"StaticGrid: Skipping Sell Limit for {self.symbol} as entry {entry_price} is at or below base_price {self.base_price}")
                    continue

                order_type = 'SELL_LIMIT'
                sl_distance = abs(entry_price - order_sl)
                if self.use_base_tp_for_all:
                    order_tp = self.base_tp
                else:
                    order_tp = round(entry_price - (sl_distance * self.individual_tp_rr_ratio), decimals)

                if order_tp >= entry_price:
                    logger.warning(f"StaticGrid: Sell Limit for {self.symbol} at {entry_price} has invalid TP {order_tp} (not below entry). Skipping.")
                    continue
            else: # Should not be reached
                continue

            lot_size = self._calculate_lot_size(entry_price=entry_price, sl_price=order_sl)

            if lot_size > 0:
                self.grid_orders.append({
                    'symbol': self.symbol, 'order_type': order_type,
                    'entry_price': entry_price, 'sl': order_sl, 'tp': order_tp,
                    'lot_size': lot_size, 'grid_id': f"SG_{self.symbol}_{order_type[0]}{order_type[-1]}_{i}"
                })

        logger.info(f"Generated {len(self.grid_orders)} orders for StaticGridModel ({self.symbol}).")
        if self.grid_orders:
             unique_orders = []
             seen_entries = set()
             for order in self.grid_orders:
                 order_key = (order['order_type'], order['entry_price'])
                 if order_key not in seen_entries:
                     unique_orders.append(order)
                     seen_entries.add(order_key)
             if len(unique_orders) != len(self.grid_orders):
                 logger.warning(f"StaticGrid: Removed duplicate orders. Original: {len(self.grid_orders)}, Unique: {len(unique_orders)}")
                 self.grid_orders = unique_orders
        return self.grid_orders

if __name__ == '__main__':
    class MockRiskManagerSG:
        def get_account_balance(self): return 10000
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.0

            cfg = sys.modules.get('grid_trader.config', config)

            symbol_details = cfg.SYMBOL_SETTINGS.get(symbol, {})
            decimals = symbol_details.get('decimals', 4)
            pip_value_per_lot = symbol_details.get('pip_value_per_lot', 10)
            point_value = 10**(-decimals)
            sl_pips = price_diff / point_value
            if sl_pips <= 0: return 0.0

            lots = risk_per_trade_usd / (sl_pips * pip_value_per_lot)
            min_lot = symbol_details.get('min_lot_size', 0.01)
            lot_step = symbol_details.get('lot_step', 0.01)
            lots = max(min_lot, round(lots / lot_step) * lot_step if lot_step > 0 else lots)
            logger.debug(f"MockRM SG: Lots for {symbol} E:{entry_price} SL:{sl_price} Risk:{risk_per_trade_usd} -> {lots} lots (SL pips: {sl_pips})")
            return lots if lots > 0 else 0.0

    class MainConfigStaticGrid:
        DEFAULT_RISK_PER_TRADE_USD = 20.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_static_grid.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0, "decimals": 5},
            "XAUUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 1.0, "decimals": 2}
        }

    import sys
    sys.modules['grid_trader.config'] = MainConfigStaticGrid
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigStaticGrid

    logger = get_logger(__name__)

    mock_rm_sg = MockRiskManagerSG()
    hist_data_sg = pd.DataFrame({'Close': [1.10000], 'High': [1.10200], 'Low': [1.09900]})

    logger.info("--- Testing StaticGridModel EURUSD (BUY direction) ---")
    base_params_sg_buy = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10500,
        'base_sl': 1.10000, 'base_tp': 1.11500,
        'base_size_lots': 0.1, 'atr': 0.00100
    }
    static_grid_buy = StaticGridModel(
        base_params_sg_buy, hist_data_sg, mock_rm_sg,
        num_grid_lines=4, use_base_tp_for_all=False, individual_tp_rr_ratio=1.5
    )
    buy_sg_orders = static_grid_buy.generate_grid_orders()
    for order in buy_sg_orders: logger.info(f"  {order}")

    logger.info("--- Testing StaticGridModel EURUSD (BUY direction, use_base_tp_for_all=True) ---")
    static_grid_buy_base_tp = StaticGridModel(
        base_params_sg_buy, hist_data_sg, mock_rm_sg,
        num_grid_lines=3, use_base_tp_for_all=True
    )
    buy_sg_orders_base_tp = static_grid_buy_base_tp.generate_grid_orders()
    for order in buy_sg_orders_base_tp: logger.info(f"  {order}")

    logger.info("--- Testing StaticGridModel XAUUSD (SELL direction) ---")
    base_params_sg_sell_xau = {
        'symbol': 'XAUUSD', 'direction': 'sell', 'base_price': 1950.00,
        'base_sl': 1960.00, 'base_tp': 1930.00,
        'base_size_lots': 0.1, 'atr': 10.00
    }
    static_grid_sell_xau = StaticGridModel(
        base_params_sg_sell_xau, hist_data_sg, mock_rm_sg,
        num_grid_lines=5, use_base_tp_for_all=False, individual_tp_rr_ratio=1.0
    )
    sell_sg_orders_xau = static_grid_sell_xau.generate_grid_orders()
    for order in sell_sg_orders_xau: logger.info(f"  {order}")

    logger.info("--- Testing StaticGridModel Invalid Config (Price <= SL for BUY) ---")
    base_params_invalid_buy = base_params_sg_buy.copy()
    base_params_invalid_buy['base_price'] = 1.09900
    try:
        StaticGridModel(base_params_invalid_buy, hist_data_sg, mock_rm_sg, num_grid_lines=3)
    except ValueError as e:
        logger.error(f"Caught expected error for invalid buy setup: {e}")

    logger.info("--- Testing StaticGridModel Invalid Config (Price >= SL for SELL) ---")
    base_params_invalid_sell = base_params_sg_sell_xau.copy()
    base_params_invalid_sell['base_price'] = 1961.00
    try:
        StaticGridModel(base_params_invalid_sell, hist_data_sg, mock_rm_sg, num_grid_lines=3)
    except ValueError as e:
        logger.error(f"Caught expected error for invalid sell setup: {e}")

    logger.info("--- Testing StaticGridModel with spacing too small ---")
    base_params_small_range = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.10001,
        'base_sl': 1.10000, 'base_tp': 1.10005,
        'base_size_lots': 0.1, 'atr': 0.00100
    }
    static_grid_small_range = StaticGridModel(
        base_params_small_range, hist_data_sg, mock_rm_sg,
        num_grid_lines=2
    )
    small_range_orders = static_grid_small_range.generate_grid_orders()
    logger.info(f"Orders with too small spacing: {len(small_range_orders)}")
