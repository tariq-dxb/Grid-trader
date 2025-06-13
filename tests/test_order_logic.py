"""
Tests for OrderManager logic, like submitting and regenerating orders.
"""

from engine.order_manager import OrderManager

def test_submit_orders(capsys):
    om = OrderManager()
    test_order = [{
        "symbol": "XAUUSD",
        "type": "sell_stop",
        "entry": 2290.0,
        "sl": 2296.0,
        "tp": 2270.0,
        "lot": 0.1,
        "status": "pending"
    }]
    om.submit_orders(test_order)
    captured = capsys.readouterr()
    assert "Submitting Order" in captured.out
