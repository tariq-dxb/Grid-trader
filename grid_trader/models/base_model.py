# grid_trader/models/base_model.py
from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Dict, Any
from ..utils.logger import get_logger
from .. import config

# Get a logger for this module
logger = get_logger(__name__)

class BaseGridModel(ABC):
    """
    Abstract base class for all grid trading models.

    Each grid model will inherit from this class and implement its own
    logic for generating pending orders based on a specific strategy.
    """
    def __init__(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame, risk_manager: Any):
        """
        Initializes the BaseGridModel.

        Args:
            base_trade_params (Dict[str, Any]): Dictionary containing parameters for the base trade.
                Expected keys: 'symbol', 'direction', 'base_price', 'base_sl',
                               'base_tp', 'base_size_lots', 'atr' (current ATR value).
            historical_data (pd.DataFrame): DataFrame containing historical price data
                                            (e.g., 'Open', 'High', 'Low', 'Close', 'Volume')
                                            and any pre-calculated indicators.
            risk_manager (Any): An instance of the RiskManager class to compute lot sizes
                                and check margin constraints.
        """
        self.symbol = base_trade_params.get('symbol')
        self.direction = base_trade_params.get('direction') # "buy" or "sell"
        self.base_price = float(base_trade_params.get('base_price', 0.0))
        self.base_sl = float(base_trade_params.get('base_sl', 0.0))
        self.base_tp = float(base_trade_params.get('base_tp', 0.0))
        self.base_size_lots = float(base_trade_params.get('base_size_lots', 0.01)) # Initial intended size for the base trade
        self.current_atr = float(base_trade_params.get('atr', 0.0))

        self.historical_data = historical_data
        self.risk_manager = risk_manager

        self.grid_orders: List[Dict[str, Any]] = []

        if not all([self.symbol, self.direction, self.base_price, self.base_sl, self.current_atr]):
            msg = "Missing critical base_trade_params: symbol, direction, base_price, base_sl, or atr."
            logger.error(msg)
            raise ValueError(msg)

        if self.direction.lower() not in ["buy", "sell"]:
            msg = f"Invalid trade direction: {self.direction}. Must be 'buy' or 'sell'."
            logger.error(msg)
            raise ValueError(msg)

        logger.info(f"Initialized {self.__class__.__name__} for {self.symbol} {self.direction} at {self.base_price}")

    @abstractmethod
    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        """
        Abstract method to generate the grid of pending orders.
        This method must be implemented by all derived classes.

        It should populate `self.grid_orders` and return it.
        Each order in the list should be a dictionary with keys like:
        'symbol', 'order_type' (e.g., 'BUY_STOP', 'SELL_LIMIT'),
        'entry_price', 'sl', 'tp', 'lot_size', 'original_grid_id' (optional).
        """
        pass

    def get_generated_orders(self) -> List[Dict[str, Any]]:
        """Returns the list of generated grid orders."""
        return self.grid_orders

    def _calculate_lot_size(self, entry_price: float, sl_price: float, risk_per_trade_usd: float = None) -> float:
        """
        Helper method to calculate lot size using the RiskManager.

        Args:
            entry_price (float): The entry price of the order.
            sl_price (float): The stop-loss price of the order.
            risk_per_trade_usd (float, optional): Fixed USD risk for this specific order.
                                                 Defaults to config.DEFAULT_RISK_PER_TRADE_USD.

        Returns:
            float: Calculated lot size, or 0.0 if calculation fails or risk is too high.
        """
        if risk_per_trade_usd is None:
            risk_per_trade_usd = config.DEFAULT_RISK_PER_TRADE_USD

        if self.risk_manager:
            try:
                lot_size = self.risk_manager.calculate_lot_size(
                    symbol=self.symbol,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    risk_per_trade_usd=risk_per_trade_usd,
                    account_balance=self.risk_manager.get_account_balance() # Assuming risk_manager has this
                )
                return lot_size
            except Exception as e:
                logger.error(f"Error calculating lot size for {self.symbol}: {e}", exc_info=True)
                return 0.0
        else:
            logger.warning("RiskManager not available for lot size calculation. Returning 0.0 lots.")
            return 0.0

    def recenter_grid(self, new_base_price: float, new_atr: float = None):
        """
        Optional method to adjust the grid based on a new base price or ATR.
        Specific models can override this if they support recentering.
        By default, it might just log or do nothing, requiring re-generation.
        """
        logger.info(f"{self.__class__.__name__} received recenter request to new base: {new_base_price}, new ATR: {new_atr}. "
                    "Default implementation does not recenter. Consider re-generating grid.")
        self.base_price = new_base_price
        if new_atr is not None:
            self.current_atr = new_atr
        # self.grid_orders.clear() # Optionally clear old orders
        # self.generate_grid_orders() # Optionally regenerate

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(symbol={self.symbol}, direction={self.direction}, base_price={self.base_price})"

# Example of a concrete class (for testing structure, not a real grid)
class DummyGridModel(BaseGridModel):
    def generate_grid_orders(self) -> List[Dict[str, Any]]:
        logger.info(f"DummyGridModel generating orders for {self.symbol} around {self.base_price}")
        # Example: one buy stop and one sell limit

        # Buy Stop (breakout)
        buy_stop_entry = self.base_price + self.current_atr * 1
        buy_stop_sl = self.base_price
        buy_stop_tp = self.base_price + self.current_atr * 3
        buy_stop_lots = self._calculate_lot_size(buy_stop_entry, buy_stop_sl)

        if buy_stop_lots > 0:
            self.grid_orders.append({
                'symbol': self.symbol, 'order_type': 'BUY_STOP',
                'entry_price': round(buy_stop_entry, 5), 'sl': round(buy_stop_sl, 5),
                'tp': round(buy_stop_tp, 5), 'lot_size': buy_stop_lots,
                'grid_id': f"{self.__class__.__name__}_BS1"
            })

        # Sell Limit (reversal)
        sell_limit_entry = self.base_price + self.current_atr * 0.5 # Entry slightly above base for a sell limit
        sell_limit_sl = self.base_price + self.current_atr * 1.5
        sell_limit_tp = self.base_price - self.current_atr * 1
        sell_limit_lots = self._calculate_lot_size(sell_limit_entry, sell_limit_sl)

        if sell_limit_lots > 0 :
            self.grid_orders.append({
                'symbol': self.symbol, 'order_type': 'SELL_LIMIT',
                'entry_price': round(sell_limit_entry, 5), 'sl': round(sell_limit_sl, 5),
                'tp': round(sell_limit_tp, 5), 'lot_size': sell_limit_lots,
                'grid_id': f"{self.__class__.__name__}_SL1"
            })

        logger.info(f"DummyGridModel generated {len(self.grid_orders)} orders.")
        return self.grid_orders

if __name__ == '__main__':
    # This example requires a mock RiskManager and config for direct execution
    class MockRiskManager:
        def get_account_balance(self):
            return 10000 # Example balance

        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd, account_balance):
            # Simplified lot calculation for testing
            price_diff = abs(entry_price - sl_price)
            if price_diff == 0: return 0.01 # Avoid division by zero

            # Assuming pip value for symbol EURUSD, 1 pip = $10 for 1 lot
            # This needs to be symbol-specific in real RM.
            # Use the config from sys.modules if available, otherwise a default.
            # This ensures the __main__ block uses the potentially mocked config.
            cfg = sys.modules.get('grid_trader.config', config) # Fallback to module's config if not in sys.modules

            pip_value_per_lot = cfg.SYMBOL_SETTINGS.get(symbol, {}).get('pip_value_per_lot', 10)

            sl_pips = price_diff / 0.0001 # Assuming 5-digit broker, 1 pip = 0.0001

            if sl_pips == 0: return 0.01

            lot_size = risk_per_trade_usd / (sl_pips * pip_value_per_lot)

            min_lot = cfg.SYMBOL_SETTINGS.get(symbol, {}).get('min_lot_size', 0.01)
            lot_step = cfg.SYMBOL_SETTINGS.get(symbol, {}).get('lot_step', 0.01)

            lot_size = max(min_lot, round(lot_size / lot_step) * lot_step)
            logger.debug(f"MockRM: Calc lots for {symbol}: entry={entry_price}, sl={sl_price}, risk={risk_per_trade_usd}, sl_pips={sl_pips}, lots={lot_size}")
            return lot_size if lot_size > 0 else min_lot


    # Mock config for __main__
    class MainConfig:
        DEFAULT_RISK_PER_TRADE_USD = 10.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_base_model.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 10.0},
            "XAUUSD": {"min_lot_size": 0.01, "lot_step": 0.01, "pip_value_per_lot": 1.0}
        }

    import sys
    # Patch sys.modules so any new imports of 'grid_trader.config' get MainConfig.
    sys.modules['grid_trader.config'] = MainConfig

    # Also, explicitly patch the 'config' object already imported by 'grid_trader.utils.logger'.
    # This ensures that the get_logger function uses our MainConfig for this test run.
    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfig

    # Now, when get_logger (from util_logger) is called, it will use MainConfig.
    # The module-level logger in this file (base_model.py) needs to be re-initialized
    # if it was already created using the old config.
    # Since __name__ is "__main__" when run as a script, the module-level 'logger'
    # is the same as the one we'd get by calling get_logger("__main__") here.
    # So, we re-initialize it to pick up the new config.
    logger = get_logger(__name__) # Re-initializes the logger named "__main__" with MainConfig.

    # Also, the MockRiskManager needs to see the patched config for SYMBOL_SETTINGS
    # Its current code `cfg = sys.modules.get('grid_trader.config', config)` should work
    # because sys.modules['grid_trader.config'] is now MainConfig.

    mock_risk_manager = MockRiskManager()

    sample_hist_data = pd.DataFrame({
        'Open': [1.1000, 1.1010], 'High': [1.1020, 1.1030],
        'Low': [1.0990, 1.1000], 'Close': [1.1010, 1.1020]
    })

    base_params = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': 1.1015,
        'base_sl': 1.0900, 'base_tp': 1.1200, 'base_size_lots': 0.1, 'atr': 0.0015
    }

    logger.info("--- Testing DummyGridModel ---")
    dummy_model = DummyGridModel(base_trade_params=base_params,
                                 historical_data=sample_hist_data,
                                 risk_manager=mock_risk_manager)

    orders = dummy_model.generate_grid_orders()

    if orders:
        logger.info(f"Generated {len(orders)} orders:")
        for order in orders:
            logger.info(f"  {order}")
    else:
        logger.info("No orders generated by DummyGridModel.")

    logger.info("--- Testing BaseGridModel validation ---")
    try:
        invalid_params = base_params.copy()
        invalid_params['direction'] = 'sideways'
        DummyGridModel(invalid_params, sample_hist_data, mock_risk_manager)
    except ValueError as e:
        logger.error(f"Caught expected error: {e}")

    try:
        invalid_params_2 = base_params.copy()
        del invalid_params_2['atr']
        DummyGridModel(invalid_params_2, sample_hist_data, mock_risk_manager)
    except ValueError as e:
        logger.error(f"Caught expected error for missing atr: {e}")
