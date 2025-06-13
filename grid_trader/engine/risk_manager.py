# grid_trader/engine/risk_manager.py
import math
from typing import Dict, Optional, Any
from .. import config
from ..utils.logger import get_logger

logger = get_logger(__name__)

class RiskManager:
    def __init__(self, account_balance: float, leverage: str,
                 max_account_risk_percentage: float = None,
                 default_risk_per_trade_usd: float = None,
                 mt5_connector: Optional[Any] = None):
        self.account_balance = float(account_balance)
        self.leverage_str = leverage
        self.leverage_ratio = self._parse_leverage(leverage)
        self.account_currency = "USD"

        self.max_account_risk_percentage = max_account_risk_percentage \
            if max_account_risk_percentage is not None \
            else getattr(config, 'MAX_ACCOUNT_RISK_PERCENTAGE', 2.0)

        self.default_risk_per_trade_usd = default_risk_per_trade_usd \
            if default_risk_per_trade_usd is not None \
            else getattr(config, 'DEFAULT_RISK_PER_TRADE_USD', 10.0)

        self.current_total_exposure_usd = 0.0
        self.current_total_at_risk_usd = 0.0

        self.mt5_connector = mt5_connector
        if self.mt5_connector:
            self.load_account_details_from_mt5()

        logger.info(f"RiskManager initialized: Balance={self.account_balance} {self.account_currency}, "
                    f"Leverage=1:{self.leverage_ratio}, MaxAccRisk={self.max_account_risk_percentage}%, "
                    f"DefaultRisk/Trade=${self.default_risk_per_trade_usd}")

    def load_account_details_from_mt5(self):
        if self.mt5_connector and \
           hasattr(self.mt5_connector, 'is_mt5_connected') and \
           hasattr(self.mt5_connector, 'get_mt5_account_info') and \
           self.mt5_connector.is_mt5_connected():
            logger.info("RiskManager: Attempting to load account details from MT5...")
            acc_info = self.mt5_connector.get_mt5_account_info()
            if acc_info:
                self.account_balance = float(acc_info.get('balance', self.account_balance))
                mt5_leverage = float(acc_info.get('leverage', self.leverage_ratio))
                if mt5_leverage > 0 : self.leverage_ratio = mt5_leverage
                self.account_currency = acc_info.get('currency', self.account_currency).upper()
                self.leverage_str = f"1:{int(self.leverage_ratio)}"
                logger.info(f"RiskManager: Account details updated from MT5. Balance: {self.account_balance} {self.account_currency}, Leverage: 1:{self.leverage_ratio}")
            else: logger.warning("RiskManager: Failed to fetch account details from MT5. Using initial/default values.")
        else: logger.info("RiskManager: MT5 connector not available/connected or lacks methods. Using initial/default values.")

    def _parse_leverage(self, leverage_str: str) -> float:
        try:
            if ":" not in leverage_str: raise ValueError("Leverage string must be in '1:N' format.")
            parts = leverage_str.split(':');
            if not (len(parts) == 2 and parts[0] == '1' and parts[1].replace('.', '', 1).isdigit()): # check for 1:N format
                 raise ValueError("Leverage string format error. Expected '1:N'.")
            ratio = float(parts[1])
            if ratio <= 0: raise ValueError("Leverage ratio must be positive.")
            return ratio
        except Exception as e: logger.error(f"Invalid leverage string: {leverage_str}. Defaulting to 1:100. Error: {e}", exc_info=True); return 100.0

    def get_symbol_config(self, symbol: str) -> Dict[str, Any]:
        static_conf = config.SYMBOL_SETTINGS.get(symbol)
        base_config = {}
        if static_conf:
            base_config = static_conf.copy()
        else:
            logger.warning(f"Symbol {symbol} not found in static SYMBOL_SETTINGS. Relying on MT5 or defaults.")

        if self.mt5_connector and \
           hasattr(self.mt5_connector, 'is_mt5_connected') and self.mt5_connector.is_mt5_connected() and \
           hasattr(self.mt5_connector, 'get_mt5_symbol_properties'):
            logger.debug(f"RiskManager: Fetching live symbol properties for {symbol} from MT5.")
            live_props = self.mt5_connector.get_mt5_symbol_properties(symbol)
            if live_props:
                logger.info(f"RiskManager: Merging live properties for {symbol} from MT5.")
                if live_props.get('digits') is not None: base_config['decimals'] = int(live_props['digits'])
                if live_props.get('point') is not None and live_props.get('point') > 0: base_config['point_value'] = float(live_props['point'])
                if live_props.get('volume_min') is not None: base_config['min_lot_size'] = float(live_props['volume_min'])
                if live_props.get('volume_step') is not None: base_config['lot_step'] = float(live_props['volume_step'])
                if live_props.get('contract_size') is not None: base_config['contract_size'] = float(live_props['contract_size']) # MT5: trade_contract_size
                base_config['mt5_raw_properties'] = live_props.get('raw_mt5_properties')
                logger.debug(f"RiskManager: Merged config for {symbol} with MT5 data: {list(base_config.keys())}")
            else: logger.warning(f"RiskManager: Failed to fetch live properties for {symbol} from MT5.")

        final_config = base_config
        if 'point_value' not in final_config and 'decimals' in final_config and final_config['decimals'] is not None:
            final_config['point_value'] = 10**(-final_config['decimals'])

        required_keys = ['pip_value_per_lot', 'min_lot_size', 'lot_step', 'decimals', 'point_value', 'contract_size']
        missing_keys = [key for key in required_keys if key not in final_config or final_config.get(key) is None]
        if missing_keys:
            msg = f"Missing essential config keys for {symbol} after MT5 merge: {missing_keys}. Check static config or MT5 data."
            logger.error(msg); raise ValueError(msg)
        return final_config

    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float,
                           risk_per_trade_usd: float = None, account_balance_override: float = None) -> float:
        current_balance = account_balance_override if account_balance_override is not None else self.account_balance
        risk_amount_usd = risk_per_trade_usd if risk_per_trade_usd is not None else self.default_risk_per_trade_usd
        if entry_price == sl_price: logger.warning(f"LotCalc ({symbol}): Entry=SL ({entry_price}). No lot size."); return 0.0
        if risk_amount_usd <= 0: logger.warning(f"LotCalc ({symbol}): Risk USD ({risk_amount_usd}) must be >0."); return 0.0

        sym_config = self.get_symbol_config(symbol)
        pip_value_per_lot = sym_config['pip_value_per_lot']; min_lot = sym_config['min_lot_size']
        lot_step = sym_config['lot_step']; point = sym_config['point_value']; decimals = sym_config['decimals']
        sl_distance_abs = abs(round(entry_price - sl_price, decimals + 2 if decimals < 7 else 7))
        if sl_distance_abs < point / 2 : logger.warning(f"LotCalc ({symbol}): SL distance too small or zero. ({sl_distance_abs})"); return 0.0

        standard_pip_unit = point * 10
        if "JPY" in symbol.upper(): standard_pip_unit = 10**(-(decimals - 1)) if decimals >=1 else 0.01
        elif sym_config.get('is_cfd_or_metal', False) or any(sub in symbol.upper() for sub in ["XAU", "XAG", "OIL", "IDX"]):
            standard_pip_unit = point
        sl_pips = sl_distance_abs / standard_pip_unit
        if sl_pips <= 0: logger.warning(f"LotCalc ({symbol}): SL pips zero/negative ({sl_pips}). No lot size."); return 0.0
        value_at_risk_per_lot = sl_pips * pip_value_per_lot
        if value_at_risk_per_lot <= 0: logger.warning(f"LotCalc ({symbol}): Val@Risk/lot zero/negative ({value_at_risk_per_lot})."); return 0.0
        raw_lot_size = risk_amount_usd / value_at_risk_per_lot

        if lot_step > 0: stepped_lot_size = math.floor(raw_lot_size / lot_step) * lot_step
        else: stepped_lot_size = raw_lot_size
        final_lot_size = stepped_lot_size
        if final_lot_size < min_lot:
            if raw_lot_size >= min_lot: final_lot_size = min_lot; logger.debug(f"LotCalc ({symbol}): Stepped {stepped_lot_size} < min_lot {min_lot}. Raw {raw_lot_size}. Using min_lot.")
            else: logger.warning(f"LotCalc ({symbol}): Raw lot {raw_lot_size} too small for min_lot {min_lot}. Risk ${risk_amount_usd}, SL {sl_pips:.2f} pips. Returning 0 lots."); return 0.0

        contract_size = sym_config.get('contract_size', 100000)
        position_nominal_value = final_lot_size * contract_size
        position_nominal_value_usd = position_nominal_value
        if not sym_config.get('base_currency_is_account_currency', False):
             if "USD" == symbol[:3].upper(): pass
             elif "USD" == symbol[-3:].upper(): position_nominal_value_usd *= entry_price
             else: logger.warning(f"LotCalc ({symbol}): Margin calc for cross-currency {symbol} may be inaccurate.")
        required_margin = position_nominal_value_usd / self.leverage_ratio
        allowed_risk_based_on_account_perc = (self.max_account_risk_percentage / 100.0) * current_balance
        if risk_amount_usd > allowed_risk_based_on_account_perc:
            logger.warning(f"LotCalc ({symbol}): Risk ${risk_amount_usd} > acc % limit per trade (${allowed_risk_based_on_account_perc}). Lot size based on ${risk_amount_usd}.")
        if required_margin > current_balance : logger.warning(f"LotCalc ({symbol}): Margin (${required_margin:.2f}) for {final_lot_size} lots > balance (${current_balance:.2f})."); return 0.0
        elif required_margin > 0.5 * current_balance : logger.error(f"LotCalc ({symbol}): Margin (${required_margin:.2f}) for {final_lot_size} lots > 50% balance. Returning 0 lots."); return 0.0

        final_lot_precision = int(-math.log10(lot_step)) if lot_step > 0 and lot_step < 1 and lot_step != 0 else (abs(math.log10(lot_step)).is_integer() if lot_step > 0 else 2) # type: ignore
        if not isinstance(final_lot_precision, int) : final_lot_precision = 2 # Fallback for safety
        final_lot_size_rounded = round(final_lot_size, final_lot_precision)

        logger.info(f"LotCalc ({symbol}): Entry={entry_price}, SL={sl_price} ({sl_pips:.2f} pips). Risk=${risk_amount_usd:.2f}. RawLots={raw_lot_size:.4f}. FinalLots={final_lot_size_rounded:.{final_lot_precision}f}. Est.Margin=${required_margin:.2f}")
        return final_lot_size_rounded

    def get_account_balance(self) -> float: return self.account_balance
    def update_account_balance(self, new_balance: float): self.account_balance = new_balance; logger.info(f"Account balance updated to: {self.account_balance}")

    def can_open_trade(self, symbol: str, lot_size: float, entry_price: float) -> bool:
        if lot_size <= 0: return False
        sym_config = self.get_symbol_config(symbol); contract_size = sym_config.get('contract_size', 100000)
        position_nominal_value = lot_size * contract_size; position_nominal_value_usd = position_nominal_value
        if not sym_config.get('base_currency_is_account_currency', False):
             if "USD" == symbol[:3].upper(): pass
             elif "USD" == symbol[-3:].upper(): position_nominal_value_usd *= entry_price
        required_margin = position_nominal_value_usd / self.leverage_ratio
        if required_margin > 0.8 * self.account_balance:
            logger.warning(f"CanOpenTrade ({symbol}): Margin for {lot_size} lots >80% total balance. Blocking."); return False
        return True

if __name__ == '__main__':
    class MainConfigRMTest: # Renamed to avoid conflict if TestConfigRM is imported elsewhere
        DEFAULT_RISK_PER_TRADE_USD = 10.0; MAX_ACCOUNT_RISK_PERCENTAGE = 2.0
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_risk_manager.log"
        SYMBOL_SETTINGS = {
            "EURUSD": {"pip_value_per_lot": 10.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 5, "point_value": 0.00001, "contract_size": 100000},
            "USDJPY": {"pip_value_per_lot": 8.5, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 3, "point_value": 0.001, "contract_size": 100000, "base_currency_is_account_currency": True},
            "XAUUSD": {"pip_value_per_lot": 1.0, "min_lot_size": 0.01, "lot_step": 0.01, "decimals": 2, "point_value": 0.01, "contract_size": 100, "is_cfd_or_metal": True, "base_currency_is_account_currency": True},
            "NODECIMALS": {"pip_value_per_lot": 1.0, "min_lot_size": 1.0, "lot_step": 1.0, "decimals": 0, "point_value": 1.0, "contract_size": 1}
        }

    import sys
    mock_config_instance_rm_main = MainConfigRMTest()
    sys.modules['grid_trader.config'] = mock_config_instance_rm_main

    import grid_trader.utils.logger as util_logger
    util_logger.config = mock_config_instance_rm_main
    config = mock_config_instance_rm_main
    logger = get_logger(__name__)

    class MockMT5ConnectorForRMTest: # Renamed to avoid conflict
        def is_mt5_connected(self): return True
        def get_mt5_account_info(self):
            logger.info("MockMT5ConnectorForRMTest: get_mt5_account_info called")
            return {"balance": 12345.67, "leverage": 200.0, "currency": "EUR"}
        def get_mt5_symbol_properties(self, symbol):
            logger.info(f"MockMT5ConnectorForRMTest: get_mt5_symbol_properties for {symbol}")
            if symbol == "EURUSD":
                return {'name': 'EURUSD', 'digits': 5, 'point': 1e-05, 'trade_contract_size': 100000.0,
                        'volume_min': 0.01, 'volume_step': 0.01,
                        'raw_mt5_properties': {'description': 'Euro vs US Dollar from Mock MT5'}}
            return None

    logger.info("--- Testing RiskManager without MT5 ---")
    rm_no_mt5 = RiskManager(account_balance=5000, leverage="1:50")
    assert rm_no_mt5.account_balance == 5000 and rm_no_mt5.leverage_ratio == 50 and rm_no_mt5.account_currency == "USD"

    logger.info("--- Testing RiskManager with MT5 for account details ---")
    mock_connector = MockMT5ConnectorForRMTest()
    rm_with_mt5_acc = RiskManager(account_balance=1000, leverage="1:30", mt5_connector=mock_connector)
    assert math.isclose(rm_with_mt5_acc.account_balance, 12345.67)
    assert math.isclose(rm_with_mt5_acc.leverage_ratio, 200.0)
    assert rm_with_mt5_acc.account_currency == "EUR"
    logger.info(f"RM with MT5 mock (account): Balance {rm_with_mt5_acc.account_balance} {rm_with_mt5_acc.account_currency}, Leverage 1:{rm_with_mt5_acc.leverage_ratio}")

    logger.info("--- Testing RiskManager with MT5 for symbol properties (EURUSD) ---")
    # Static EURUSD config has decimals: 5, point: 0.00001
    # Mock MT5 returns digits: 5, point: 1e-05. These should match or MT5 should override.
    eurusd_conf_merged = rm_with_mt5_acc.get_symbol_config("EURUSD")
    assert eurusd_conf_merged['decimals'] == 5
    assert math.isclose(eurusd_conf_merged['point_value'], 1e-05)
    assert eurusd_conf_merged['contract_size'] == 100000.0
    assert 'mt5_raw_properties' in eurusd_conf_merged
    logger.info(f"RM main - Merged EURUSD config from MT5 source: {eurusd_conf_merged.get('mt5_raw_properties', {}).get('description')}")

    logger.info("--- Original RiskManager tests from its __main__ ---")
    rm = RiskManager(account_balance=10000, leverage="1:100", default_risk_per_trade_usd=10.0, mt5_connector=mock_connector) # Pass connector here too
    logger.info("--- Testing EURUSD Lot Sizing (original tests with potentially live data) ---")
    lots_eurusd = rm.calculate_lot_size("EURUSD", entry_price=1.10000, sl_price=1.09500)
    logger.info(f"EURUSD (50 pip SL, $10 risk): Calculated Lots = {lots_eurusd}")
    # Expected: risk $10. SL 50 pips (0.00500). For EURUSD, pip val $10. Val@Risk/Lot = 50*10=500. Lots = 10/500=0.02
    assert abs(lots_eurusd - 0.02) < 0.0001

    logger.info("RiskManager tests completed (from original __main__ section).")
