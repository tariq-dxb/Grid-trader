from models.base_model import BaseGridModel

class DualSidedGrid(BaseGridModel):
    def generate_grid(self, base_trade, settings):
        """
        Generates both breakout and reversal pending orders.

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
            # Buy breakout
            entry_buy = base_trade["base_price"] + step * (i + 1)
            sl_buy = entry_buy - base_trade["atr"]
            tp_buy = entry_buy + settings["rr_ratio"] * base_trade["atr"]
            lot = round(risk / abs(entry_buy - sl_buy), 2)

            orders.append({
                "symbol": base_trade["symbol"],
                "type": "buy_stop",
                "entry": round(entry_buy, 2),
                "sl": round(sl_buy, 2),
                "tp": round(tp_buy, 2),
                "lot": lot,
                "grid_level": i + 1,
                "attempt": 1,
                "max_attempts": settings["max_attempts"],
                "cooldown_bars": settings["cooldown_bars"],
                "status": "pending"
            })

            # Sell fade
            entry_sell = base_trade["base_price"] - step * (i + 1)
            sl_sell = entry_sell + base_trade["atr"]
            tp_sell = entry_sell - settings["rr_ratio"] * base_trade["atr"]
            lot = round(risk / abs(entry_sell - sl_sell), 2)

            orders.append({
                "symbol": base_trade["symbol"],
                "type": "sell_stop",
                "entry": round(entry_sell, 2),
                "sl": round(sl_sell, 2),
                "tp": round(tp_sell, 2),
                "lot": lot,
                "grid_level": i + 1,
                "attempt": 1,
                "max_attempts": settings["max_attempts"],
                "cooldown_bars": settings["cooldown_bars"],
                "status": "pending"
            })

        return orders
