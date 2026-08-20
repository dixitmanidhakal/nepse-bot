"""
Trend Analyzer

This module analyzes price trends and detects trend lines.
Identifies:
- Uptrends and downtrends
- Trend strength
- Trend channels
- Trend reversals
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date
from sqlalchemy.orm import Session
from scipy import stats
import logging

from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.models.pattern import Pattern, PatternType, PatternStatus

logger = logging.getLogger(__name__)


class TrendAnalyzer:
    """
    Trend Analyzer
    
    Analyzes price trends and detects trend lines and channels.
    """
    
    def __init__(self, db: Session):
        """
        Initialize analyzer with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_ohlcv_data(self, symbol: str, days: int = 180) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data for analysis
        
        Args:
            symbol: Stock symbol
            days: Number of days of historical data
            
        Returns:
            DataFrame with OHLCV data or None
        """
        try:
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                logger.error(f"Stock not found: {symbol}")
                return None
            
            ohlcv_records = (
                self.db.query(StockOHLCV)
                .filter(StockOHLCV.stock_id == stock.id)
                .order_by(StockOHLCV.date.desc())
                .limit(days)
                .all()
            )
            
            if not ohlcv_records:
                logger.warning(f"No OHLCV data found for {symbol}")
                return None
            
            data = {
                'date': [r.date for r in ohlcv_records],
                'open': [r.open for r in ohlcv_records],
                'high': [r.high for r in ohlcv_records],
                'low': [r.low for r in ohlcv_records],
                'close': [r.close for r in ohlcv_records],
                'volume': [r.volume for r in ohlcv_records]
            }
            
            df = pd.DataFrame(data)
            df = df.sort_values('date').reset_index(drop=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {str(e)}")
            return None
    
    def calculate_trend_strength(self, 
                                 prices: np.ndarray, 
                                 r_squared: float) -> str:
        """
        Calculate trend strength based on R-squared
        
        Args:
            prices: Price array
            r_squared: R-squared value from linear regression
            
        Returns:
            Trend strength ('weak', 'moderate', 'strong')
        """
        if r_squared >= 0.7:
            return 'strong'
        elif r_squared >= 0.4:
            return 'moderate'
        else:
            return 'weak'
    
    def detect_trend_line(self,
                         prices: np.ndarray,
                         dates: np.ndarray,
                         trend_type: str = 'uptrend') -> Optional[Dict]:
        """
        Detect trend line using linear regression
        
        Args:
            prices: Price array
            dates: Date array (as numeric)
            trend_type: 'uptrend' or 'downtrend'
            
        Returns:
            Dictionary with trend line details or None
        """
        try:
            if len(prices) < 10:
                return None
            
            # Perform linear regression
            x = np.arange(len(prices))
            slope, intercept, r_value, p_value, std_err = stats.linregress(x, prices)
            
            # Calculate R-squared
            r_squared = r_value ** 2
            
            # Check if trend matches expected type
            if trend_type == 'uptrend' and slope <= 0:
                return None
            if trend_type == 'downtrend' and slope >= 0:
                return None
            
            # Calculate trend line values
            trend_line = slope * x + intercept
            
            # Calculate current and projected values
            current_value = trend_line[-1]
            projected_10d = slope * (len(prices) + 10) + intercept
            projected_30d = slope * (len(prices) + 30) + intercept
            
            # Calculate angle (in degrees)
            angle = np.degrees(np.arctan(slope / np.mean(prices)))
            
            # Determine strength
            strength = self.calculate_trend_strength(prices, r_squared)
            
            return {
                'type': trend_type,
                'slope': float(slope),
                'intercept': float(intercept),
                'r_squared': float(r_squared),
                'strength': strength,
                'angle': float(angle),
                'current_value': float(current_value),
                'projected_10d': float(projected_10d),
                'projected_30d': float(projected_30d),
                'start_price': float(prices[0]),
                'end_price': float(prices[-1]),
                'price_change': float(prices[-1] - prices[0]),
                'price_change_percent': float(((prices[-1] - prices[0]) / prices[0]) * 100),
                'duration_days': len(prices)
            }
            
        except Exception as e:
            logger.error(f"Error detecting trend line: {str(e)}")
            return None
    
    def analyze_trend(self,
                     symbol: str,
                     days: int = 90) -> Dict[str, any]:
        """
        Analyze overall trend for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with trend analysis
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 20:
                return {
                    'success': False,
                    'error': 'Insufficient data',
                    'symbol': symbol
                }
            
            close_prices = df['close'].values
            dates = np.arange(len(close_prices))
            
            # Detect both uptrend and downtrend
            uptrend = self.detect_trend_line(close_prices, dates, 'uptrend')
            downtrend = self.detect_trend_line(close_prices, dates, 'downtrend')
            
            # Determine primary trend
            if uptrend and downtrend:
                primary_trend = uptrend if uptrend['r_squared'] > downtrend['r_squared'] else downtrend
            elif uptrend:
                primary_trend = uptrend
            elif downtrend:
                primary_trend = downtrend
            else:
                primary_trend = None
            
            # Calculate short-term trend (last 20 days)
            if len(close_prices) >= 20:
                short_term_prices = close_prices[-20:]
                short_term_dates = np.arange(len(short_term_prices))
                short_term_slope = np.polyfit(short_term_dates, short_term_prices, 1)[0]
                short_term_trend = 'bullish' if short_term_slope > 0 else 'bearish'
            else:
                short_term_trend = 'neutral'
            
            # Calculate medium-term trend (last 50 days)
            if len(close_prices) >= 50:
                medium_term_prices = close_prices[-50:]
                medium_term_dates = np.arange(len(medium_term_prices))
                medium_term_slope = np.polyfit(medium_term_dates, medium_term_prices, 1)[0]
                medium_term_trend = 'bullish' if medium_term_slope > 0 else 'bearish'
            else:
                medium_term_trend = 'neutral'
            
            # Get current price
            current_price = float(close_prices[-1])
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'primary_trend': primary_trend,
                'short_term_trend': short_term_trend,
                'medium_term_trend': medium_term_trend,
                'uptrend_detected': uptrend is not None,
                'downtrend_detected': downtrend is not None,
                'analyzed_days': days,
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing trend: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def detect_trend_channel(self,
                            symbol: str,
                            days: int = 90) -> Dict[str, any]:
        """
        Detect trend channel (parallel support and resistance lines)
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with channel details
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 30:
                return {
                    'success': False,
                    'error': 'Insufficient data',
                    'symbol': symbol
                }
            
            highs = df['high'].values
            lows = df['low'].values
            closes = df['close'].values
            x = np.arange(len(closes))
            
            # Fit trend lines to highs and lows
            high_slope, high_intercept = np.polyfit(x, highs, 1)
            low_slope, low_intercept = np.polyfit(x, lows, 1)
            
            # Calculate channel lines
            upper_line = high_slope * x + high_intercept
            lower_line = low_slope * x + low_intercept
            middle_line = (upper_line + lower_line) / 2
            
            # Calculate channel width
            channel_width = np.mean(upper_line - lower_line)
            channel_width_percent = (channel_width / np.mean(closes)) * 100
            
            # Determine channel type
            if abs(high_slope - low_slope) / abs(high_slope) < 0.1:  # Slopes are similar
                if high_slope > 0:
                    channel_type = 'ascending'
                elif high_slope < 0:
                    channel_type = 'descending'
                else:
                    channel_type = 'horizontal'
            else:
                channel_type = 'irregular'
            
            # Calculate current position in channel
            current_price = closes[-1]
            current_upper = upper_line[-1]
            current_lower = lower_line[-1]
            current_middle = middle_line[-1]
            
            position_in_channel = ((current_price - current_lower) / (current_upper - current_lower)) * 100
            
            # Determine position description
            if position_in_channel >= 80:
                position_desc = 'near_upper'
            elif position_in_channel >= 60:
                position_desc = 'upper_half'
            elif position_in_channel >= 40:
                position_desc = 'middle'
            elif position_in_channel >= 20:
                position_desc = 'lower_half'
            else:
                position_desc = 'near_lower'
            
            return {
                'success': True,
                'symbol': symbol,
                'channel_type': channel_type,
                'current_price': round(current_price, 2),
                'upper_line': round(current_upper, 2),
                'middle_line': round(current_middle, 2),
                'lower_line': round(current_lower, 2),
                'channel_width': round(channel_width, 2),
                'channel_width_percent': round(channel_width_percent, 2),
                'position_in_channel': round(position_in_channel, 2),
                'position_description': position_desc,
                'upper_slope': round(high_slope, 4),
                'lower_slope': round(low_slope, 4),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting trend channel: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def detect_trend_reversal(self,
                             symbol: str,
                             days: int = 60) -> Dict[str, any]:
        """
        Detect potential trend reversals
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with reversal signals
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 30:
                return {
                    'success': False,
                    'error': 'Insufficient data',
                    'symbol': symbol
                }
            
            closes = df['close'].values
            
            # Analyze recent trend vs longer trend
            recent_period = 10
            longer_period = 30
            
            if len(closes) < longer_period:
                return {
                    'success': False,
                    'error': 'Insufficient data for reversal detection',
                    'symbol': symbol
                }
            
            # Recent trend
            recent_prices = closes[-recent_period:]
            recent_slope = np.polyfit(np.arange(len(recent_prices)), recent_prices, 1)[0]
            
            # Longer trend
            longer_prices = closes[-longer_period:]
            longer_slope = np.polyfit(np.arange(len(longer_prices)), longer_prices, 1)[0]
            
            # Detect reversal
            reversal_detected = False
            reversal_type = None
            
            if longer_slope > 0 and recent_slope < 0:
                # Was uptrend, now downtrend
                reversal_detected = True
                reversal_type = 'bearish'
            elif longer_slope < 0 and recent_slope > 0:
                # Was downtrend, now uptrend
                reversal_detected = True
                reversal_type = 'bullish'
            
            # Calculate reversal strength
            if reversal_detected:
                slope_change = abs(recent_slope - longer_slope)
                avg_price = np.mean(closes)
                reversal_strength = min((slope_change / avg_price) * 100, 100)
            else:
                reversal_strength = 0
            
            return {
                'success': True,
                'symbol': symbol,
                'reversal_detected': reversal_detected,
                'reversal_type': reversal_type,
                'reversal_strength': round(reversal_strength, 2),
                'recent_trend': 'bullish' if recent_slope > 0 else 'bearish',
                'longer_trend': 'bullish' if longer_slope > 0 else 'bearish',
                'current_price': round(closes[-1], 2),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting trend reversal: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def get_comprehensive_trend_analysis(self,
                                        symbol: str,
                                        days: int = 90) -> Dict[str, any]:
        """
        Get comprehensive trend analysis
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with complete trend analysis
        """
        try:
            trend_analysis = self.analyze_trend(symbol, days)
            channel_analysis = self.detect_trend_channel(symbol, days)
            reversal_analysis = self.detect_trend_reversal(symbol, days)
            
            return {
                'success': True,
                'symbol': symbol,
                'trend_analysis': trend_analysis,
                'channel_analysis': channel_analysis,
                'reversal_analysis': reversal_analysis,
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in comprehensive trend analysis: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }


def analyze_trend(db: Session, symbol: str, days: int = 90) -> Dict[str, any]:
    """
    Convenience function to analyze trend
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days to analyze
        
    Returns:
        Dictionary with trend analysis
    """
    analyzer = TrendAnalyzer(db)
    return analyzer.get_comprehensive_trend_analysis(symbol, days)
