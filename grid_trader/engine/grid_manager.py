# grid_trader/engine/grid_manager.py
import time
import pandas as pd
from typing import Dict, Any, Optional, Type, List

# These top-level imports will capture the initial 'config' object from the package
from .. import config as package_config # Alias to avoid conflict if 'config' is rebound in __main__
from ..utils.logger import get_logger
from .signal_router import SignalRouter
from .risk_manager import RiskManager
from .order_manager import OrderManager, OrderStatus, Order, OrderType
from ..models.base_model import BaseGridModel
from ..models.volatility_grid import VolatilityGridModel
from ..models.dual_grid import DualGridModel
from ..models.static_grid import StaticGridModel
from ..models.pyramid_grid import PyramidGridModel
from ..models.structure_grid import StructureGridModel
from ..models.range_grid import RangeGridModel

logger = get_logger(__name__) # Initial logger using potentially original package_config

class ActiveGrid:
    def __init__(self, grid_id: str, model_name: str, model_instance: BaseGridModel, base_params: Dict[str, Any]):
        self.grid_id: str = grid_id
        self.model_name: str = model_name
        self.model_instance: BaseGridModel = model_instance
        self.base_params: Dict[str, Any] = base_params
        self.original_base_price: float = base_params['base_price']
        self.order_ids: List[str] = []
        self.creation_time: float = time.time()
        self.is_active: bool = True
    def __repr__(self):
        return f"ActiveGrid(ID: {self.grid_id[-6:]}, Model: {self.model_name}, BasePrice: {self.original_base_price}, Orders: {len(self.order_ids)})"

class GridManager:
    def __init__(self, risk_manager: RiskManager, order_manager: OrderManager):
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.active_grids: Dict[str, ActiveGrid] = {}
        self.model_mapping: Dict[str, Type[BaseGridModel]] = {
            "VolatilityGridModel": VolatilityGridModel, "DualGridModel": DualGridModel,
            "StaticGridModel": StaticGridModel, "PyramidGridModel": PyramidGridModel,
            "StructureGridModel": StructureGridModel, "RangeGridModel": RangeGridModel,
        }
        self.slot_cooldown_tracker: Dict[str, float] = {}
        # Use package_config here as this is module level
        self.bar_duration_seconds = getattr(package_config, 'BAR_DURATION_SECONDS', 60)
        logger.info("GridManager initialized.")

    def create_new_grid(self, base_trade_params: Dict[str, Any], historical_data: pd.DataFrame) -> Optional[str]:
        if not base_trade_params or historical_data.empty: logger.error("GM: Base params/hist data missing."); return None
        if 'atr' not in base_trade_params: base_trade_params['atr'] = 0.0
        if 'base_sl' not in base_trade_params or 'base_tp' not in base_trade_params:
             current_price = base_trade_params.get('base_price', 0); atr_val = base_trade_params.get('atr', 0.001)
             direction_buy = base_trade_params.get('direction','buy').lower() == 'buy'
             default_sl = current_price - 3 * atr_val if direction_buy else current_price + 3 * atr_val
             default_tp = current_price + 3 * atr_val if direction_buy else current_price - 3 * atr_val
             base_trade_params['base_sl'] = base_trade_params.get('base_sl', default_sl if current_price > 0 and atr_val > 0 else 0)
             base_trade_params['base_tp'] = base_trade_params.get('base_tp', default_tp if current_price > 0 and atr_val > 0 else 0)

        # SignalRouter is instantiated here, will use the 'config' available in its own module's scope.
        # If __main__ patches signal_router_module.config, that will be used.
        signal_router_instance = SignalRouter(base_trade_params, historical_data)
        model_name, reason = signal_router_instance.select_grid_model()
        logger.info(f"GM: SignalRouter selected '{model_name}' for {base_trade_params['symbol']}. Reason: {reason}")

        ModelClass = self.model_mapping.get(model_name)
        if not ModelClass: logger.error(f"GM: Model class '{model_name}' not found."); return None

        # Model specific params from the config currently in scope for GridManager (could be mocked)
        model_specific_config_key = f"{model_name}_params"
        model_params = getattr(package_config if __name__ != '__main__' else config, model_specific_config_key, {}) # Use global 'config' if in __main__
        logger.debug(f"GM: Using model params for {model_name}: {model_params}")

        try: model_instance = ModelClass(base_trade_params=base_trade_params, historical_data=historical_data, risk_manager=self.risk_manager, **model_params)
        except Exception as e: logger.error(f"GM: Failed to instantiate {model_name}: {e}", exc_info=True); return None

        desired_orders_spec = model_instance.generate_grid_orders()
        if not desired_orders_spec: logger.warning(f"GM: Model {model_name} gen'd no orders."); return None

        grid_id = f"grid_{model_name.replace('Model','').lower()}_{base_trade_params['symbol']}_{int(time.time())}"
        active_grid_instance = ActiveGrid(grid_id, model_name, model_instance, base_trade_params)
        placed_order_count = 0
        for spec in desired_orders_spec:
            if not all(key in spec for key in ['symbol', 'order_type', 'entry_price', 'sl', 'tp']): logger.warning(f"GM: Spec missing keys: {spec}. Skip."); continue
            lot_size = spec.get('lot_size')
            if lot_size is None or lot_size == 0: lot_size = self.risk_manager.calculate_lot_size(symbol=spec['symbol'], entry_price=spec['entry_price'], sl_price=spec['sl'])
            if lot_size > 0:
                if not self.risk_manager.can_open_trade(spec['symbol'], lot_size, spec['entry_price']): logger.warning(f"GM: RiskManager blocked {spec['symbol']}. Skip."); continue
                order = self.order_manager.place_new_order(symbol=spec['symbol'], order_type_str=spec['order_type'], entry_price=spec['entry_price'], sl_price=spec['sl'], tp_price=spec['tp'], lot_size=lot_size, grid_id=grid_id, original_order_id_for_regen=spec.get('grid_id'))
                if order: active_grid_instance.order_ids.append(order.order_id); placed_order_count +=1
            else: logger.warning(f"GM: Lot size zero for spec: {spec}. Skip.")
        if placed_order_count > 0: self.active_grids[grid_id] = active_grid_instance; logger.info(f"GM: Created grid '{grid_id}' ({placed_order_count} orders) using {model_name}."); return grid_id
        else: logger.warning(f"GM: No orders placed for grid with {model_name}."); return None

    def process_market_data_update(self, market_data: Dict[str, Dict[str, float]]):
        if not market_data: return
        self.order_manager.check_pending_orders(market_data)
        closed_ids = self.order_manager.check_active_positions(market_data)
        stopped_orders = [self.order_manager.orders.get(oid) for oid in closed_ids if self.order_manager.orders.get(oid) and self.order_manager.orders[oid].status == OrderStatus.STOPPED_OUT]
        if stopped_orders: self._handle_regenerations(stopped_orders)
        self._check_grid_recentering(market_data)

    def _handle_regenerations(self, stopped_orders: List[Order]):
        # Use package_config here or ensure 'config' is the right one during __init__
        _current_config = package_config if __name__ != '__main__' else config
        cooldown_seconds = _current_config.DEFAULT_COOLDOWN_PERIOD_BARS * self.bar_duration_seconds
        for order in stopped_orders:
            if not order or not order.grid_id or order.grid_id not in self.active_grids: logger.debug(f"Stopped order {order.order_id if order else 'N/A'} no active grid. Skip regen."); continue
            original_slot_id = order.original_order_id
            last_event_time = self.slot_cooldown_tracker.get(original_slot_id)
            if last_event_time and (time.time() - last_event_time) < cooldown_seconds: logger.debug(f"Slot {original_slot_id} (order {order.order_id}) in cooldown."); continue
            if self.order_manager.needs_regeneration(order.order_id):
                current_attempt_for_config = self.order_manager.regeneration_counts.get(original_slot_id, 0)
                widen_key = f'REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_{current_attempt_for_config}'
                widen_factor = getattr(_current_config, widen_key, _current_config.DEFAULT_SL_TP_WIDENING_FACTOR)
                regen_params = self.order_manager.get_order_details_for_regeneration(order.order_id, widen_sltp_factor=widen_factor)
                if regen_params:
                    logger.info(f"GM: Attempting regen for slot {original_slot_id} (order {order.order_id}), next OM attempt count: {current_attempt_for_config+1}.")
                    new_lot_size = self.risk_manager.calculate_lot_size(symbol=regen_params['symbol'], entry_price=regen_params['entry_price'],sl_price=regen_params['sl_price'])
                    if new_lot_size <= 0: logger.warning(f"GM: Lot size 0 for regen slot {original_slot_id}. Abort."); self.slot_cooldown_tracker[original_slot_id] = time.time(); continue
                    regen_params['lot_size'] = new_lot_size
                    if not self.risk_manager.can_open_trade(regen_params['symbol'], new_lot_size, regen_params['entry_price']): logger.warning(f"GM: RiskManager blocked regen for slot {original_slot_id}. Abort."); self.slot_cooldown_tracker[original_slot_id] = time.time(); continue
                    new_order = self.order_manager.place_new_order(**regen_params)
                    if new_order: self.active_grids[order.grid_id].order_ids.append(new_order.order_id); logger.info(f"GM: Slot {original_slot_id} regenerated as {new_order.order_id}."); self.slot_cooldown_tracker[original_slot_id] = time.time()
                    else: logger.error(f"GM: Failed to place regen order for {original_slot_id}."); self.slot_cooldown_tracker[original_slot_id] = time.time()
            else:
                 logger.info(f"Slot {original_slot_id} (order {order.order_id}) needs no further regen.")
                 if self.order_manager.regeneration_counts.get(original_slot_id, 0) >= self.order_manager.max_regeneration_attempts: # Check OM's max attempts
                     if original_slot_id in self.slot_cooldown_tracker: del self.slot_cooldown_tracker[original_slot_id]

    def _check_grid_recentering(self, market_data: Dict[str, Dict[str, float]]): pass
    def get_active_grid_info(self, grid_id:str) -> Optional[ActiveGrid]: return self.active_grids.get(grid_id)
    def get_all_active_grids(self) -> List[ActiveGrid]: return list(self.active_grids.values())

if __name__ == '__main__':
    # --- Mock components for testing GridManager ---
    class MockRiskManagerGM:
        def __init__(self, balance=10000, leverage="1:100"): self.balance = balance
        def calculate_lot_size(self, symbol, entry_price, sl_price, risk_per_trade_usd=None): logger.debug(f"MockRMGM: Calc lots for {symbol} E:{entry_price} SL:{sl_price}"); return 0.01
        def can_open_trade(self, symbol, lot_size, entry_price): return True
        def get_symbol_config(self, symbol): return {"decimals":5, "point_value":0.00001}

    class MockSignalRouterGM:
        def __init__(self, base_trade_params, historical_data): logger.debug(f"MockSignalRouterGM init with base_params: {base_trade_params}")
        def select_grid_model(self): return "VolatilityGridModel", "MockedSelection_Volatility"

    class MockVolatilityGridModel(BaseGridModel):
        def __init__(self, base_trade_params, historical_data, risk_manager, **kwargs):
            super().__init__(base_trade_params, historical_data, risk_manager)
            logger.info(f"MockVolatilityGridModel instantiated with base: {base_trade_params.get('base_price')} and kwargs: {kwargs}")
        def generate_grid_orders(self):
            return [{'symbol': self.symbol, 'order_type': 'BUY_STOP', 'entry_price': round(self.base_price + 0.0010,5), 'sl': round(self.base_price,5), 'tp': round(self.base_price + 0.0020,5), 'lot_size': 0, 'grid_id': f"{self.symbol}_slot1"}]

    class MainConfigGM: # This is the mock config object
        DEFAULT_COOLDOWN_PERIOD_BARS = 1; BAR_DURATION_SECONDS = 0.00001 # Extremely short for test
        DEFAULT_SL_TP_WIDENING_FACTOR = 1.2
        DEFAULT_MAX_REGENERATION_ATTEMPTS = 2
        LOG_LEVEL = "DEBUG"; LOG_FILE = "test_grid_manager.log"
        VolatilityGridModel_params = {"num_levels": 2, "atr_multiplier": 0.8}
        REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_0 = 1.2
        REGEN_SLTP_WIDEN_FACTOR_ATTEMPT_1 = 1.5
        SYMBOL_SETTINGS = {"EURUSD": {"decimals": 5}} # Minimal for test
        # def get(self, key, default): return getattr(self, key, default) # Not needed if using getattr(config,...)

    import sys
    # Store original modules to restore later if necessary
    original_package_config = sys.modules.get('grid_trader.config')
    # Use getattr to safely get the 'config' attribute if the module and attribute exist
    logger_module_ref = sys.modules.get('grid_trader.utils.logger')
    original_util_logger_config = getattr(logger_module_ref, 'config', None) if logger_module_ref else None

    om_module_ref = sys.modules.get('grid_trader.engine.order_manager')
    original_om_config = getattr(om_module_ref, 'config', None) if om_module_ref else None

    sr_module_ref = sys.modules.get('grid_trader.engine.signal_router')
    original_sr_config = getattr(sr_module_ref, 'config', None) if sr_module_ref else None

    # Create instance of mock config
    mock_config_instance = MainConfigGM()

    # Apply mock config to sys.modules so new imports of 'grid_trader.config' get the mock
    sys.modules['grid_trader.config'] = mock_config_instance

    # Rebind 'config' in modules that have already imported it using 'from .. import config'
    # This ensures they use the mock_config_instance for this run.
    # Current module (grid_manager.py)
    config = mock_config_instance # Rebinds the 'package_config' alias effectively for __main__
                                 # and any direct 'config.' usage in global scope of this file.
                                 # Note: GridManager class itself uses 'package_config' for its module-level defaults.
                                 # For __init__ and methods, it should use the instance's config reference.

    import grid_trader.utils.logger # Ensure module is loaded
    grid_trader.utils.logger.config = mock_config_instance

    import grid_trader.engine.order_manager # Ensure module is loaded
    grid_trader.engine.order_manager.config = mock_config_instance

    import grid_trader.engine.signal_router # Ensure module is loaded
    grid_trader.engine.signal_router.config = mock_config_instance

    # Re-initialize logger for THIS __main__ scope, ensuring it uses the mocked config
    logger = get_logger(__name__)

    # Save original SignalRouter (already imported at top of grid_manager.py) and mock it
    OriginalSignalRouterForGMTest = SignalRouter
    SignalRouter = MockSignalRouterGM # Rebind global SignalRouter in grid_manager's module scope to the mock

    rm_gm = MockRiskManagerGM()
    om_gm = OrderManager(rm_gm) # OrderManager will now use the mocked config via om_module.config

    gm = GridManager(rm_gm, om_gm)
    # GridManager's __init__ would have used whatever 'package_config' was at module load time for bar_duration_seconds
    # For tests, explicitly set it if it depends on the mock config
    gm.bar_duration_seconds = mock_config_instance.BAR_DURATION_SECONDS

    gm.model_mapping["VolatilityGridModel"] = MockVolatilityGridModel

    logger.info("--- Testing GridManager: create_new_grid ---")
    sample_hist_data = pd.DataFrame({'Close': [1.1100, 1.1120], 'High': [1.1110,1.1121], 'Low': [1.1090,1.1119], 'ATR_14':[0.0010,0.0010]})
    sample_hist_data.index = pd.to_datetime(['2023-01-15 10:00:00', '2023-01-15 10:01:00'])

    _base_price_gm_test = 1.1100; _atr_gm_test = 0.0010
    _decimals_gm_test = mock_config_instance.SYMBOL_SETTINGS.get('EURUSD',{}).get('decimals', 5)
    base_params = {
        'symbol': 'EURUSD', 'direction': 'buy', 'base_price': _base_price_gm_test, 'atr': _atr_gm_test,
        'base_sl': round(_base_price_gm_test - 3 * _atr_gm_test, _decimals_gm_test),
        'base_tp': round(_base_price_gm_test + 3 * _atr_gm_test, _decimals_gm_test),
        'base_size_lots': 0.01
    }
    grid_id = gm.create_new_grid(base_params, sample_hist_data)
    assert grid_id is not None, "Grid creation failed"
    assert grid_id in gm.active_grids, "Grid not added to active_grids"
    active_grid_obj = gm.active_grids[grid_id]
    assert len(active_grid_obj.order_ids) > 0, "No orders placed"
    logger.info(f"Created grid: {active_grid_obj}")

    logger.info("--- Testing GridManager: _handle_regenerations ---")
    if active_grid_obj.order_ids:
        first_order_id = active_grid_obj.order_ids[0]
        test_order = om_gm.orders.get(first_order_id)
        if test_order:
            test_order.status = OrderStatus.STOPPED_OUT; test_order.close_time = time.time()
            om_gm.regeneration_counts[test_order.original_order_id] = 0
            gm.slot_cooldown_tracker[test_order.original_order_id] = time.time() - (mock_config_instance.DEFAULT_COOLDOWN_PERIOD_BARS * gm.bar_duration_seconds * 2)
            logger.info(f"Manually set {first_order_id} to STOPPED_OUT for 1st regen test.")
            gm._handle_regenerations([test_order])

            regen_1_order = None
            for oid, o in om_gm.orders.items():
                if o.original_order_id == test_order.original_order_id and o.regeneration_attempts == 1: regen_1_order = o; break
            assert regen_1_order is not None, "1st regeneration failed."
            logger.info(f"Found 1st regen: {regen_1_order}")

            regen_1_order.status = OrderStatus.STOPPED_OUT; regen_1_order.close_time = time.time()
            gm.slot_cooldown_tracker[regen_1_order.original_order_id] = time.time() - (mock_config_instance.DEFAULT_COOLDOWN_PERIOD_BARS * gm.bar_duration_seconds * 2)
            logger.info(f"Manually set {regen_1_order.order_id} to STOPPED_OUT for 2nd regen test.")
            gm._handle_regenerations([regen_1_order])

            regen_2_order = None
            for oid, o in om_gm.orders.items():
                if o.original_order_id == test_order.original_order_id and o.regeneration_attempts == 2: regen_2_order = o; break
            assert regen_2_order is not None, "2nd regeneration failed."
            logger.info(f"Found 2nd regen: {regen_2_order}")

            regen_2_order.status = OrderStatus.STOPPED_OUT; regen_2_order.close_time = time.time()
            gm.slot_cooldown_tracker[regen_2_order.original_order_id] = time.time() - (mock_config_instance.DEFAULT_COOLDOWN_PERIOD_BARS * gm.bar_duration_seconds * 2)
            logger.info(f"Manually set {regen_2_order.order_id} to STOPPED_OUT for 3rd regen test (should not regen).")
            gm._handle_regenerations([regen_2_order])

            found_regen_3 = False
            for oid, o in om_gm.orders.items():
                if o.original_order_id == test_order.original_order_id and o.regeneration_attempts == 3: found_regen_3 = True; break
            assert not found_regen_3, "3rd regeneration occurred but max attempts should be 2."

    SignalRouter = OriginalSignalRouterForGMTest
    # Restore original configs if they were stored
    if original_package_config: sys.modules['grid_trader.config'] = original_package_config
    if original_util_logger_config: grid_trader.utils.logger.config = original_util_logger_config
    if original_om_config: grid_trader.engine.order_manager.config = original_om_config
    if original_sr_config: grid_trader.engine.signal_router.config = original_sr_config

    logger.info("GridManager tests complete.")
