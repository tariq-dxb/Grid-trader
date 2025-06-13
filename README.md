We are building a modular Python package for an adaptive pending order engine for Forex and Gold CFD trading. The system must support the following:
________________________________________
✅ Core Functionality
1.	Accept a base trade with:
    symbol, direction ("buy"/"sell"), base_price, base_sl, base_tp, base_size, atr
2.	Dynamically generate one of multiple pending order grid strategies:
    Volatility Grid: opposite-direction pending orders spaced by ATR
  	Dual-Sided Grid: both breakout and reversal orders
  	Pyramiding Grid: same-direction Buy Stops/Sell Stops above entry
  	Static Grid: evenly spaced from base_price to SL
  	Structure-Based Grid: align to swing highs/lows or fib levels
  	Range Grid: tight, bi-directional grid inside consolidation zones
4.	Choose grid model using filters:
    o	Volatility: ATR vs rolling median
    o	Trend: EMA slope, ADX
    o	Range: Bollinger Band width
    o	Price structure: swing level proximity
5.	Generate pending orders with:
    o	Entry, SL, TP
    o	Lot size computed by fixed $ risk per trade (e.g. $10/order)
    o	Respect margin and leverage cap (e.g. 1:100 max exposure)
    o	Output orders in a standard dictionary format
6.	Implement smart reactivity and regeneration:
    o	Recenter grid if price deviates from base > X% or ATR
    o	Regenerate only orders that were triggered + stopped out
    o	Limit regeneration per order (max attempts = N)
    o	Add cooldown (delay N bars before retrying)
    o	Allow optional SL/TP widening on retry
7.	Include support modules:
    o	RiskManager: ensures lot size, leverage, and exposure constraints
    o	OrderManager: manages order status, fills, regeneration
    o	SignalRouter: evaluates filters to select grid model
8.	Design as a Python package with the following structure:

grid_trader/
│
├── main.py                   # Entry point (example run)
├── config.py                 # Global parameters
│
├── models/
│   ├── base_model.py         # Base class for grid models
│   ├── volatility_grid.py    # Volatility Grid
│   ├── dual_grid.py          # Dual-Sided Grid
│   ├── static_grid.py        # Static Grid
│   ├── pyramid_grid.py       # Pyramiding Grid
│   ├── structure_grid.py     # Structure-Based Grid
│   ├── range_grid.py         # Range-Reactive Grid
│
├── engine/
│   ├── signal_router.py      # Chooses grid model based on filters
│   ├── risk_manager.py       # Handles lot sizing, leverage rules
│   ├── order_manager.py      # Handles order status, regeneration
│   ├── grid_manager.py       # Central grid controller
│
├── utils/
│   ├── indicators.py         # EMA, ADX, ATR, BB
│   ├── price_structure.py    # Detect swing highs/lows
│   ├── logger.py             # Logging/debugging
│
└── tests/
    ├── test_grid_models.py
    ├── test_order_logic.py
    ├── test_risk_engine.py
