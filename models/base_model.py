from abc import ABC, abstractmethod

class BaseGridModel(ABC):
    @abstractmethod
    def generate_grid(self, base_trade, settings):
        """Generates a grid of pending orders based on base trade and settings."""
        pass
