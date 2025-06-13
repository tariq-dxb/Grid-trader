# grid_trader/config.py

# Default Risk Settings
DEFAULT_RISK_PER_TRADE_USD = 10.00  # Fixed $ risk per order
MAX_ACCOUNT_RISK_PERCENTAGE = 2.0  # Max % of account balance to risk at any time
DEFAULT_LEVERAGE = "1:100"  # Default leverage

# Order Settings
DEFAULT_MAX_REGENERATION_ATTEMPTS = 3
DEFAULT_COOLDOWN_PERIOD_BARS = 5  # Delay N bars before retrying an order
DEFAULT_SL_TP_WIDENING_FACTOR = 1.2  # Factor to widen SL/TP on retry (e.g., 1.2 = 20% wider)

# Grid Generation Settings
DEFAULT_ATR_PERIOD = 14
MIN_ATR_VALUE_FOR_VOLATILITY_GRID = 0.0005  # Example value, adjust based on typical symbol values
MAX_PRICE_DEVIATION_PERCENTAGE_FOR_RECENTER = 1.0  # Recenter if price moves > 1% from base
MAX_PRICE_DEVIATION_ATR_FOR_RECENTER = 2.0  # Recenter if price moves > 2 ATRs from base

# Technical Indicator Settings
EMA_SHORT_PERIOD = 12
EMA_LONG_PERIOD = 26
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25
BOLLINGER_BANDS_PERIOD = 20
BOLLINGER_BANDS_STD_DEV = 2

# Logging
LOG_LEVEL = "INFO"  # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
LOG_FILE = "grid_trader.log"

# API / Broker Settings (placeholders)
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
ACCOUNT_ID = "YOUR_ACCOUNT_ID"

# Symbol-Specific Settings (example)
SYMBOL_SETTINGS = {
    "EURUSD": {
        "min_lot_size": 0.01,
        "lot_step": 0.01,
        "pip_value_per_lot": 10.0, # For a standard lot
        "min_stop_level_pips": 2.0
    },
    "XAUUSD": {
        "min_lot_size": 0.01,
        "lot_step": 0.01,
        "pip_value_per_lot": 1.0, # For a 1 oz contract, 1 pip = $0.01, so 1 lot (100oz) = $1
        "min_stop_level_pips": 20.0
    }
}

# Add any other global constants or parameters your system might need
