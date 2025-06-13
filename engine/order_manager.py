class OrderManager:
    def submit_orders(self, orders):
        """
        Submits orders to execution engine (simulated here).

        Args:
            orders (List[dict]): List of order dictionaries
        """
        for order in orders:
            print(f"Submitting Order: {order}")
