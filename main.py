from engine.grid_manager import GridManager
from config import default_settings

if __name__ == "__main__":
    base_trade = {
        "symbol": "XAUUSD",
        "direction": "buy",
        "base_price": 2300.0,
        "base_sl": 2285.0,
        "base_tp": 2330.0,
        "base_size": 1.0,
        "atr": 4.0
    }

    manager = GridManager(base_trade, default_settings)
    manager.run()
