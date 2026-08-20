"""
Volatility Indicators Module

This module provides volatility-based technical indicators:
- Average True Range (ATR)
- Bollinger Bands
- Standard Deviation
- Historical Volatility

Used for measuring price volatility and setting stop-loss levels.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union, Tuple
import logging

logger = logging.getLogger(__name__)


class VolatilityIndicators:
    """
    Volatility Indicators Calculator
    
    Provides methods to calculate various volatility indicators
    from OHLCV data.
    """
    
    @staticmethod
    def atr(high: Union[List[float], pd.Series, np.ndarray],
            low: Union[List[float], pd.Series, np.ndarray],
            close: Union[List[float], pd.Series, np.ndarray],
            period: int = 14) -> np.ndarray:
        """
        Calculate Average True Range (ATR)
        
        ATR measures market volatility by decomposing the entire range
        of an asset price for that period.
        
        Formula:
            True Range = max(High - Low, |High - Previous Close|, |Low - Previous Close|)
            ATR = SMA(True Range, period)
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: Number of periods (default: 14)
            
        Returns:
            numpy array with ATR values
            
        Interpretation:
            Higher ATR = Higher volatility
            Lower ATR = Lower volatility
            Used for setting stop-loss levels
        """
        try:
            # Convert to numpy arrays
            if isinstance(high, list):
                high = np.array(high)
            elif isinstance(high, pd.Series):
                high = high.values
                
            if isinstance(low, list):
                low = np.array(low)
            elif isinstance(low, pd.Series):
                low = low.values
                
            if isinstance(close, list):
                close = np.array(close)
            elif isinstance(close, pd.Series):
                close = close.values
            
            if len(close) < period + 1:
                logger.warning(f"Insufficient data for ATR calculation. Need {period + 1}, got {len(close)}")
                return np.full(len(close), np.nan)
            
            # Calculate True Range
            tr = np.full(len(close), np.nan)
            
            for i in range(1, len(close)):
                hl = high[i] - low[i]
                hc = abs(high[i] - close[i-1])
                lc = abs(low[i] - close[i-1])
                tr[i] = max(hl, hc, lc)
            
            # Calculate ATR (smoothed moving average of TR)
            atr = np.full(len(close), np.nan)
            
            # First ATR is simple average
            atr[period] = np.mean(tr[1:period + 1])
            
            # Subsequent ATRs use smoothing
            for i in range(period + 1, len(close)):
                atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            
            return atr
            
        except Exception as e:
            logger.error(f"Error calculating ATR: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def bollinger_bands(data: Union[List[float], pd.Series, np.ndarray],
                       period: int = 20,
                       std_dev: float = 2.0) -> Dict[str, np.ndarray]:
        """
        Calculate Bollinger Bands
        
        Bollinger Bands consist of a middle band (SMA) and two outer bands
        (standard deviations away from the middle band).
        
        Formula:
            Middle Band = SMA(Close, period)
            Upper Band = Middle Band + (std_dev * Standard Deviation)
            Lower Band = Middle Band - (std_dev * Standard Deviation)
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods (default: 20)
            std_dev: Number of standard deviations (default: 2.0)
            
        Returns:
            Dictionary with 'upper', 'middle', 'lower', 'bandwidth' arrays
            
        Interpretation:
            Price near upper band: Overbought
            Price near lower band: Oversold
            Bands squeeze: Low volatility (potential breakout)
            Bands expand: High volatility
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period:
                logger.warning(f"Insufficient data for Bollinger Bands. Need {period}, got {len(data)}")
                return {
                    'upper': np.full(len(data), np.nan),
                    'middle': np.full(len(data), np.nan),
                    'lower': np.full(len(data), np.nan),
                    'bandwidth': np.full(len(data), np.nan)
                }
            
            # Calculate middle band (SMA)
            middle = pd.Series(data).rolling(window=period, min_periods=period).mean().values
            
            # Calculate standard deviation
            std = pd.Series(data).rolling(window=period, min_periods=period).std().values
            
            # Calculate upper and lower bands
            upper = middle + (std_dev * std)
            lower = middle - (std_dev * std)
            
            # Calculate bandwidth (measure of volatility)
            bandwidth = np.where(middle != 0, (upper - lower) / middle * 100, np.nan)
            
            return {
                'upper': upper,
                'middle': middle,
                'lower': lower,
                'bandwidth': bandwidth
            }
            
        except Exception as e:
            logger.error(f"Error calculating Bollinger Bands: {str(e)}")
            return {
                'upper': np.full(len(data), np.nan),
                'middle': np.full(len(data), np.nan),
                'lower': np.full(len(data), np.nan),
                'bandwidth': np.full(len(data), np.nan)
            }
    
    @staticmethod
    def standard_deviation(data: Union[List[float], pd.Series, np.ndarray],
                          period: int = 20) -> np.ndarray:
        """
        Calculate rolling Standard Deviation
        
        Standard deviation measures the dispersion of prices from the mean.
        
        Args:
            data: Price data (typically close prices)
            period: Number of periods (default: 20)
            
        Returns:
            numpy array with standard deviation values
            
        Interpretation:
            Higher std dev = Higher volatility
            Lower std dev = Lower volatility
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period:
                logger.warning(f"Insufficient data for Std Dev calculation. Need {period}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate rolling standard deviation
            std = pd.Series(data).rolling(window=period, min_periods=period).std().values
            
            return std
            
        except Exception as e:
            logger.error(f"Error calculating Standard Deviation: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def historical_volatility(data: Union[List[float], pd.Series, np.ndarray],
                             period: int = 20,
                             trading_days: int = 252) -> np.ndarray:
        """
        Calculate Historical Volatility (annualized)
        
        Historical volatility measures the standard deviation of logarithmic
        returns, annualized.
        
        Formula:
            Log Returns = ln(Price_t / Price_t-1)
            HV = Std Dev(Log Returns) * sqrt(trading_days)
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods for calculation (default: 20)
            trading_days: Number of trading days per year (default: 252)
            
        Returns:
            numpy array with annualized volatility (as percentage)
            
        Interpretation:
            Higher HV = Higher risk/volatility
            Lower HV = Lower risk/volatility
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period + 1:
                logger.warning(f"Insufficient data for HV calculation. Need {period + 1}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate log returns
            log_returns = np.log(data[1:] / data[:-1])
            
            # Pad with NaN at the beginning
            log_returns = np.concatenate([[np.nan], log_returns])
            
            # Calculate rolling standard deviation of log returns
            std_log_returns = pd.Series(log_returns).rolling(
                window=period, 
                min_periods=period
            ).std().values
            
            # Annualize the volatility
            hv = std_log_returns * np.sqrt(trading_days) * 100  # Convert to percentage
            
            return hv
            
        except Exception as e:
            logger.error(f"Error calculating Historical Volatility: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def keltner_channels(high: Union[List[float], pd.Series, np.ndarray],
                        low: Union[List[float], pd.Series, np.ndarray],
                        close: Union[List[float], pd.Series, np.ndarray],
                        period: int = 20,
                        atr_period: int = 10,
                        multiplier: float = 2.0) -> Dict[str, np.ndarray]:
        """
        Calculate Keltner Channels
        
        Keltner Channels use ATR instead of standard deviation for bands.
        
        Formula:
            Middle Line = EMA(Close, period)
            Upper Channel = Middle Line + (multiplier * ATR)
            Lower Channel = Middle Line - (multiplier * ATR)
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: EMA period (default: 20)
            atr_period: ATR period (default: 10)
            multiplier: ATR multiplier (default: 2.0)
            
        Returns:
            Dictionary with 'upper', 'middle', 'lower' arrays
        """
        try:
            if isinstance(close, list):
                close = np.array(close)
            elif isinstance(close, pd.Series):
                close = close.values
            
            if len(close) < max(period, atr_period):
                logger.warning(f"Insufficient data for Keltner Channels")
                return {
                    'upper': np.full(len(close), np.nan),
                    'middle': np.full(len(close), np.nan),
                    'lower': np.full(len(close), np.nan)
                }
            
            # Calculate middle line (EMA)
            middle = pd.Series(close).ewm(span=period, adjust=False).mean().values
            
            # Calculate ATR
            atr = VolatilityIndicators.atr(high, low, close, atr_period)
            
            # Calculate upper and lower channels
            upper = middle + (multiplier * atr)
            lower = middle - (multiplier * atr)
            
            return {
                'upper': upper,
                'middle': middle,
                'lower': lower
            }
            
        except Exception as e:
            logger.error(f"Error calculating Keltner Channels: {str(e)}")
            return {
                'upper': np.full(len(close), np.nan),
                'middle': np.full(len(close), np.nan),
                'lower': np.full(len(close), np.nan)
            }
    
    @staticmethod
    def get_bollinger_signals(close: Union[List[float], pd.Series, np.ndarray],
                             bb_data: Dict[str, np.ndarray]) -> Dict[str, any]:
        """
        Generate trading signals from Bollinger Bands
        
        Args:
            close: Close prices
            bb_data: Bollinger Bands data
            
        Returns:
            Dictionary with current position and signals
        """
        try:
            if isinstance(close, list):
                close = np.array(close)
            elif isinstance(close, pd.Series):
                close = close.values
            
            upper = bb_data['upper']
            middle = bb_data['middle']
            lower = bb_data['lower']
            bandwidth = bb_data['bandwidth']
            
            # Get last valid values
            valid_indices = ~(np.isnan(close) | np.isnan(upper) | np.isnan(lower))
            
            if not np.any(valid_indices):
                return {
                    'current_position': 'unknown',
                    'bandwidth': None,
                    'squeeze': False,
                    'upper_touches': [],
                    'lower_touches': []
                }
            
            last_idx = np.where(valid_indices)[0][-1]
            
            current_close = close[last_idx]
            current_upper = upper[last_idx]
            current_middle = middle[last_idx]
            current_lower = lower[last_idx]
            current_bandwidth = bandwidth[last_idx]
            
            # Determine position
            if current_close >= current_upper:
                position = 'above_upper'
            elif current_close <= current_lower:
                position = 'below_lower'
            elif current_close > current_middle:
                position = 'above_middle'
            else:
                position = 'below_middle'
            
            # Detect squeeze (low volatility)
            valid_bandwidth = bandwidth[~np.isnan(bandwidth)]
            if len(valid_bandwidth) > 0:
                avg_bandwidth = np.mean(valid_bandwidth[-20:]) if len(valid_bandwidth) >= 20 else np.mean(valid_bandwidth)
                squeeze = bool(current_bandwidth < avg_bandwidth * 0.7)
            else:
                squeeze = False
            
            # Find touches
            upper_touches = np.where(close >= upper * 0.99)[0].tolist()
            lower_touches = np.where(close <= lower * 1.01)[0].tolist()
            
            return {
                'current_position': position,
                'bandwidth': float(current_bandwidth) if not np.isnan(current_bandwidth) else None,
                'squeeze': squeeze,
                'upper_touches': upper_touches[-5:] if len(upper_touches) > 5 else upper_touches,
                'lower_touches': lower_touches[-5:] if len(lower_touches) > 5 else lower_touches
            }
            
        except Exception as e:
            logger.error(f"Error generating Bollinger signals: {str(e)}")
            return {
                'current_position': 'unknown',
                'bandwidth': None,
                'squeeze': False,
                'upper_touches': [],
                'lower_touches': []
            }
    
    @staticmethod
    def calculate_all(high: Union[List[float], pd.Series, np.ndarray],
                     low: Union[List[float], pd.Series, np.ndarray],
                     close: Union[List[float], pd.Series, np.ndarray],
                     periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all volatility indicators
        
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            periods: Optional dict with custom periods
                    Default: {'atr': 14, 'bb': 20, 'bb_std': 2.0, 
                             'std_dev': 20, 'hv': 20}
            
        Returns:
            Dictionary with all calculated volatility indicators
        """
        if periods is None:
            periods = {
                'atr': 14,
                'bb': 20,
                'bb_std': 2.0,
                'std_dev': 20,
                'hv': 20,
                'keltner': 20,
                'keltner_atr': 10,
                'keltner_mult': 2.0
            }
        
        result = {}
        
        try:
            # Calculate ATR
            atr_values = VolatilityIndicators.atr(high, low, close, periods['atr'])
            result['atr'] = atr_values
            
            # Calculate Bollinger Bands
            bb_data = VolatilityIndicators.bollinger_bands(
                close, 
                periods['bb'], 
                periods['bb_std']
            )
            result['bollinger_bands'] = {
                'values': bb_data,
                'signals': VolatilityIndicators.get_bollinger_signals(close, bb_data)
            }
            
            # Calculate Standard Deviation
            result['std_dev'] = VolatilityIndicators.standard_deviation(
                close, 
                periods['std_dev']
            )
            
            # Calculate Historical Volatility
            result['historical_volatility'] = VolatilityIndicators.historical_volatility(
                close, 
                periods['hv']
            )
            
            # Calculate Keltner Channels
            result['keltner_channels'] = VolatilityIndicators.keltner_channels(
                high, low, close,
                periods['keltner'],
                periods['keltner_atr'],
                periods['keltner_mult']
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating all volatility indicators: {str(e)}")
            return result


# Convenience functions
def atr(high: Union[List[float], pd.Series, np.ndarray],
        low: Union[List[float], pd.Series, np.ndarray],
        close: Union[List[float], pd.Series, np.ndarray],
        period: int = 14) -> np.ndarray:
    """Quick ATR calculation"""
    return VolatilityIndicators.atr(high, low, close, period)


def bollinger_bands(data: Union[List[float], pd.Series, np.ndarray],
                   period: int = 20,
                   std_dev: float = 2.0) -> Dict[str, np.ndarray]:
    """Quick Bollinger Bands calculation"""
    return VolatilityIndicators.bollinger_bands(data, period, std_dev)
