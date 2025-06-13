"""
Test generation logic of grid models.
"""

from models.volatility_grid import VolatilityGrid
from models.dual_grid import DualSidedGrid
from models.pyramid_grid import PyramidingGrid
from models.static_grid import StaticGrid

def test_volatility_grid():
    model = VolatilityGrid()
    base_trade = {
        "symbol": "XAUUSD",
        "direction": "buy",
        "base_price": 2300.0,
        "base_sl": 2285.0,
        "base_tp": 2330.0,
        "base_size": 1.0,
        "atr": 4.0
    }
    settings = {
        "order_count": 5,
        "risk_per_order": 10.0,
        "rr_ratio": 1.5,
        "atr_multiplier": 0.5,
        "max_attempts": 2,
        "cooldown_bars": 5
    }

    orders = model.generate_grid(base_trade, settings)
    assert len(orders) == 5
    assert all(o["type"] == "sell_stop" for o in orders)
