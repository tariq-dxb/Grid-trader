from models.base_model import BaseGridModel

class VolatilityGrid(BaseGridModel):
    def generate_grid(self, base_trade, settings):
        """
        Generate opposite-direction pending orders spaced by ATR.

        Args:
            base_trade (dict): Base trade signal
            settings (dict): Model parameters

        Returns:
            List[dict]: List of pending orders
        """
        orders = []
        step = settings["atr_multiplier"] * base_trade["atr"]
        direction = base_trade["direction"]
        risk = settings["risk_per_order"]

        for i in range(settings["order_count"]):
            if direction == "buy":
                entry = base_trade["base_price"] - step * (i + 1)
                sl = entry + base_trade["atr"]
                tp = entry - settings["rr_ratio"] * base_trade["atr"]
                order_type = "sell_stop"
            else:
                entry = base_trade["base_price"] + step * (i + 1)
                sl = entry - base_trade["atr"]
                tp = entry + settings["rr_ratio"] * base_trade["atr"]
                order_type = "buy_stop"

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
