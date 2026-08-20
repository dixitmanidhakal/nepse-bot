"""
Volume Indicators Module

This module provides volume-based technical indicators:
- On-Balance Volume (OBV)
- Volume Moving Average
- Volume Rate of Change
- Money Flow Index (MFI)
- Accumulation/Distribution Line
- Chaikin Money Flow

Used for confirming trends and detecting divergences.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union
import logging

logger = logging.getLogger(__name__)


class VolumeIndicators:
    """
    Volume Indicators Calculator
    
    Provides methods to calculate various volume indicators
    from OHLCV data.
    """
    
    @staticmethod
    def obv(close: Union[List[float], pd.Series, np.ndarray],
            volume: Union[List[float], pd.Series, np.ndarray]) -> np.ndarray:
        """
        Calculate On-Balance Volume (OBV)
        
        OBV measures buying and selling pressure as a cumulative indicator.
        
        Formula:
            If Close > Previous Close: OBV = Previous OBV + Volume
            If Close < Previous Close: OBV = Previous OBV - Volume
            If Close = Previous Close: OBV = Previous OBV
            
        Args:
            close: Close prices
            volume: Volume data
            
        Returns:
            numpy array with OBV values
            
        Interpretation:
            Rising OBV = Buying pressure
            Falling OBV = Selling pressure
            OBV divergence from price = Potential reversal
        """
        try:
            if isinstance(close, list):
                close = np.array(close)
            elif isinstance(close, pd.Series):
                close = close.values
                
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(close) < 2:
                logger.warning(f"Insufficient data for OBV calculation. Need at least 2, got {len(close)}")
                return np.full(len(close), np.nan)
            
            # Initialize OBV
            obv = np.zeros(len(close))
            obv[0] = volume[0]
            
            # Calculate OBV
            for i in range(1, len(close)):
                if close[i] > close[i-1]:
                    obv[i] = obv[i-1] + volume[i]
                elif close[i] < close[i-1]:
                    obv[i] = obv[i-1] - volume[i]
                else:
                    obv[i] = obv[i-1]
            
            return obv
            
        except Exception as e:
            logger.error(f"Error calculating OBV: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def volume_sma(volume: Union[List[float], pd.Series, np.ndarray],
                   period: int = 20) -> np.ndarray:
        """
        Calculate Volume Simple Moving Average
        
        Args:
            volume: Volume data
            period: Number of periods (default: 20)
            
        Returns:
            numpy array with volume SMA values
            
        Interpretation:
            Volume > Volume SMA: Above average volume
            Volume < Volume SMA: Below average volume
        """
        try:
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(volume) < period:
                logger.warning(f"Insufficient data for Volume SMA. Need {period}, got {len(volume)}")
                return np.full(len(volume), np.nan)
            
            # Calculate SMA
            vol_sma = pd.Series(volume).rolling(window=period, min_periods=period).mean().values
            
            return vol_sma
            
        except Exception as e:
            logger.error(f"Error calculating Volume SMA: {str(e)}")
            return np.full(len(volume), np.nan)
    
    @staticmethod
    def volume_roc(volume: Union[List[float], pd.Series, np.ndarray],
                   period: int = 12) -> np.ndarray:
        """
        Calculate Volume Rate of Change
        
        Formula:
            Volume ROC = ((Volume - Volume_n_periods_ago) / Volume_n_periods_ago) * 100
            
        Args:
            volume: Volume data
            period: Number of periods (default: 12)
            
        Returns:
            numpy array with Volume ROC values (percentage)
            
        Interpretation:
            Positive ROC: Increasing volume
            Negative ROC: Decreasing volume
        """
        try:
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(volume) < period + 1:
                logger.warning(f"Insufficient data for Volume ROC. Need {period + 1}, got {len(volume)}")
                return np.full(len(volume), np.nan)
            
            # Calculate ROC
            vol_roc = np.full(len(volume), np.nan)
            
            for i in range(period, len(volume)):
                if volume[i - period] != 0:
                    vol_roc[i] = ((volume[i] - volume[i - period]) / volume[i - period]) * 100
                else:
                    vol_roc[i] = 0
            
            return vol_roc
            
        except Exception as e:
            logger.error(f"Error calculating Volume ROC: {str(e)}")
            return np.full(len(volume), np.nan)
    
    @staticmethod
    def mfi(high: Union[List[float], pd.Series, np.ndarray],
            low: Union[List[float], pd.Series, np.ndarray],
            close: Union[List[float], pd.Series, np.ndarray],
            volume: Union[List[float], pd.Series, np.ndarray],
            period: int = 14) -> np.ndarray:
        """
        Calculate Money Flow Index (MFI)
        
        MFI is a volume-weighted RSI that measures buying and selling pressure.
        
        Formula:
            Typical Price = (High + Low + Close) / 3
            Raw Money Flow = Typical Price * Volume
            Money Flow Ratio = (Positive Money Flow / Negative Money Flow)
            MFI = 100 - (100 / (1 + Money Flow Ratio))
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            volume: Volume data
            period: Number of periods (default: 14)
            
        Returns:
            numpy array with MFI values (0-100)
            
        Interpretation:
            MFI > 80: Overbought
            MFI < 20: Oversold
            MFI divergence from price: Potential reversal
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
                
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(close) < period + 1:
                logger.warning(f"Insufficient data for MFI calculation. Need {period + 1}, got {len(close)}")
                return np.full(len(close), np.nan)
            
            # Calculate Typical Price
            typical_price = (high + low + close) / 3
            
            # Calculate Raw Money Flow
            raw_money_flow = typical_price * volume
            
            # Calculate MFI
            mfi = np.full(len(close), np.nan)
            
            for i in range(period, len(close)):
                positive_flow = 0
                negative_flow = 0
                
                for j in range(i - period + 1, i + 1):
                    if typical_price[j] > typical_price[j-1]:
                        positive_flow += raw_money_flow[j]
                    elif typical_price[j] < typical_price[j-1]:
                        negative_flow += raw_money_flow[j]
                
                if negative_flow != 0:
                    money_flow_ratio = positive_flow / negative_flow
                    mfi[i] = 100 - (100 / (1 + money_flow_ratio))
                else:
                    mfi[i] = 100
            
            return mfi
            
        except Exception as e:
            logger.error(f"Error calculating MFI: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def ad_line(high: Union[List[float], pd.Series, np.ndarray],
                low: Union[List[float], pd.Series, np.ndarray],
                close: Union[List[float], pd.Series, np.ndarray],
                volume: Union[List[float], pd.Series, np.ndarray]) -> np.ndarray:
        """
        Calculate Accumulation/Distribution Line
        
        A/D Line measures the cumulative flow of money into and out of a security.
        
        Formula:
            Money Flow Multiplier = ((Close - Low) - (High - Close)) / (High - Low)
            Money Flow Volume = Money Flow Multiplier * Volume
            A/D Line = Previous A/D + Money Flow Volume
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            volume: Volume data
            
        Returns:
            numpy array with A/D Line values
            
        Interpretation:
            Rising A/D: Accumulation (buying pressure)
            Falling A/D: Distribution (selling pressure)
            A/D divergence from price: Potential reversal
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
                
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(close) < 1:
                logger.warning(f"Insufficient data for A/D Line calculation")
                return np.full(len(close), np.nan)
            
            # Calculate Money Flow Multiplier
            mf_multiplier = np.zeros(len(close))
            
            for i in range(len(close)):
                if high[i] - low[i] != 0:
                    mf_multiplier[i] = ((close[i] - low[i]) - (high[i] - close[i])) / (high[i] - low[i])
                else:
                    mf_multiplier[i] = 0
            
            # Calculate Money Flow Volume
            mf_volume = mf_multiplier * volume
            
            # Calculate A/D Line (cumulative)
            ad_line = np.cumsum(mf_volume)
            
            return ad_line
            
        except Exception as e:
            logger.error(f"Error calculating A/D Line: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def cmf(high: Union[List[float], pd.Series, np.ndarray],
            low: Union[List[float], pd.Series, np.ndarray],
            close: Union[List[float], pd.Series, np.ndarray],
            volume: Union[List[float], pd.Series, np.ndarray],
            period: int = 20) -> np.ndarray:
        """
        Calculate Chaikin Money Flow (CMF)
        
        CMF measures the amount of Money Flow Volume over a period.
        
        Formula:
            Money Flow Multiplier = ((Close - Low) - (High - Close)) / (High - Low)
            Money Flow Volume = Money Flow Multiplier * Volume
            CMF = Sum(Money Flow Volume, period) / Sum(Volume, period)
            
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            volume: Volume data
            period: Number of periods (default: 20)
            
        Returns:
            numpy array with CMF values (-1 to +1)
            
        Interpretation:
            CMF > 0: Buying pressure
            CMF < 0: Selling pressure
            CMF near +1: Strong buying
            CMF near -1: Strong selling
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
                
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(close) < period:
                logger.warning(f"Insufficient data for CMF calculation. Need {period}, got {len(close)}")
                return np.full(len(close), np.nan)
            
            # Calculate Money Flow Multiplier
            mf_multiplier = np.zeros(len(close))
            
            for i in range(len(close)):
                if high[i] - low[i] != 0:
                    mf_multiplier[i] = ((close[i] - low[i]) - (high[i] - close[i])) / (high[i] - low[i])
                else:
                    mf_multiplier[i] = 0
            
            # Calculate Money Flow Volume
            mf_volume = mf_multiplier * volume
            
            # Calculate CMF
            cmf = np.full(len(close), np.nan)
            
            for i in range(period - 1, len(close)):
                sum_mf_volume = np.sum(mf_volume[i - period + 1:i + 1])
                sum_volume = np.sum(volume[i - period + 1:i + 1])
                
                if sum_volume != 0:
                    cmf[i] = sum_mf_volume / sum_volume
                else:
                    cmf[i] = 0
            
            return cmf
            
        except Exception as e:
            logger.error(f"Error calculating CMF: {str(e)}")
            return np.full(len(close), np.nan)
    
    @staticmethod
    def volume_ratio(volume: Union[List[float], pd.Series, np.ndarray],
                     period: int = 20) -> np.ndarray:
        """
        Calculate Volume Ratio (Current Volume / Average Volume)
        
        Args:
            volume: Volume data
            period: Number of periods for average (default: 20)
            
        Returns:
            numpy array with volume ratio values
            
        Interpretation:
            Ratio > 1: Above average volume
            Ratio < 1: Below average volume
            Ratio > 2: Significantly high volume
        """
        try:
            if isinstance(volume, list):
                volume = np.array(volume)
            elif isinstance(volume, pd.Series):
                volume = volume.values
            
            if len(volume) < period:
                logger.warning(f"Insufficient data for Volume Ratio. Need {period}, got {len(volume)}")
                return np.full(len(volume), np.nan)
            
            # Calculate average volume
            avg_volume = pd.Series(volume).rolling(window=period, min_periods=period).mean().values
            
            # Calculate ratio
            ratio = np.where(avg_volume != 0, volume / avg_volume, np.nan)
            
            return ratio
            
        except Exception as e:
            logger.error(f"Error calculating Volume Ratio: {str(e)}")
            return np.full(len(volume), np.nan)
    
    @staticmethod
    def calculate_all(high: Union[List[float], pd.Series, np.ndarray],
                     low: Union[List[float], pd.Series, np.ndarray],
                     close: Union[List[float], pd.Series, np.ndarray],
                     volume: Union[List[float], pd.Series, np.ndarray],
                     periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all volume indicators
        
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            volume: Volume data
            periods: Optional dict with custom periods
                    Default: {'vol_sma': 20, 'vol_roc': 12, 'mfi': 14, 'cmf': 20}
            
        Returns:
            Dictionary with all calculated volume indicators
        """
        if periods is None:
            periods = {
                'vol_sma': 20,
                'vol_roc': 12,
                'mfi': 14,
                'cmf': 20,
                'vol_ratio': 20
            }
        
        result = {}
        
        try:
            # Calculate OBV
            result['obv'] = VolumeIndicators.obv(close, volume)
            
            # Calculate Volume SMA
            result['volume_sma'] = VolumeIndicators.volume_sma(volume, periods['vol_sma'])
            
            # Calculate Volume ROC
            result['volume_roc'] = VolumeIndicators.volume_roc(volume, periods['vol_roc'])
            
            # Calculate MFI
            result['mfi'] = VolumeIndicators.mfi(high, low, close, volume, periods['mfi'])
            
            # Calculate A/D Line
            result['ad_line'] = VolumeIndicators.ad_line(high, low, close, volume)
            
            # Calculate CMF
            result['cmf'] = VolumeIndicators.cmf(high, low, close, volume, periods['cmf'])
            
            # Calculate Volume Ratio
            result['volume_ratio'] = VolumeIndicators.volume_ratio(volume, periods['vol_ratio'])
            
            # Add current volume analysis
            if len(volume) > 0 and not np.isnan(result['volume_sma'][-1]):
                current_volume = volume[-1]
                avg_volume = result['volume_sma'][-1]
                
                result['current_analysis'] = {
                    'current_volume': float(current_volume),
                    'average_volume': float(avg_volume),
                    'volume_ratio': float(current_volume / avg_volume) if avg_volume != 0 else None,
                    'volume_status': 'high' if current_volume > avg_volume * 1.5 else 
                                   'above_average' if current_volume > avg_volume else
                                   'below_average' if current_volume < avg_volume * 0.5 else
                                   'normal'
                }
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating all volume indicators: {str(e)}")
            return result


# Convenience functions
def obv(close: Union[List[float], pd.Series, np.ndarray],
        volume: Union[List[float], pd.Series, np.ndarray]) -> np.ndarray:
    """Quick OBV calculation"""
    return VolumeIndicators.obv(close, volume)


def mfi(high: Union[List[float], pd.Series, np.ndarray],
        low: Union[List[float], pd.Series, np.ndarray],
        close: Union[List[float], pd.Series, np.ndarray],
        volume: Union[List[float], pd.Series, np.ndarray],
        period: int = 14) -> np.ndarray:
    """Quick MFI calculation"""
    return VolumeIndicators.mfi(high, low, close, volume, period)
