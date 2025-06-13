from engine.signal_router import SignalRouter
from engine.order_manager import OrderManager
from models.volatility_grid import VolatilityGrid
from models.dual_grid import DualSidedGrid
from models.pyramid_grid import PyramidingGrid
from models.static_grid import StaticGrid

class GridManager:
    def __init__(self, base_trade, config):
        self.trade = base_trade
        self.config = config
        self.model_name = SignalRouter().choose_model(base_trade)
        self.model = self._load_model(self.model_name)

    def _load_model(self, name):
        if name == "volatility":
            return VolatilityGrid()
        elif name == "dual":
            return DualSidedGrid()
        elif name == "pyramiding":
            return PyramidingGrid()
        elif name == "static":
            return StaticGrid()
        else:
            return VolatilityGrid()  # default fallback

    def run(self):
        orders = self.model.generate_grid(self.trade, self.config)
        OrderManager().submit_orders(orders)
