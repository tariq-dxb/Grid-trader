from models.base_model import BaseGridModel

class PyramidingGrid(BaseGridModel):
    def generate_grid(self, base_trade, settings):
        """
        Generates same-direction pending orders (trend-following pyramids).

        Args:
            base_trade (dict): Base trade signal
            settings (dict): Model parameters

        Returns:
            List[dict]: List of pending orders
        """
        orders = []
        step = settings["atr_multiplier"] * base_trade["atr"]
        risk = settings["risk_per_order"]

        for i in range(settings["order_count"]):
            if base_trade["direction"] == "buy":
                entry = base_trade["base_price"] + step * (i + 1)
                sl = base_trade["base_sl"]
                tp = base_trade["base_tp"]
                order_type = "buy_stop"
            else:
                entry = base_trade["base_price"] - step * (i + 1)
                sl = base_trade["base_sl"]
                tp = base_trade["base_tp"]
                order_type = "sell_stop"

            lot = round(risk / abs(entry - sl), 2)

            orders.append({
                "symbol": base_trade["symbol"],
                "type": order_type,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp": round(tp, 2),
                "lot": lot,
                "grid_level": i + 1,
                "attempt": 1,
                "max_attempts": settings["max_attempts"],
                "cooldown_bars": settings["cooldown_bars"],
                "status": "pending"
            })

        return orders
