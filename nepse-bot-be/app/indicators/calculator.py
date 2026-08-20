"""
Indicator Calculator Module

This module orchestrates the calculation of all technical indicators
from OHLCV data stored in the database.

It provides a unified interface to calculate and retrieve indicators
for any stock symbol.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import logging

from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.indicators.moving_averages import MovingAverages
from app.indicators.momentum import MomentumIndicators
from app.indicators.volatility import VolatilityIndicators
from app.indicators.volume import VolumeIndicators

logger = logging.getLogger(__name__)


class IndicatorCalculator:
    """
    Indicator Calculator
    
    Orchestrates the calculation of all technical indicators
    from database OHLCV data.
    """
    
    def __init__(self, db: Session):
        """
        Initialize calculator with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_ohlcv_data(self, 
                       symbol: str, 
                       days: int = 200,
                       end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data from database
        
        Args:
            symbol: Stock symbol
            days: Number of days of historical data
            end_date: End date for data (default: today)
            
        Returns:
            DataFrame with OHLCV data or None if not found
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                logger.error(f"Stock not found: {symbol}")
                return None
            
            # Calculate start date
            if end_date is None:
                end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Fetch OHLCV data
            ohlcv_records = (
                self.db.query(StockOHLCV)
                .filter(
                    StockOHLCV.stock_id == stock.id,
                    StockOHLCV.date >= start_date,
                    StockOHLCV.date <= end_date
                )
                .order_by(StockOHLCV.date.asc())
                .all()
            )
            
            if not ohlcv_records:
                logger.warning(f"No OHLCV data found for {symbol}")
                return None
            
            # Convert to DataFrame
            data = {
                'date': [r.date for r in ohlcv_records],
                'open': [r.open for r in ohlcv_records],
                'high': [r.high for r in ohlcv_records],
                'low': [r.low for r in ohlcv_records],
                'close': [r.close for r in ohlcv_records],
                'volume': [r.volume for r in ohlcv_records]
            }
            
            df = pd.DataFrame(data)
            df.set_index('date', inplace=True)
            
            logger.info(f"Fetched {len(df)} OHLCV records for {symbol}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data for {symbol}: {str(e)}")
            return None
    
    def calculate_moving_averages(self,
                                 df: pd.DataFrame,
                                 periods: Optional[Dict[str, List[int]]] = None) -> Dict[str, any]:
        """
        Calculate all moving averages
        
        Args:
            df: DataFrame with OHLCV data
            periods: Optional custom periods
            
        Returns:
            Dictionary with moving average results
        """
        try:
            close_prices = df['close'].values
            
            result = MovingAverages.calculate_all(close_prices, periods)
            
            # Convert numpy arrays to lists for JSON serialization
            for ma_type in ['sma', 'ema', 'wma']:
                if ma_type in result:
                    for key, values in result[ma_type].items():
                        if isinstance(values, np.ndarray):
                            # Get last valid value
                            valid_values = values[~np.isnan(values)]
                            result[ma_type][key] = {
                                'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                                'values': values.tolist()
                            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating moving averages: {str(e)}")
            return {}
    
    def calculate_momentum(self,
                          df: pd.DataFrame,
                          periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all momentum indicators
        
        Args:
            df: DataFrame with OHLCV data
            periods: Optional custom periods
            
        Returns:
            Dictionary with momentum indicator results
        """
        try:
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            
            result = MomentumIndicators.calculate_all(high, low, close, periods)
            
            # Convert numpy arrays to lists for JSON serialization
            if 'rsi' in result and 'values' in result['rsi']:
                values = result['rsi']['values']
                valid_values = values[~np.isnan(values)]
                result['rsi']['current'] = float(valid_values[-1]) if len(valid_values) > 0 else None
                result['rsi']['values'] = values.tolist()
            
            if 'macd' in result and 'values' in result['macd']:
                for key in ['macd', 'signal', 'histogram']:
                    if key in result['macd']['values']:
                        values = result['macd']['values'][key]
                        result['macd']['values'][key] = values.tolist()
            
            if 'stochastic' in result:
                for key in ['k', 'd']:
                    if key in result['stochastic']:
                        values = result['stochastic'][key]
                        valid_values = values[~np.isnan(values)]
                        result['stochastic'][f'{key}_current'] = float(valid_values[-1]) if len(valid_values) > 0 else None
                        result['stochastic'][key] = values.tolist()
            
            if 'roc' in result:
                values = result['roc']
                valid_values = values[~np.isnan(values)]
                result['roc'] = {
                    'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                    'values': values.tolist()
                }
            
            if 'cci' in result:
                values = result['cci']
                valid_values = values[~np.isnan(values)]
                result['cci'] = {
                    'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                    'values': values.tolist()
                }
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating momentum indicators: {str(e)}")
            return {}
    
    def calculate_volatility(self,
                           df: pd.DataFrame,
                           periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all volatility indicators
        
        Args:
            df: DataFrame with OHLCV data
            periods: Optional custom periods
            
        Returns:
            Dictionary with volatility indicator results
        """
        try:
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            
            result = VolatilityIndicators.calculate_all(high, low, close, periods)
            
            # Convert numpy arrays to lists for JSON serialization
            if 'atr' in result:
                values = result['atr']
                valid_values = values[~np.isnan(values)]
                result['atr'] = {
                    'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                    'values': values.tolist()
                }
            
            if 'bollinger_bands' in result and 'values' in result['bollinger_bands']:
                bb_values = result['bollinger_bands']['values']
                for key in ['upper', 'middle', 'lower', 'bandwidth']:
                    if key in bb_values:
                        values = bb_values[key]
                        valid_values = values[~np.isnan(values)]
                        bb_values[f'{key}_current'] = float(valid_values[-1]) if len(valid_values) > 0 else None
                        bb_values[key] = values.tolist()
            
            if 'std_dev' in result:
                values = result['std_dev']
                valid_values = values[~np.isnan(values)]
                result['std_dev'] = {
                    'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                    'values': values.tolist()
                }
            
            if 'historical_volatility' in result:
                values = result['historical_volatility']
                valid_values = values[~np.isnan(values)]
                result['historical_volatility'] = {
                    'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                    'values': values.tolist()
                }
            
            if 'keltner_channels' in result:
                for key in ['upper', 'middle', 'lower']:
                    if key in result['keltner_channels']:
                        values = result['keltner_channels'][key]
                        valid_values = values[~np.isnan(values)]
                        result['keltner_channels'][f'{key}_current'] = float(valid_values[-1]) if len(valid_values) > 0 else None
                        result['keltner_channels'][key] = values.tolist()
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating volatility indicators: {str(e)}")
            return {}
    
    def calculate_volume(self,
                        df: pd.DataFrame,
                        periods: Optional[Dict[str, int]] = None) -> Dict[str, any]:
        """
        Calculate all volume indicators
        
        Args:
            df: DataFrame with OHLCV data
            periods: Optional custom periods
            
        Returns:
            Dictionary with volume indicator results
        """
        try:
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            volume = df['volume'].values
            
            result = VolumeIndicators.calculate_all(high, low, close, volume, periods)
            
            # Convert numpy arrays to lists for JSON serialization
            for key in ['obv', 'volume_sma', 'volume_roc', 'mfi', 'ad_line', 'cmf', 'volume_ratio']:
                if key in result:
                    values = result[key]
                    if isinstance(values, np.ndarray):
                        valid_values = values[~np.isnan(values)]
                        result[key] = {
                            'current': float(valid_values[-1]) if len(valid_values) > 0 else None,
                            'values': values.tolist()
                        }
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating volume indicators: {str(e)}")
            return {}
    
    def calculate_all(self,
                     symbol: str,
                     days: int = 200,
                     custom_periods: Optional[Dict[str, any]] = None) -> Dict[str, any]:
        """
        Calculate all indicators for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days of historical data
            custom_periods: Optional custom periods for indicators
            
        Returns:
            Dictionary with all indicator results
        """
        try:
            # Fetch OHLCV data
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) == 0:
                return {
                    'success': False,
                    'error': f'No OHLCV data found for {symbol}',
                    'symbol': symbol
                }
            
            # Extract custom periods if provided
            ma_periods = custom_periods.get('moving_averages') if custom_periods else None
            momentum_periods = custom_periods.get('momentum') if custom_periods else None
            volatility_periods = custom_periods.get('volatility') if custom_periods else None
            volume_periods = custom_periods.get('volume') if custom_periods else None
            
            # Calculate all indicators
            result = {
                'success': True,
                'symbol': symbol,
                'data_points': len(df),
                'date_range': {
                    'start': df.index[0].isoformat(),
                    'end': df.index[-1].isoformat()
                },
                'current_price': float(df['close'].iloc[-1]),
                'indicators': {
                    'moving_averages': self.calculate_moving_averages(df, ma_periods),
                    'momentum': self.calculate_momentum(df, momentum_periods),
                    'volatility': self.calculate_volatility(df, volatility_periods),
                    'volume': self.calculate_volume(df, volume_periods)
                },
                'calculated_at': datetime.now().isoformat()
            }
            
            logger.info(f"Successfully calculated all indicators for {symbol}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def get_indicator_summary(self, symbol: str) -> Dict[str, any]:
        """
        Get a summary of key indicators for quick analysis
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with key indicator values
        """
        try:
            # Fetch OHLCV data (last 100 days should be enough)
            df = self.get_ohlcv_data(symbol, days=100)
            
            if df is None or len(df) == 0:
                return {
                    'success': False,
                    'error': f'No OHLCV data found for {symbol}'
                }
            
            close = df['close'].values
            high = df['high'].values
            low = df['low'].values
            volume = df['volume'].values
            
            # Calculate key indicators
            sma_20 = MovingAverages.sma(close, 20)
            sma_50 = MovingAverages.sma(close, 50)
            ema_20 = MovingAverages.ema(close, 20)
            rsi_14 = MomentumIndicators.rsi(close, 14)
            macd_data = MomentumIndicators.macd(close)
            atr_14 = VolatilityIndicators.atr(high, low, close, 14)
            bb_data = VolatilityIndicators.bollinger_bands(close, 20, 2.0)
            
            # Get current values
            current_price = float(close[-1])
            
            summary = {
                'success': True,
                'symbol': symbol,
                'current_price': current_price,
                'date': df.index[-1].isoformat(),
                'trend': {
                    'sma_20': float(sma_20[-1]) if not np.isnan(sma_20[-1]) else None,
                    'sma_50': float(sma_50[-1]) if not np.isnan(sma_50[-1]) else None,
                    'ema_20': float(ema_20[-1]) if not np.isnan(ema_20[-1]) else None,
                    'price_vs_sma20': 'above' if current_price > sma_20[-1] else 'below',
                    'price_vs_sma50': 'above' if current_price > sma_50[-1] else 'below'
                },
                'momentum': {
                    'rsi_14': float(rsi_14[-1]) if not np.isnan(rsi_14[-1]) else None,
                    'rsi_signal': 'overbought' if rsi_14[-1] > 70 else 'oversold' if rsi_14[-1] < 30 else 'neutral',
                    'macd': float(macd_data['macd'][-1]) if not np.isnan(macd_data['macd'][-1]) else None,
                    'macd_signal': float(macd_data['signal'][-1]) if not np.isnan(macd_data['signal'][-1]) else None,
                    'macd_histogram': float(macd_data['histogram'][-1]) if not np.isnan(macd_data['histogram'][-1]) else None
                },
                'volatility': {
                    'atr_14': float(atr_14[-1]) if not np.isnan(atr_14[-1]) else None,
                    'bb_upper': float(bb_data['upper'][-1]) if not np.isnan(bb_data['upper'][-1]) else None,
                    'bb_middle': float(bb_data['middle'][-1]) if not np.isnan(bb_data['middle'][-1]) else None,
                    'bb_lower': float(bb_data['lower'][-1]) if not np.isnan(bb_data['lower'][-1]) else None,
                    'bb_position': ('above_upper' if current_price > bb_data['upper'][-1] else 
                                   'below_lower' if current_price < bb_data['lower'][-1] else 'within_bands')
                },
                'volume': {
                    'current': float(volume[-1]),
                    'average_20': float(np.mean(volume[-20:])),
                    'volume_ratio': float(volume[-1] / np.mean(volume[-20:]))
                }
            }
            
            return summary
            
        except Exception as e:
            logger.error(f"Error getting indicator summary for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }


def calculate_indicators(db: Session, 
                        symbol: str, 
                        days: int = 200) -> Dict[str, any]:
    """
    Convenience function to calculate all indicators
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days of historical data
        
    Returns:
        Dictionary with all indicator results
    """
    calculator = IndicatorCalculator(db)
    return calculator.calculate_all(symbol, days)


def get_indicator_summary(db: Session, symbol: str) -> Dict[str, any]:
    """
    Convenience function to get indicator summary
    
    Args:
        db: Database session
        symbol: Stock symbol
        
    Returns:
        Dictionary with key indicator values
    """
    calculator = IndicatorCalculator(db)
    return calculator.get_indicator_summary(symbol)
