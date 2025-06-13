# grid_trader/utils/indicators.py
import pandas as pd
import numpy as np

def calculate_ema(data: pd.DataFrame, period: int) -> pd.Series:
    """Calculates the Exponential Moving Average (EMA)."""
    if f'EMA_{period}' in data.columns:
        return data[f'EMA_{period}']
    return data['Close'].ewm(span=period, adjust=False).mean()

def calculate_atr(data: pd.DataFrame, period: int) -> pd.Series:
    """Calculates the Average True Range (ATR)."""
    if f'ATR_{period}' in data.columns:
        return data[f'ATR_{period}']

    high_low = data['High'] - data['Low']
    high_close_prev = np.abs(data['High'] - data['Close'].shift(1))
    low_close_prev = np.abs(data['Low'] - data['Close'].shift(1))

    tr = pd.DataFrame({'hl': high_low, 'hcp': high_close_prev, 'lcp': low_close_prev}).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean() # Wilder's smoothing for ATR
    return atr

def calculate_adx(data: pd.DataFrame, period: int) -> pd.DataFrame:
    """
    Calculates the Average Directional Index (ADX), +DI, and -DI.
    Returns a DataFrame with columns: '+DI', '-DI', 'ADX'.
    """
    if f'ADX_{period}' in data.columns and f'+DI_{period}' in data.columns and f'-DI_{period}' in data.columns:
        return data[[f'+DI_{period}', f'-DI_{period}', f'ADX_{period}']]

    high = data['High']
    low = data['Low']
    close = data['Close']

    # Calculate +DM, -DM
    move_up = high - high.shift(1)
    move_down = low.shift(1) - low

    plus_dm = pd.Series(np.where((move_up > move_down) & (move_up > 0), move_up, 0.0), index=data.index)
    minus_dm = pd.Series(np.where((move_down > move_up) & (move_down > 0), move_down, 0.0), index=data.index)

    # Smoothed +DM, -DM, TR
    # ATR calculation is slightly different for ADX (Wilder's smoothing)
    tr1 = pd.DataFrame({'hl': high - low, 'hcp': abs(high - close.shift(1)), 'lcp': abs(low - close.shift(1))}).max(axis=1)
    atr_adx = tr1.ewm(alpha=1/period, adjust=False).mean()

    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_adx)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_adx)

    # ADX
    # Handle division by zero for dx if (plus_di + minus_di) is zero
    dx_numerator = abs(plus_di - minus_di)
    dx_denominator = plus_di + minus_di
    # Initialize dx with zeros or a suitable value for non-directional periods
    dx = pd.Series(0.0, index=data.index)
    # Calculate dx only where denominator is not zero
    dx = dx.where(dx_denominator == 0, 100 * (dx_numerator / dx_denominator))
    # Some implementations might set dx to 100 if denominator is 0 but numerator is not,
    # but for ADX, if no directional movement, ADX should be low. Setting dx to 0 is safer.

    adx = dx.ewm(alpha=1/period, adjust=False).mean()

    return pd.DataFrame({f'+DI_{period}': plus_di, f'-DI_{period}': minus_di, f'ADX_{period}': adx})


def calculate_bollinger_bands(data: pd.DataFrame, period: int, std_dev: int) -> pd.DataFrame:
    """Calculates Bollinger Bands."""
    if f'BB_Mid_{period}_{std_dev}' in data.columns and \
       f'BB_Upper_{period}_{std_dev}' in data.columns and \
       f'BB_Lower_{period}_{std_dev}' in data.columns:
        return data[[f'BB_Upper_{period}_{std_dev}', f'BB_Mid_{period}_{std_dev}', f'BB_Lower_{period}_{std_dev}']]

    close = data['Close']
    middle_band = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper_band = middle_band + (std * std_dev)
    lower_band = middle_band - (std * std_dev)

    return pd.DataFrame({
        f'BB_Upper_{period}_{std_dev}': upper_band,
        f'BB_Mid_{period}_{std_dev}': middle_band,
        f'BB_Lower_{period}_{std_dev}': lower_band
    })

# Example Usage (optional, for testing during development)
if __name__ == '__main__':
    # Create sample data
    sample_data = {
        'High': [1.10, 1.11, 1.09, 1.12, 1.13, 1.11, 1.14, 1.15, 1.13, 1.16, 1.17, 1.15, 1.18, 1.19, 1.20],
        'Low':  [1.08, 1.09, 1.07, 1.10, 1.11, 1.09, 1.12, 1.13, 1.11, 1.14, 1.15, 1.13, 1.16, 1.17, 1.18],
        'Close':[1.09, 1.10, 1.08, 1.11, 1.12, 1.10, 1.13, 1.14, 1.12, 1.15, 1.16, 1.14, 1.17, 1.18, 1.19]
    }
    df = pd.DataFrame(sample_data)
    df.index = pd.to_datetime([f'2023-01-{i:02d}' for i in range(1, 16)])


    # EMA
    df['EMA_10'] = calculate_ema(df, period=10)
    print("EMA_10:\n", df['EMA_10'].tail())

    # ATR
    df['ATR_14'] = calculate_atr(df, period=14)
    print("\nATR_14:\n", df['ATR_14'].tail())

    # ADX
    adx_results = calculate_adx(df, period=14)
    df = pd.concat([df, adx_results], axis=1)
    print("\nADX_14, +DI_14, -DI_14:\n", df[['+DI_14', '-DI_14', 'ADX_14']].tail())

    # Bollinger Bands
    bb_results = calculate_bollinger_bands(df, period=10, std_dev=2)
    df = pd.concat([df, bb_results], axis=1)
    print("\nBollinger Bands (10, 2):\n", df[[f'BB_Upper_10_2', f'BB_Mid_10_2', f'BB_Lower_10_2']].tail())

    print("\nFinal DataFrame Head:\n", df.head())
    print("\nFinal DataFrame Tail:\n", df.tail())
