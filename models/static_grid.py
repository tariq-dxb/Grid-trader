from models.base_model import BaseGridModel

class StaticGrid(BaseGridModel):
    def generate_grid(self, base_trade, settings):
        """
        Generates evenly spaced pending orders between base_price and SL.

        Args:
            base_trade (dict): Base trade signal
            settings (dict): Model parameters

        Returns:
            List[dict]: List of pending orders
        """
        orders = []
        direction = base_trade["direction"]
        start = base_trade["base_price"]
        end = base_trade["base_sl"]
        interval = (start - end) / settings["order_count"] if direction == "buy" else (end - start) / settings["order_count"]
        risk = settings["risk_per_order"]

        for i in range(settings["order_count"]):
            if direction == "buy":
                entry = base_trade["base_price"] - interval * (i + 1)
                order_type = "sell_stop"
                sl = base_trade["base_tp"]
                tp = base_trade["base_sl"]
            else:
                entry = base_trade["base_price"] + interval * (i + 1)
                order_type = "buy_stop"
                sl = base_trade["base_tp"]
                tp = base_trade["base_sl"]

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
