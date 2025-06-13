class SignalRouter:
    def choose_model(self, trade_info) -> str:
        """
        Choose best grid model based on market filters.

        Args:
            trade_info (dict): Contains ATR, trend data, etc.

        Returns:
            str: Model name
        """
        atr = trade_info.get("atr", 0)
        price = trade_info.get("base_price", 0)
        ema = trade_info.get("ema", 0)
        adx = trade_info.get("adx", 0)

        if adx > 25 and price > ema:
            return "pyramiding"
        elif atr > 2.0:
            return "volatility"
        else:
            return "static"
