"""
Momentum Indicators Module

This module provides momentum-based technical indicators:
- Relative Strength Index (RSI)
- Moving Average Convergence Divergence (MACD)
- Stochastic Oscillator
- Rate of Change (ROC)
- Commodity Channel Index (CCI)

Used for identifying overbought/oversold conditions and momentum shifts.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union, Tuple
import logging

logger = logging.getLogger(__name__)


class MomentumIndicators:
    """
    Momentum Indicators Calculator
    
    Provides methods to calculate various momentum indicators
    from OHLCV data.
    """
    
    @staticmethod
    def rsi(data: Union[List[float], pd.Series, np.ndarray], 
            period: int = 14) -> np.ndarray:
        """
        Calculate Relative Strength Index (RSI)
        
        RSI measures the magnitude of recent price changes to evaluate
        overbought or oversold conditions.
        
        Formula:
            RS = Average Gain / Average Loss
            RSI = 100 - (100 / (1 + RS))
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods (default: 14)
            
        Returns:
            numpy array with RSI values (0-100)
            
        Interpretation:
            RSI > 70: Overbought
            RSI < 30: Oversold
            
        Example:
            >>> prices = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42]
            >>> rsi = MomentumIndicators.rsi(prices, period=6)
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period + 1:
                logger.warning(f"Insufficient data for RSI calculation. Need {period + 1}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate price changes
            deltas = np.diff(data)
            
            # Separate gains and losses
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            # Calculate average gains and losses
            avg_gains = np.full(len(data), np.nan)
            avg_losses = np.full(len(data), np.nan)
            
            # First average is simple mean
            avg_gains[period] = np.mean(gains[:period])
            avg_losses[period] = np.mean(losses[:period])
            
            # Subsequent values use smoothed average
            for i in range(period + 1, len(data)):
                avg_gains[i] = (avg_gains[i-1] * (period - 1) + gains[i-1]) / period
                avg_losses[i] = (avg_losses[i-1] * (period - 1) + losses[i-1]) / period
            
            # Calculate RS and RSI
            rs = np.where(avg_losses != 0, avg_gains / avg_losses, 0)
            rsi = 100 - (100 / (1 + rs))
            
            # Handle division by zero (when avg_losses = 0)
            rsi = np.where(avg_losses == 0, 100, rsi)
            
            return rsi
            
        except Exception as e:
            logger.error(f"Error calculating RSI: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def macd(data: Union[List[float], pd.Series, np.ndarray],
             fast_period: int = 12,
             slow_period: int = 26,
             signal_period: int = 9) -> Dict[str, np.ndarray]:
        """
        Calculate Moving Average Convergence Divergence (MACD)
        
        MACD shows the relationship between two moving averages.
        
        Formula:
            MACD Line = EMA(fast) - EMA(slow)
            Signal Line = EMA(MACD Line, signal_period)
            Histogram = MACD Line - Signal Line
            
        Args:
            data: Price data (typically close prices)
            fast_period: Fast EMA period (default: 12)
            slow_period: Slow EMA period (default: 26)
            signal_period: Signal line EMA period (default: 9)
            
        Returns:
            Dictionary with 'macd', 'signal', and 'histogram' arrays
            
        Interpretation:
            MACD > Signal: Bullish
            MACD < Signal: Bearish
            Histogram > 0: Bullish momentum
            Histogram < 0: Bearish momentum
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < slow_period:
                logger.warning(f"Insufficient data for MACD calculation. Need {slow_period}, got {len(data)}")
                return {
                    'macd': np.full(len(data), np.nan),
                    'signal': np.full(len(data), np.nan),
                    'histogram': np.full(len(data), np.nan)
                }
            
            # Calculate EMAs
            fast_ema = pd.Series(data).ewm(span=fast_period, adjust=False).mean().values
            slow_ema = pd.Series(data).ewm(span=slow_period, adjust=False).mean().values
            
            # Calculate MACD line
            macd_line = fast_ema - slow_ema
            
            # Calculate signal line
            signal_line = pd.Series(macd_line).ewm(span=signal_period, adjust=False).mean().values
            
            # Calculate histogram
            histogram = macd_line - signal_line
            
            return {
                'macd': macd_line,
                'signal': signal_line,
                'histogram': histogram
            }
            
        except Exception as e:
            logger.error(f"Error calculating MACD: {str(e)}")
            return {
                'macd': np.full(len(data), np.nan),
                'signal': np.full(len(data), np.nan),
                'histogram': np.full(len(data), np.nan)
            }
    
    @staticmethod
    def stochastic(high: Union[List[float], pd.Series, np.ndarray],
                   low: Union[List[float], pd.Series, np.ndarray],
                   close: Union[List[float], pd.Series, np.ndarray],
                   k_period: int = 14,
                   d_period: int = 3) -> Dict[str, np.ndarray]:
        """
        Calculate Stochastic Oscillator
        
        Stochastic compares closing price to price range over a period.
        
        Formula:
            %K = 100 * (Close - Lowest Low) / (Highest High - Lowest Low)
            %D = SMA(%K, d_period)
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            k_period: Period for %K (default: 14)
            d_period: Period for %D (default: 3)
            
        Returns:
            Dictionary with 'k' and 'd' arrays (0-100)
            
        Interpretation:
            %K > 80: Overbought
            %K < 20: Oversold
            %K crosses above %D: Bullish signal
            %K crosses below %D: Bearish signal
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
            
            if len(close) < k_period:
                logger.warning(f"Insufficient data for Stochastic calculation. Need {k_period}, got {len(close)}")
                return {
                    'k': np.full(len(close), np.nan),
                    'd': np.full(len(close), np.nan)
                }
            
            # Calculate %K
            k = np.full(len(close), np.nan)
            
            for i in range(k_period - 1, len(close)):
                period_high = np.max(high[i - k_period + 1:i + 1])
                period_low = np.min(low[i - k_period + 1:i + 1])
                
                if period_high - period_low != 0:
                    k[i] = 100 * (close[i] - period_low) / (period_high - period_low)
                else:
                    k[i] = 50  # Neutral value when range is 0
            
            # Calculate %D (SMA of %K)
            d = pd.Series(k).rolling(window=d_period, min_periods=d_period).mean().values
            
            return {
                'k': k,
                'd': d
            }
            
        except Exception as e:
            logger.error(f"Error calculating Stochastic: {str(e)}")
            return {
                'k': np.full(len(close), np.nan),
                'd': np.full(len(close), np.nan)
            }
    
    @staticmethod
    def roc(data: Union[List[float], pd.Series, np.ndarray],
            period: int = 12) -> np.ndarray:
        """
        Calculate Rate of Change (ROC)
        
        ROC measures the percentage change in price over a period.
        
        Formula:
            ROC = ((Close - Close_n_periods_ago) / Close_n_periods_ago) * 100
            
        Args:
            data: Price data (typically close prices)
            period: Number of periods (default: 12)
            
        Returns:
            numpy array with ROC values (percentage)
            
        Interpretation:
            ROC > 0: Upward momentum
            ROC < 0: Downward momentum
            ROC crossing 0: Momentum shift
        """
        try:
            if isinstance(data, list):
                data = np.array(data)
            elif isinstance(data, pd.Series):
                data = data.values
            
            if len(data) < period + 1:
                logger.warning(f"Insufficient data for ROC calculation. Need {period + 1}, got {len(data)}")
                return np.full(len(data), np.nan)
            
            # Calculate ROC
            roc = np.full(len(data), np.nan)
            
            for i in range(period, len(data)):
                if data[i - period] != 0:
                    roc[i] = ((data[i] - data[i - period]) / data[i - period]) * 100
                else:
                    roc[i] = 0
            
            return roc
            
        except Exception as e:
            logger.error(f"Error calculating ROC: {str(e)}")
            return np.full(len(data), np.nan)
    
    @staticmethod
    def cci(high: Union[List[float], pd.Series, np.ndarray],
            low: Union[List[float], pd.Series, np.ndarray],
            close: Union[List[float], pd.Series, np.ndarray],
            period: int = 20,
            constant: float = 0.015) -> np.ndarray:
        """
        Calculate Commodity Channel Index (CCI)
        
        CCI measures the variation of price from its statistical mean.
        
        Formula:
            Typical Price = (High + Low + Close) / 3
            SMA = SMA(Typical Price, period)
            Mean Deviation = Mean(|Typical Price - SMA|)
            CCI = (Typical Price - SMA) / (constant * Mean Deviation)
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: Number of periods (default: 20)
            constant: Scaling constant (default: 0.015)
            
        Returns:
            numpy array with CCI values
            
        Interpretation:
            CCI > 100: Overbought
            CCI < -100: Oversold
            CCI crossing 0: Trend change
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
            
            if len(close) < period:
                logger.warning(f"Insufficient data for CCI calculation. Need {period}, got {len(close)}")
                return np.full(len(close), np.nan)
            
            # Calculate Typical Price
            typical_price = (high + low + close) / 3
            
            # Calculate SMA of Typical Price
            sma = pd.Series(typical_price).rolling(window=period, min_periods=period).mean().values
            
            # Calculate Mean Deviation
            cci = np.full(len(close), np.nan)
            
            for i in range(period - 1, len(close)):
                mean_dev = np.mean(np.abs(typical_price[i - period + 1:i + 1] - sma[i]))
                
                if mean_dev != 0:
                    cci[i] = (typical_price[i] - sma[i]) / (constant * mean_dev)
                else:
                    cci[i] = 0
            
            return cci
            
        except Exception as e:
            logger.error(f"Error calculating CCI: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def get_rsi_signals(rsi: np.ndarray,
                       overbought: float = 70,
                       oversold: float = 30) -> Dict[str, any]:
        """
        Generate trading signals from RSI
        
        Args:
            rsi: RSI values
            overbought: Overbought threshold (default: 70)
            oversold: Oversold threshold (default: 30)
            
        Returns:
            Dictionary with current condition and signal indices
        """
        try:
            # Get last valid RSI value
            valid_rsi = rsi[~np.isnan(rsi)]
            
            if len(valid_rsi) == 0:
                return {
                    'current_rsi': None,
                    'condition': 'unknown',
                    'overbought_indices': [],
                    'oversold_indices': []
                }
            
            current_rsi = valid_rsi[-1]
            
            # Determine condition
            if current_rsi > overbought:
                condition = 'overbought'
            elif current_rsi < oversold:
                condition = 'oversold'
            else:
                condition = 'neutral'
            
            # Find all overbought and oversold points
            overbought_indices = np.where(rsi > overbought)[0].tolist()
            oversold_indices = np.where(rsi < oversold)[0].tolist()
            
            return {
                'current_rsi': float(current_rsi),
                'condition': condition,
                'overbought_indices': overbought_indices,
                'oversold_indices': oversold_indices
            }
            
        except Exception as e:
            logger.error(f"Error generating RSI signals: {str(e)}")
            return {
                'current_rsi': None,
                'condition': 'unknown',
                'overbought_indices': [],
                'oversold_indices': []
            }
    
    @staticmethod
    def get_macd_signals(macd_data: Dict[str, np.ndarray]) -> Dict[str, any]:
        """
        Generate trading signals from MACD
        
        Args:
            macd_data: Dictionary with 'macd', 'signal', 'histogram' arrays
            
        Returns:
            Dictionary with current values and crossover signals
        """
        try:
            macd = macd_data['macd']
            signal = macd_data['signal']
            histogram = macd_data['histogram']
            
            # Get last valid values
            valid_indices = ~(np.isnan(macd) | np.isnan(signal))
            
            if not np.any(valid_indices):
                return {
                    'current_macd': None,
                    'current_signal': None,
                    'current_histogram': None,
                    'trend': 'unknown',
                    'bullish_crossovers': [],
                    'bearish_crossovers': []
                }
            
            last_idx = np.where(valid_indices)[0][-1]
            
            current_macd = float(macd[last_idx])
            current_signal = float(signal[last_idx])
            current_histogram = float(histogram[last_idx])
            
            # Determine trend
            if current_histogram > 0:
                trend = 'bullish'
            elif current_histogram < 0:
                trend = 'bearish'
            else:
                trend = 'neutral'
            
            # Find crossovers
            bullish_crossovers = []
            bearish_crossovers = []
            
            for i in range(1, len(macd)):
                if np.isnan(macd[i]) or np.isnan(signal[i]) or \
                   np.isnan(macd[i-1]) or np.isnan(signal[i-1]):
                    continue
                
                # Bullish crossover: MACD crosses above signal
                if macd[i-1] <= signal[i-1] and macd[i] > signal[i]:
                    bullish_crossovers.append(i)
                
                # Bearish crossover: MACD crosses below signal
                elif macd[i-1] >= signal[i-1] and macd[i] < signal[i]:
                    bearish_crossovers.append(i)
            
            return {
                'current_macd': current_macd,
                'current_signal': current_signal,
                'current_histogram': current_histogram,
                'trend': trend,
                'bullish_crossovers': bullish_crossovers,
                'bearish_crossovers': bearish_crossovers
            }
            
        except Exception as e:
            logger.error(f"Error generating MACD signals: {str(e)}")
            return {
                'current_macd': None,
                'current_signal': None,
                'current_histogram': None,
                'trend': 'unknown',
                'bullish_crossovers': [],
                'bearish_crossovers': []
            }
    
    @staticmethod
    def calculate_all(high: Union[List[float], pd.Series, np.ndarray],
                     low: Union[List[float], pd.Series, np.ndarray],
                     close: Union[List[float], pd.Series, np.ndarray],
                     periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all momentum indicators
        
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            periods: Optional dict with custom periods
                    Default: {'rsi': 14, 'macd_fast': 12, 'macd_slow': 26, 
                             'macd_signal': 9, 'stoch_k': 14, 'stoch_d': 3,
                             'roc': 12, 'cci': 20}
            
        Returns:
            Dictionary with all calculated momentum indicators
        """
        if periods is None:
            periods = {
                'rsi': 14,
                'macd_fast': 12,
                'macd_slow': 26,
                'macd_signal': 9,
                'stoch_k': 14,
                'stoch_d': 3,
                'roc': 12,
                'cci': 20
            }
        
        result = {}
        
        try:
            # Calculate RSI
            rsi_values = MomentumIndicators.rsi(close, periods['rsi'])
            result['rsi'] = {
                'values': rsi_values,
                'signals': MomentumIndicators.get_rsi_signals(rsi_values)
            }
            
            # Calculate MACD
            macd_data = MomentumIndicators.macd(
                close,
                periods['macd_fast'],
                periods['macd_slow'],
                periods['macd_signal']
            )
            result['macd'] = {
                'values': macd_data,
                'signals': MomentumIndicators.get_macd_signals(macd_data)
            }
            
            # Calculate Stochastic
            stoch_data = MomentumIndicators.stochastic(
                high, low, close,
                periods['stoch_k'],
                periods['stoch_d']
            )
            result['stochastic'] = stoch_data
            
            # Calculate ROC
            result['roc'] = MomentumIndicators.roc(close, periods['roc'])
            
            # Calculate CCI
            result['cci'] = MomentumIndicators.cci(high, low, close, periods['cci'])
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating all momentum indicators: {str(e)}")
            return result


# Convenience functions
def rsi(data: Union[List[float], pd.Series, np.ndarray], period: int = 14) -> np.ndarray:
    """Quick RSI calculation"""
    return MomentumIndicators.rsi(data, period)


def macd(data: Union[List[float], pd.Series, np.ndarray]) -> Dict[str, np.ndarray]:
    """Quick MACD calculation"""
    return MomentumIndicators.macd(data)
