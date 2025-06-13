# grid_trader/utils/price_structure.py
import pandas as pd
import numpy as np
from scipy.signal import find_peaks

def find_swing_highs(data: pd.DataFrame, n_bars: int = 2) -> pd.Series:
    """
    Identifies swing highs in the price data.
    A swing high is a peak where the high is greater than the highs of
    n_bars to the left and n_bars to the right.

    Args:
        data (pd.DataFrame): DataFrame with 'High' column.
        n_bars (int): Number of bars to compare on each side. (Must be >= 1)

    Returns:
        pd.Series: Boolean series (indexed like `data`), True where a swing high is detected.
    """
    if 'High' not in data.columns:
        raise ValueError("DataFrame must contain 'High' column.")
    if n_bars < 1:
        raise ValueError("n_bars must be at least 1.")

    highs = data['High']
    is_swing_high = pd.Series(False, index=data.index)

    # Find candidate peaks using scipy.signal.find_peaks
    # distance ensures peaks are separated. We use distance=1 as we will do a precise N-bar check.
    candidate_indices, _ = find_peaks(highs, distance=1)

    for peak_idx in candidate_indices:
        if peak_idx < n_bars or peak_idx >= len(highs) - n_bars:
            # Not enough bars on one or both sides for a valid swing high
            continue

        current_high = highs.iloc[peak_idx]

        is_highest_left = True
        for i in range(1, n_bars + 1):
            if highs.iloc[peak_idx - i] >= current_high:
                is_highest_left = False
                break

        if not is_highest_left:
            continue

        is_highest_right = True
        for i in range(1, n_bars + 1):
            if highs.iloc[peak_idx + i] >= current_high:
                is_highest_right = False
                break

        if is_highest_right:
            is_swing_high.iloc[peak_idx] = True

    return is_swing_high

def find_swing_lows(data: pd.DataFrame, n_bars: int = 2) -> pd.Series:
    """
    Identifies swing lows in the price data.
    A swing low is a trough where the low is less than the lows of
    n_bars to the left and n_bars to the right.

    Args:
        data (pd.DataFrame): DataFrame with 'Low' column.
        n_bars (int): Number of bars to compare on each side. (Must be >= 1)

    Returns:
        pd.Series: Boolean series (indexed like `data`), True where a swing low is detected.
    """
    if 'Low' not in data.columns:
        raise ValueError("DataFrame must contain 'Low' column.")
    if n_bars < 1:
        raise ValueError("n_bars must be at least 1.")

    lows = data['Low']
    is_swing_low = pd.Series(False, index=data.index)

    candidate_indices, _ = find_peaks(-lows, distance=1)

    for peak_idx in candidate_indices:
        if peak_idx < n_bars or peak_idx >= len(lows) - n_bars:
            continue

        current_low = lows.iloc[peak_idx]

        is_lowest_left = True
        for i in range(1, n_bars + 1):
            if lows.iloc[peak_idx - i] <= current_low:
                is_lowest_left = False
                break

        if not is_lowest_left:
            continue

        is_lowest_right = True
        for i in range(1, n_bars + 1):
            if lows.iloc[peak_idx + i] <= current_low:
                is_lowest_right = False
                break

        if is_lowest_right:
            is_swing_low.iloc[peak_idx] = True

    return is_swing_low

# Example Usage (optional, for testing during development)
if __name__ == '__main__':
    sample_data = {
        'High': [1.10, 1.11, 1.09, 1.12, 1.08, 1.10, 1.11, 1.15, 1.14, 1.13, 1.16, 1.12, 1.18, 1.17, 1.16, 1.19, 1.18],
        'Low':  [1.08, 1.07, 1.09, 1.10, 1.06, 1.05, 1.07, 1.12, 1.11, 1.09, 1.10, 1.11, 1.15, 1.14, 1.13, 1.17, 1.15],
        'Close':[1.09, 1.08, 1.08, 1.11, 1.07, 1.06, 1.10, 1.13, 1.12, 1.10, 1.15, 1.11, 1.16, 1.15, 1.14, 1.18, 1.16]
    }
    df = pd.DataFrame(sample_data)

    n = 2
    df['SwingHigh_N2'] = find_swing_highs(df, n_bars=n)
    df['SwingLow_N2'] = find_swing_lows(df, n_bars=n)

    n_1 = 1
    df[f'SwingHigh_N{n_1}'] = find_swing_highs(df, n_bars=n_1)
    df[f'SwingLow_N{n_1}'] = find_swing_lows(df, n_bars=n_1)

    print(f"--- Swing Highs (n={n}) ---")
    print(df[df['SwingHigh_N2']][['High', 'SwingHigh_N2']])

    print(f"\n--- Swing Lows (n={n}) ---")
    print(df[df['SwingLow_N2']][['Low', 'SwingLow_N2']])

    print(f"\n--- Swing Highs (n={n_1}) ---")
    print(df[df[f'SwingHigh_N{n_1}']][['High', f'SwingHigh_N{n_1}']])

    print(f"\n--- Swing Lows (n={n_1}) ---")
    print(df[df[f'SwingLow_N{n_1}']][['Low', f'SwingLow_N{n_1}']])

    data_edge = {'High': [1,2,3,2,1], 'Low': [5,4,3,4,5]}
    df_edge = pd.DataFrame(data_edge)
    df_edge['SH_N1'] = find_swing_highs(df_edge, 1)
    df_edge['SL_N1'] = find_swing_lows(df_edge, 1)
    print("\n--- Edge Case N=1 ---")
    print(df_edge)

    df_edge['SH_N2'] = find_swing_highs(df_edge, 2)
    df_edge['SL_N2'] = find_swing_lows(df_edge, 2)
    print("\n--- Edge Case N=2 (expected empty for SH_N2, SL_N2) ---")
    # Printing only rows where SH_N2 or SL_N2 is True
    print(df_edge[df_edge['SH_N2'] | df_edge['SL_N2']])
