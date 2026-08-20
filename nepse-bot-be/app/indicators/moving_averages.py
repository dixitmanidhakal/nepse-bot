"""
Moving Averages Module

This module provides various moving average calculations:
- Simple Moving Average (SMA)
- Exponential Moving Average (EMA)
- Weighted Moving Average (WMA)

Used for trend identification and support/resistance levels.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union
import logging

logger = logging.getLogger(__name__)


class MovingAverages:
    """
    Moving Averages Calculator
    
    Provides methods to calculate different types of moving averages
    from OHLCV data.
    """
    
    @staticmethod
    def sma(data: Union[List[float], pd.Series, np.ndarray], period: int) -> np.ndarray:
        """
        Calculate Simple Moving Average (SMA)
        
        SMA = Sum of prices over period / period
        
        Args:
            data: Price data (typically close prices)
            period: Number of periods for the moving average
            
        Returns:
            numpy array with SMA values (NaN for insufficient data)
            
        Example:
            >>> prices = [10, 11, 12, 13, 14, 15]
            >>> sma = MovingAverages.sma(prices, period=3)
            >>> # Returns: [nan, nan, 11.0, 12.0, 13.0, 14.0]
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period:
                logger.warning(f"Insufficient data for SMA calculation. Need {period}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate SMA using pandas for efficiency
            sma = pd.Series(data).rolling(window=period, min_periods=period).mean().values
            
            return sma
            
        except Exception as e:
            logger.error(f"Error calculating SMA: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def ema(data: Union[List[float], pd.Series, np.ndarray], period: int, 
            smoothing: float = 2.0) -> np.ndarray:
        """
        Calculate Exponential Moving Average (EMA)
        
        EMA gives more weight to recent prices.
        
        Formula:
            Multiplier = smoothing / (period + 1)
            EMA = (Close - EMA_previous) * Multiplier + EMA_previous
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods for the moving average
            smoothing: Smoothing factor (default: 2.0)
            
        Returns:
            numpy array with EMA values
            
        Example:
            >>> prices = [10, 11, 12, 13, 14, 15]
            >>> ema = MovingAverages.ema(prices, period=3)
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period:
                logger.warning(f"Insufficient data for EMA calculation. Need {period}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate EMA using pandas ewm
            ema = pd.Series(data).ewm(span=period, adjust=False).mean().values
            
            return ema
            
        except Exception as e:
            logger.error(f"Error calculating EMA: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def wma(data: Union[List[float], pd.Series, np.ndarray], period: int) -> np.ndarray:
        """
        Calculate Weighted Moving Average (WMA)
        
        WMA gives linearly increasing weights to recent prices.
        Most recent price has weight = period, oldest has weight = 1.
        
        Formula:
            WMA = (P1*1 + P2*2 + ... + Pn*n) / (1 + 2 + ... + n)
            where n = period
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods for the moving average
            
        Returns:
            numpy array with WMA values
            
        Example:
            >>> prices = [10, 11, 12, 13, 14, 15]
            >>> wma = MovingAverages.wma(prices, period=3)
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period:
                logger.warning(f"Insufficient data for WMA calculation. Need {period}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Create weights (1, 2, 3, ..., period)
            weights = np.arange(1, period + 1)
            weight_sum = weights.sum()
            
            # Calculate WMA
            wma = np.full(len(data), np.nan)
            
            for i in range(period - 1, len(data)):
                window = data[i - period + 1:i + 1]
                wma[i] = np.dot(window, weights) / weight_sum
            
            return wma
            
        except Exception as e:
            logger.error(f"Error calculating WMA: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def calculate_multiple_sma(data: Union[List[float], pd.Series, np.ndarray], 
                               periods: List[int]) -> Dict[str, np.ndarray]:
        """
        Calculate multiple SMAs at once
        
        Args:
            data: Price data
            periods: List of periods to calculate (e.g., [5, 10, 20, 50, 200])
            
        Returns:
            Dictionary with period as key and SMA array as value
            
        Example:
            >>> prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
            >>> smas = MovingAverages.calculate_multiple_sma(prices, [3, 5])
            >>> # Returns: {'sma_3': [...], 'sma_5': [...]}
        """
        result = {}
        
        for period in periods:
            try:
                sma = MovingAverages.sma(data, period)
                result[f'sma_{period}'] = sma
            except Exception as e:
                logger.error(f"Error calculating SMA for period {period}: {str(e)}")
                result[f'sma_{period}'] = np.full(len(data), np.nan)
        
        return result
    
    @staticmethod
    def calculate_multiple_ema(data: Union[List[float], pd.Series, np.ndarray], 
                               periods: List[int]) -> Dict[str, np.ndarray]:
        """
        Calculate multiple EMAs at once
        
        Args:
            data: Price data
            periods: List of periods to calculate (e.g., [5, 10, 20, 50, 200])
            
        Returns:
            Dictionary with period as key and EMA array as value
            
        Example:
            >>> prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
            >>> emas = MovingAverages.calculate_multiple_ema(prices, [3, 5])
            >>> # Returns: {'ema_3': [...], 'ema_5': [...]}
        """
        result = {}
        
        for period in periods:
            try:
                ema = MovingAverages.ema(data, period)
                result[f'ema_{period}'] = ema
            except Exception as e:
                logger.error(f"Error calculating EMA for period {period}: {str(e)}")
                result[f'ema_{period}'] = np.full(len(data), np.nan)
        
        return result
    
    @staticmethod
    def get_crossover_signals(fast_ma: np.ndarray, slow_ma: np.ndarray) -> Dict[str, List[int]]:
        """
        Detect moving average crossovers
        
        Args:
            fast_ma: Faster moving average (e.g., 10-day)
            slow_ma: Slower moving average (e.g., 50-day)
            
        Returns:
            Dictionary with 'bullish' and 'bearish' crossover indices
            
        Example:
            >>> fast = np.array([10, 11, 12, 11, 10])
            >>> slow = np.array([11, 11, 11, 11, 11])
            >>> signals = MovingAverages.get_crossover_signals(fast, slow)
            >>> # Returns: {'bullish': [2], 'bearish': [3]}
        """
        try:
            bullish_crossovers = []
            bearish_crossovers = []
            
            for i in range(1, len(fast_ma)):
                # Skip if either value is NaN
                if np.isnan(fast_ma[i]) or np.isnan(slow_ma[i]) or \
                   np.isnan(fast_ma[i-1]) or np.isnan(slow_ma[i-1]):
                    continue
                
                # Bullish crossover: fast crosses above slow
                if fast_ma[i-1] <= slow_ma[i-1] and fast_ma[i] > slow_ma[i]:
                    bullish_crossovers.append(i)
                
                # Bearish crossover: fast crosses below slow
                elif fast_ma[i-1] >= slow_ma[i-1] and fast_ma[i] < slow_ma[i]:
                    bearish_crossovers.append(i)
            
            return {
                'bullish': bullish_crossovers,
                'bearish': bearish_crossovers
            }
            
        except Exception as e:
            logger.error(f"Error detecting crossovers: {str(e)}")
            return {'bullish': [], 'bearish': []}
    
    @staticmethod
    def get_ma_trend(data: Union[List[float], pd.Series, np.ndarray], 
                     period: int = 20, 
                     ma_type: str = 'sma') -> str:
        """
        Determine trend based on moving average
        
        Args:
            data: Price data
            period: MA period
            ma_type: Type of MA ('sma', 'ema', 'wma')
            
        Returns:
            'uptrend', 'downtrend', or 'sideways'
        """
        try:
            if ma_type == 'sma':
                ma = MovingAverages.sma(data, period)
            elif ma_type == 'ema':
                ma = MovingAverages.ema(data, period)
            elif ma_type == 'wma':
                ma = MovingAverages.wma(data, period)
            else:
                raise ValueError(f"Invalid ma_type: {ma_type}")
            
            # Get last valid MA values
            valid_ma = ma[~np.isnan(ma)]
            
            if len(valid_ma) < 2:
                return 'unknown'
            
            # Compare last few values to determine trend
            recent_ma = valid_ma[-5:] if len(valid_ma) >= 5 else valid_ma
            
            # Calculate slope
            x = np.arange(len(recent_ma))
            slope = np.polyfit(x, recent_ma, 1)[0]
            
            # Determine trend based on slope
            if slope > 0.01:  # Threshold for uptrend
                return 'uptrend'
            elif slope < -0.01:  # Threshold for downtrend
                return 'downtrend'
            else:
                return 'sideways'
                
        except Exception as e:
            logger.error(f"Error determining MA trend: {str(e)}")
            return 'unknown'
    
    @staticmethod
    def calculate_all(close_prices: Union[List[float], pd.Series, np.ndarray],
                     periods: Optional[Dict[str, List[int]]] = None) -> Dict[str, any]:
        """
        Calculate all moving averages with default or custom periods
        
        Args:
            close_prices: Close price data
            periods: Optional dict with 'sma', 'ema', 'wma' keys and period lists
                    Default: {'sma': [5, 10, 20, 50, 200], 'ema': [5, 10, 20, 50, 200]}
            
        Returns:
            Dictionary with all calculated moving averages and metadata
        """
        if periods is None:
            periods = {
                'sma': [5, 10, 20, 50, 200],
                'ema': [5, 10, 20, 50, 200],
                'wma': [10, 20]
            }
        
        result = {
            'sma': {},
            'ema': {},
            'wma': {},
            'crossovers': {},
            'trends': {}
        }
        
        try:
            # Calculate SMAs
            if 'sma' in periods:
                result['sma'] = MovingAverages.calculate_multiple_sma(
                    close_prices, periods['sma']
                )
            
            # Calculate EMAs
            if 'ema' in periods:
                result['ema'] = MovingAverages.calculate_multiple_ema(
                    close_prices, periods['ema']
                )
            
            # Calculate WMAs
            if 'wma' in periods:
                for period in periods['wma']:
                    wma = MovingAverages.wma(close_prices, period)
                    result['wma'][f'wma_{period}'] = wma
            
            # Detect common crossovers (e.g., 10/50, 20/200)
            if 'sma' in periods:
                if 10 in periods['sma'] and 50 in periods['sma']:
                    result['crossovers']['sma_10_50'] = MovingAverages.get_crossover_signals(
                        result['sma']['sma_10'], result['sma']['sma_50']
                    )
                
                if 50 in periods['sma'] and 200 in periods['sma']:
                    result['crossovers']['sma_50_200'] = MovingAverages.get_crossover_signals(
                        result['sma']['sma_50'], result['sma']['sma_200']
                    )
            
            # Determine trends
            result['trends']['sma_20'] = MovingAverages.get_ma_trend(close_prices, 20, 'sma')
            result['trends']['ema_20'] = MovingAverages.get_ma_trend(close_prices, 20, 'ema')
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating all moving averages: {str(e)}")
            return result


# Convenience functions for quick calculations
def sma(data: Union[List[float], pd.Series, np.ndarray], period: int = 20) -> np.ndarray:
    """Quick SMA calculation"""
    return MovingAverages.sma(data, period)


def ema(data: Union[List[float], pd.Series, np.ndarray], period: int = 20) -> np.ndarray:
    """Quick EMA calculation"""
    return MovingAverages.ema(data, period)


def wma(data: Union[List[float], pd.Series, np.ndarray], period: int = 20) -> np.ndarray:
    """Quick WMA calculation"""
    return MovingAverages.wma(data, period)
