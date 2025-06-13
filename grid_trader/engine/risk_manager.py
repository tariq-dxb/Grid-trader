# grid_trader/engine/risk_manager.py
import math
from typing import Dict # Import Dict for type hinting
from .. import config
from ..utils.logger import get_logger

logger = get_logger(__name__)

class RiskManager:
    """
    Manages risk for trading operations, including lot size calculation,
    leverage considerations, and exposure limits.
    """
    def __init__(self, account_balance: float, leverage: str,
                 max_account_risk_percentage: float = None,
                 default_risk_per_trade_usd: float = None):
        """
        Initializes the RiskManager.

        Args:
            account_balance (float): Current account balance in account currency.
            leverage (str): Account leverage, e.g., "1:100", "1:50".
            max_account_risk_percentage (float, optional): Maximum percentage of account
                                                           to risk across all trades.
                                                           Defaults to config.MAX_ACCOUNT_RISK_PERCENTAGE.
            default_risk_per_trade_usd (float, optional): Default fixed USD risk for a single trade/order.
                                                          Defaults to config.DEFAULT_RISK_PER_TRADE_USD.
        """
        self.account_balance = float(account_balance)
        self.leverage_ratio = self._parse_leverage(leverage) # Store as a float e.g. 100 for "1:100"

        self.max_account_risk_percentage = max_account_risk_percentage \
            if max_account_risk_percentage is not None \
            else config.MAX_ACCOUNT_RISK_PERCENTAGE

        self.default_risk_per_trade_usd = default_risk_per_trade_usd \
            if default_risk_per_trade_usd is not None \
            else config.DEFAULT_RISK_PER_TRADE_USD

        self.current_total_exposure_usd = 0.0
        self.current_total_at_risk_usd = 0.0

        logger.info(f"RiskManager initialized: Balance={self.account_balance}, Leverage=1:{self.leverage_ratio}, "
                    f"MaxAccRisk={self.max_account_risk_percentage}%, DefaultRisk/Trade=${self.default_risk_per_trade_usd}")

    def _parse_leverage(self, leverage_str: str) -> float:
        try:
            if ":" not in leverage_str:
                raise ValueError("Leverage string must be in '1:N' format.")
            parts = leverage_str.split(':')
            if float(parts[0]) != 1: # Ensure it's "1:N" not "N:1" or other
                raise ValueError("Leverage string must start with '1:'.")
            ratio = float(parts[1])
            if ratio <= 0:
                raise ValueError("Leverage ratio must be positive.")
            return ratio
        except Exception as e:
            logger.error(f"Invalid leverage string: {leverage_str}. Defaulting to 1:100. Error: {e}", exc_info=True)
            return 100.0

    def get_symbol_config(self, symbol: str) -> Dict:
        settings = config.SYMBOL_SETTINGS.get(symbol)
        if not settings:
            logger.error(f"Symbol {symbol} not found in SYMBOL_SETTINGS in config.py.")
            raise ValueError(f"Configuration for symbol {symbol} not found.")

        required_keys = ['pip_value_per_lot', 'min_lot_size', 'lot_step', 'decimals']
        for key in required_keys:
            if key not in settings:
                msg = f"Missing required key '{key}' in SYMBOL_SETTINGS for {symbol}."
                logger.error(msg)
                raise ValueError(msg)

        if 'point_value' not in settings :
            settings['point_value'] = 10**(-settings['decimals'])

        return settings

    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float,
                           risk_per_trade_usd: float = None, account_balance_override: float = None) -> float:
        current_balance = account_balance_override if account_balance_override is not None else self.account_balance
        risk_amount_usd = risk_per_trade_usd if risk_per_trade_usd is not None else self.default_risk_per_trade_usd

        if entry_price == sl_price:
            logger.warning(f"LotCalc ({symbol}): Entry price and SL price are identical ({entry_price}). Cannot calculate lot size.")
            return 0.0
        if risk_amount_usd <= 0:
            logger.warning(f"LotCalc ({symbol}): Risk amount USD ({risk_amount_usd}) must be positive.")
            return 0.0

        sym_config = self.get_symbol_config(symbol)
        pip_value_per_lot = sym_config['pip_value_per_lot']
        min_lot = sym_config['min_lot_size']
        lot_step = sym_config['lot_step']
        point = sym_config['point_value']
        decimals = sym_config['decimals']

        sl_distance_abs = abs(round(entry_price - sl_price, decimals + 1)) # Round diff to avoid tiny float issues
        if sl_distance_abs == 0 : return 0.0


        standard_pip_unit = point * 10 # Default: 1 standard pip = 10 points
        if "JPY" in symbol.upper(): # For JPY pairs, 1 pip is 0.01 (if 1 point is 0.001)
            standard_pip_unit = 10**(-(decimals - 1)) if decimals >=1 else 0.01
        elif sym_config.get('is_cfd_or_metal', False) or "XAU" in symbol or "XAG" in symbol or "OIL" in symbol or "IDX" in symbol.upper():
            standard_pip_unit = point # For these, "pip" is often the smallest increment (point)

        sl_pips = sl_distance_abs / standard_pip_unit

        if sl_pips <= 0:
            logger.warning(f"LotCalc ({symbol}): SL pips is zero or negative ({sl_pips}). Cannot calculate lot size.")
            return 0.0

        value_at_risk_per_lot = sl_pips * pip_value_per_lot
        if value_at_risk_per_lot <= 0:
            logger.warning(f"LotCalc ({symbol}): Value at risk per lot ({value_at_risk_per_lot}) is zero/negative. Check pip value and SL distance.")
            return 0.0

        raw_lot_size = risk_amount_usd / value_at_risk_per_lot

        if lot_step > 0:
            stepped_lot_size = math.floor(raw_lot_size / lot_step) * lot_step
        else:
            stepped_lot_size = raw_lot_size

        final_lot_size = stepped_lot_size # Start with stepped, then check against min_lot

        if final_lot_size < min_lot:
            if raw_lot_size >= min_lot: # If raw was okay, but stepping down made it too small, use min_lot
                final_lot_size = min_lot
                logger.debug(f"LotCalc ({symbol}): Stepped lot size {stepped_lot_size} < min_lot {min_lot}. Raw was {raw_lot_size}. Using min_lot.")
            else: # Raw was already too small for min_lot, implies risk is too low for this SL
                logger.warning(f"LotCalc ({symbol}): Raw lot size {raw_lot_size} too small for min_lot {min_lot} with risk ${risk_amount_usd} and SL {sl_pips:.2f} pips. Returning 0 lots.")
                return 0.0

        contract_size = sym_config.get('contract_size', 100000)
        position_nominal_value = final_lot_size * contract_size

        # Crude conversion to USD for margin if necessary
        # Assumes account currency is USD.
        position_nominal_value_usd = position_nominal_value
        if not sym_config.get('base_currency_is_account_currency', False):
             if "USD" == symbol[:3].upper(): # Base is USD e.g. USDCAD, USDJPY
                  pass # Nominal value is effectively in USD terms of exposure
             elif "USD" == symbol[-3:].upper(): # Quote is USD e.g. EURUSD, GBPUSD
                  position_nominal_value_usd *= entry_price
             else:
                  logger.warning(f"LotCalc ({symbol}): Margin calc for cross-currency pair {symbol} may be inaccurate (needs live rates). Using rough estimate.")
                  # This part is tricky. For a pair like EURJPY, if account is USD,
                  # value = lots * contract_size_EUR * EURUSD_rate.
                  # For simplicity, if quote is not USD, we might use entry_price as a proxy for conversion factor to USD.
                  # This is a placeholder for a proper currency conversion service.
                  # position_nominal_value_usd *= entry_price # Very rough if quote is not USD

        required_margin = position_nominal_value_usd / self.leverage_ratio

        max_loss_for_this_trade = risk_amount_usd
        # Max loss based on account percentage for THIS trade, not total.
        # This is a policy check on the input risk_per_trade_usd.
        allowed_risk_based_on_account_perc = (self.max_account_risk_percentage / 100.0) * current_balance
        if max_loss_for_this_trade > allowed_risk_based_on_account_perc:
            logger.warning(f"LotCalc ({symbol}): Requested risk ${max_loss_for_this_trade} "
                           f"exceeds account % limit per trade (${allowed_risk_based_on_account_perc}). "
                           f"Lot size is based on ${max_loss_for_this_trade}.")
            # Could cap risk_amount_usd here and re-calculate, or just warn. Current: warn.

        if required_margin > current_balance :
            logger.warning(f"LotCalc ({symbol}): Required margin (${required_margin:.2f}) for {final_lot_size} lots "
                           f"exceeds total balance (${current_balance:.2f}).")
            return 0.0 # Definitely cannot open
        elif required_margin > 0.5 * current_balance : # Arbitrary threshold for "too much of balance"
             logger.error(f"LotCalc ({symbol}): Required margin (${required_margin:.2f}) for {final_lot_size} lots is > 50% of balance. Reducing lot size to 0 for safety.")
             return 0.0

        logger.info(f"LotCalc ({symbol}): Entry={entry_price}, SL={sl_price} ({sl_pips:.2f} pips). "
                    f"Risk=${risk_amount_usd:.2f}. RawLots={raw_lot_size:.4f}. FinalLots={final_lot_size:.2f}. Est.Margin=${required_margin:.2f}")
        return round(final_lot_size, int(-math.log10(lot_step)) if lot_step > 0 else 2) # Round to lot_step precision

    def get_account_balance(self) -> float:
        return self.account_balance

    def update_account_balance(self, new_balance: float):
        self.account_balance = new_balance
        logger.info(f"Account balance updated to: {self.account_balance}")

    def can_open_trade(self, symbol: str, lot_size: float, entry_price: float) -> bool:
        if lot_size <= 0:
            return False

        sym_config = self.get_symbol_config(symbol)
        contract_size = sym_config.get('contract_size', 100000)
        position_nominal_value = lot_size * contract_size

        position_nominal_value_usd = position_nominal_value
        if not sym_config.get('base_currency_is_account_currency', False):
             if "USD" == symbol[:3].upper(): pass
             elif "USD" == symbol[-3:].upper(): position_nominal_value_usd *= entry_price

        required_margin = position_nominal_value_usd / self.leverage_ratio
        # Simplified check: if this trade uses >80% of *total* balance (not free margin)
        if required_margin > 0.8 * self.account_balance:
            logger.warning(f"CanOpenTrade ({symbol}): Required margin for {lot_size} lots would use >80% of total balance. Blocking.")
            return False

        # TODO: Add checks for total open positions, max exposure per symbol, max total account risk from open trades.
        return True


if __name__ == '__main__':
    class MainConfigRM:
        DEFAULT_RISK_PER_TRADE_USD = 10.0
        MAX_ACCOUNT_RISK_PERCENTAGE = 2.0
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "test_risk_manager.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"pip_value_per_lot": 10.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 5, "point_value": 0.00001, "contract_size": 100000},
            "USDJPY": {"pip_value_per_lot": 8.5, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 3, "point_value": 0.001, "contract_size": 100000, "base_currency_is_account_currency": True},
            "XAUUSD": {"pip_value_per_lot": 1.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 2, "point_value": 0.01, "contract_size": 100, "is_cfd_or_metal": True, "base_currency_is_account_currency": True}
        }

    import sys
    sys.modules['grid_trader.config'] = MainConfigRM # For any *new* imports of 'grid_trader.config'

    # Rebind this module's 'config' global to use MainConfigRM directly.
    # This ensures that functions/methods in this file that refer to 'config'
    # (which was imported at the top of the file) will use MainConfigRM for this test run.
    config = MainConfigRM

    import grid_trader.utils.logger as util_logger
    util_logger.config = MainConfigRM # Ensure logger used by get_logger also uses this test config
    logger = get_logger(__name__) # Re-initialize this module's logger with the new config

    rm = RiskManager(account_balance=10000, leverage="1:100", default_risk_per_trade_usd=10.0)

    logger.info("--- Testing EURUSD Lot Sizing ---")
    # SL = 0.00500 (50 standard pips). Risk $10. standard_pip_unit = 0.0001. sl_pips = 0.00500 / 0.0001 = 50.
    # Value at risk per lot = 50 * $10 = $500. Lots = $10 / $500 = 0.02.
    lots_eurusd = rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09500)
    logger.info(f"EURUSD (50 pip SL, $10 risk): Calculated Lots = {lots_eurusd}")
    assert abs(lots_eurusd - 0.02) < 0.0001

    lots_eurusd_small_risk = rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09500, risk_per_trade_usd=1)
    logger.info(f"EURUSD (50 pip SL, $1 risk): Calculated Lots = {lots_eurusd_small_risk}")
    # Raw lots = 1 / 500 = 0.002. Stepped = floor(0.002/0.01)*0.01 = 0. final_lot_size < min_lot (0 < 0.01) -> returns 0.0
    assert abs(lots_eurusd_small_risk - 0.00) < 0.0001

    logger.info("--- Testing USDJPY Lot Sizing ---")
    # SL = 0.500 (50 standard pips). Risk $10. standard_pip_unit = 0.01. sl_pips = 0.500 / 0.01 = 50.
    # Value at risk per lot = 50 * $8.5 = $425. Lots = $10 / $425 = 0.0235... Stepped to 0.02.
    lots_usdjpy = rm.calculate_lot_size("USDJPY", entry_price=130.000, sl_price=130.500)
    logger.info(f"USDJPY (50 pip SL, $10 risk): Calculated Lots = {lots_usdjpy}")
    assert abs(lots_usdjpy - 0.02) < 0.0001

    logger.info("--- Testing XAUUSD Lot Sizing ---")
    # SL = $10 (e.g. 1950 to 1940). Risk $10. standard_pip_unit = point = 0.01.
    # sl_pips = 10.00 / 0.01 = 1000.
    # pip_value_per_lot = $1 (value of $0.01 price move for 1 lot of 100oz).
    # Value at risk per lot = 1000 * $1 = $1000. Lots = $10 / $1000 = 0.01.
    lots_xauusd = rm.calculate_lot_size("XAUUSD", entry_price=1950.00, sl_price=1940.00)
    logger.info(f"XAUUSD ($10 SL, $10 risk): Calculated Lots = {lots_xauusd}")
    assert abs(lots_xauusd - 0.01) < 0.0001

    # SL = $0.50. sl_pips = 0.50 / 0.01 = 50. Value at risk per lot = 50 * $1 = $50. Lots = $10 / $50 = 0.20.
    lots_xauusd_tight_sl = rm.calculate_lot_size("XAUUSD", entry_price=1950.00, sl_price=1949.50)
    logger.info(f"XAUUSD ($0.50 SL, $10 risk): Calculated Lots = {lots_xauusd_tight_sl}")
    assert abs(lots_xauusd_tight_sl - 0.20) < 0.0001

    logger.info("--- Testing Margin Calculation (EURUSD, to see log) ---")
    rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09500)

    logger.info("--- Testing Invalid Symbol ---")
    try:
        rm.calculate_lot_size("INVALID_SYMBOL", 1.0, 0.9)
    except ValueError as e:
        logger.info(f"Caught expected error for invalid symbol: {e}")

    logger.info("--- Testing SL equals Entry ---")
    lots_eq = rm.calculate_lot_size("EURUSD", 1.1, 1.1)
    assert lots_eq == 0.0, f"Expected 0.0 lots, got {lots_eq}"

    logger.info("RiskManager tests completed.")
