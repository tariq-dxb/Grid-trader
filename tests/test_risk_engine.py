"""
Tests for future RiskManager module (e.g. leverage and lot size validation).
"""

def test_lot_size_computation():
    risk = 10.0
    sl_distance = 5.0
    expected_lot = round(risk / sl_distance, 2)
    assert expected_lot == 2.0
