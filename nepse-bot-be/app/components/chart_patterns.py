"""
Chart Patterns Detector

This module detects various chart patterns including:
- Reversal patterns (Double Top/Bottom, Head & Shoulders)
- Continuation patterns (Triangles, Flags, Pennants, Wedges)
- Breakout patterns
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from scipy.signal import argrelextrema
import logging

from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.models.pattern import Pattern, PatternType, PatternStatus

logger = logging.getLogger(__name__)


class ChartPatternDetector:
    """
    Chart Pattern Detector
    
    Detects various chart patterns from price data.
    """
    
    def __init__(self, db: Session):
        """
        Initialize detector with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_ohlcv_data(self, symbol: str, days: int = 180) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data for analysis"""
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
    
    def find_peaks_and_troughs(self, prices: np.ndarray, order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """Find peaks (highs) and troughs (lows) in price data"""
        peaks = argrelextrema(prices, np.greater_equal, order=order)[0]
        troughs = argrelextrema(prices, np.less_equal, order=order)[0]
        return peaks, troughs
    
    def detect_double_top(self, symbol: str, days: int = 90, tolerance: float = 0.03) -> Dict[str, any]:
        """
        Detect Double Top pattern (bearish reversal)
        
        Pattern: Two peaks at similar levels with a trough between them
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 30:
                return {'success': False, 'error': 'Insufficient data', 'symbol': symbol}
            
            highs = df['high'].values
            peaks, troughs = self.find_peaks_and_troughs(highs, order=5)
            
            if len(peaks) < 2:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            # Look for two recent peaks at similar levels
            for i in range(len(peaks) - 1):
                peak1_idx = peaks[i]
                peak2_idx = peaks[i + 1]
                
                peak1_price = highs[peak1_idx]
                peak2_price = highs[peak2_idx]
                
                # Check if peaks are at similar levels
                price_diff = abs(peak1_price - peak2_price) / peak1_price
                
                if price_diff <= tolerance:
                    # Find trough between peaks
                    between_troughs = troughs[(troughs > peak1_idx) & (troughs < peak2_idx)]
                    
                    if len(between_troughs) > 0:
                        trough_idx = between_troughs[0]
                        trough_price = highs[trough_idx]
                        
                        # Calculate pattern metrics
                        pattern_height = peak1_price - trough_price
                        target_price = trough_price - pattern_height
                        
                        # Check if pattern is recent
                        days_since_peak2 = len(highs) - peak2_idx
                        
                        if days_since_peak2 <= 10:  # Pattern formed recently
                            return {
                                'success': True,
                                'symbol': symbol,
                                'pattern_detected': True,
                                'pattern_type': 'double_top',
                                'pattern_name': 'Double Top',
                                'description': 'Bearish reversal pattern with two peaks',
                                'peak1_price': round(peak1_price, 2),
                                'peak2_price': round(peak2_price, 2),
                                'trough_price': round(trough_price, 2),
                                'neckline': round(trough_price, 2),
                                'target_price': round(target_price, 2),
                                'pattern_height': round(pattern_height, 2),
                                'current_price': round(highs[-1], 2),
                                'status': 'forming',
                                'confidence': 'high' if price_diff < 0.01 else 'medium',
                                'detected_at': datetime.now().isoformat()
                            }
            
            return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
        except Exception as e:
            logger.error(f"Error detecting double top: {str(e)}")
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def detect_double_bottom(self, symbol: str, days: int = 90, tolerance: float = 0.03) -> Dict[str, any]:
        """
        Detect Double Bottom pattern (bullish reversal)
        
        Pattern: Two troughs at similar levels with a peak between them
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 30:
                return {'success': False, 'error': 'Insufficient data', 'symbol': symbol}
            
            lows = df['low'].values
            peaks, troughs = self.find_peaks_and_troughs(lows, order=5)
            
            if len(troughs) < 2:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            # Look for two recent troughs at similar levels
            for i in range(len(troughs) - 1):
                trough1_idx = troughs[i]
                trough2_idx = troughs[i + 1]
                
                trough1_price = lows[trough1_idx]
                trough2_price = lows[trough2_idx]
                
                # Check if troughs are at similar levels
                price_diff = abs(trough1_price - trough2_price) / trough1_price
                
                if price_diff <= tolerance:
                    # Find peak between troughs
                    between_peaks = peaks[(peaks > trough1_idx) & (peaks < trough2_idx)]
                    
                    if len(between_peaks) > 0:
                        peak_idx = between_peaks[0]
                        peak_price = lows[peak_idx]
                        
                        # Calculate pattern metrics
                        pattern_height = peak_price - trough1_price
                        target_price = peak_price + pattern_height
                        
                        # Check if pattern is recent
                        days_since_trough2 = len(lows) - trough2_idx
                        
                        if days_since_trough2 <= 10:
                            return {
                                'success': True,
                                'symbol': symbol,
                                'pattern_detected': True,
                                'pattern_type': 'double_bottom',
                                'pattern_name': 'Double Bottom',
                                'description': 'Bullish reversal pattern with two troughs',
                                'trough1_price': round(trough1_price, 2),
                                'trough2_price': round(trough2_price, 2),
                                'peak_price': round(peak_price, 2),
                                'neckline': round(peak_price, 2),
                                'target_price': round(target_price, 2),
                                'pattern_height': round(pattern_height, 2),
                                'current_price': round(lows[-1], 2),
                                'status': 'forming',
                                'confidence': 'high' if price_diff < 0.01 else 'medium',
                                'detected_at': datetime.now().isoformat()
                            }
            
            return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
        except Exception as e:
            logger.error(f"Error detecting double bottom: {str(e)}")
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def detect_head_and_shoulders(self, symbol: str, days: int = 120) -> Dict[str, any]:
        """
        Detect Head and Shoulders pattern (bearish reversal)
        
        Pattern: Three peaks with middle peak (head) higher than shoulders
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 40:
                return {'success': False, 'error': 'Insufficient data', 'symbol': symbol}
            
            highs = df['high'].values
            peaks, troughs = self.find_peaks_and_troughs(highs, order=5)
            
            if len(peaks) < 3 or len(troughs) < 2:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            # Look for three consecutive peaks
            for i in range(len(peaks) - 2):
                left_shoulder_idx = peaks[i]
                head_idx = peaks[i + 1]
                right_shoulder_idx = peaks[i + 2]
                
                left_shoulder = highs[left_shoulder_idx]
                head = highs[head_idx]
                right_shoulder = highs[right_shoulder_idx]
                
                # Check if head is higher than shoulders
                if head > left_shoulder and head > right_shoulder:
                    # Check if shoulders are at similar levels (within 5%)
                    shoulder_diff = abs(left_shoulder - right_shoulder) / left_shoulder
                    
                    if shoulder_diff <= 0.05:
                        # Find neckline (troughs between peaks)
                        left_trough_idx = troughs[(troughs > left_shoulder_idx) & (troughs < head_idx)]
                        right_trough_idx = troughs[(troughs > head_idx) & (troughs < right_shoulder_idx)]
                        
                        if len(left_trough_idx) > 0 and len(right_trough_idx) > 0:
                            neckline = (highs[left_trough_idx[0]] + highs[right_trough_idx[0]]) / 2
                            
                            # Calculate target
                            pattern_height = head - neckline
                            target_price = neckline - pattern_height
                            
                            # Check if pattern is recent
                            days_since = len(highs) - right_shoulder_idx
                            
                            if days_since <= 15:
                                return {
                                    'success': True,
                                    'symbol': symbol,
                                    'pattern_detected': True,
                                    'pattern_type': 'head_and_shoulders',
                                    'pattern_name': 'Head and Shoulders',
                                    'description': 'Bearish reversal with three peaks',
                                    'left_shoulder': round(left_shoulder, 2),
                                    'head': round(head, 2),
                                    'right_shoulder': round(right_shoulder, 2),
                                    'neckline': round(neckline, 2),
                                    'target_price': round(target_price, 2),
                                    'pattern_height': round(pattern_height, 2),
                                    'current_price': round(highs[-1], 2),
                                    'status': 'forming',
                                    'confidence': 'high' if shoulder_diff < 0.02 else 'medium',
                                    'detected_at': datetime.now().isoformat()
                                }
            
            return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
        except Exception as e:
            logger.error(f"Error detecting head and shoulders: {str(e)}")
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def detect_triangle(self, symbol: str, days: int = 60) -> Dict[str, any]:
        """
        Detect Triangle patterns (Ascending, Descending, Symmetrical)
        
        Triangles are continuation patterns formed by converging trend lines
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 30:
                return {'success': False, 'error': 'Insufficient data', 'symbol': symbol}
            
            highs = df['high'].values
            lows = df['low'].values
            
            # Fit trend lines to recent highs and lows
            recent_period = min(30, len(highs))
            x = np.arange(recent_period)
            
            recent_highs = highs[-recent_period:]
            recent_lows = lows[-recent_period:]
            
            # Calculate slopes
            high_slope = np.polyfit(x, recent_highs, 1)[0]
            low_slope = np.polyfit(x, recent_lows, 1)[0]
            
            # Determine triangle type
            triangle_type = None
            description = None
            
            if abs(high_slope) < 0.1 and low_slope > 0.1:
                # Ascending triangle (flat top, rising bottom)
                triangle_type = 'ascending'
                description = 'Bullish continuation pattern with flat resistance'
            elif high_slope < -0.1 and abs(low_slope) < 0.1:
                # Descending triangle (falling top, flat bottom)
                triangle_type = 'descending'
                description = 'Bearish continuation pattern with flat support'
            elif high_slope < -0.05 and low_slope > 0.05:
                # Symmetrical triangle (converging lines)
                triangle_type = 'symmetrical'
                description = 'Neutral pattern indicating consolidation'
            
            if triangle_type:
                # Calculate apex (where lines would meet)
                high_line = np.polyfit(x, recent_highs, 1)
                low_line = np.polyfit(x, recent_lows, 1)
                
                # Estimate days to apex
                if abs(high_slope - low_slope) > 0.01:
                    days_to_apex = int((low_line[1] - high_line[1]) / (high_slope - low_slope))
                else:
                    days_to_apex = 999
                
                current_price = highs[-1]
                upper_line_current = high_line[0] * (recent_period - 1) + high_line[1]
                lower_line_current = low_line[0] * (recent_period - 1) + low_line[1]
                
                return {
                    'success': True,
                    'symbol': symbol,
                    'pattern_detected': True,
                    'pattern_type': f'triangle_{triangle_type}',
                    'pattern_name': f'{triangle_type.title()} Triangle',
                    'description': description,
                    'triangle_type': triangle_type,
                    'upper_line': round(upper_line_current, 2),
                    'lower_line': round(lower_line_current, 2),
                    'current_price': round(current_price, 2),
                    'days_to_apex': days_to_apex if days_to_apex < 999 else None,
                    'high_slope': round(high_slope, 4),
                    'low_slope': round(low_slope, 4),
                    'status': 'forming',
                    'detected_at': datetime.now().isoformat()
                }
            
            return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
        except Exception as e:
            logger.error(f"Error detecting triangle: {str(e)}")
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def detect_flag(self, symbol: str, days: int = 40) -> Dict[str, any]:
        """
        Detect Flag patterns (Bullish/Bearish continuation)
        
        Flag: Sharp move followed by consolidation in opposite direction
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 20:
                return {'success': False, 'error': 'Insufficient data', 'symbol': symbol}
            
            closes = df['close'].values
            
            # Look for sharp move (flagpole)
            pole_period = 10
            consolidation_period = 10
            
            if len(closes) < pole_period + consolidation_period:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            # Check for sharp move
            pole_start = closes[-(pole_period + consolidation_period)]
            pole_end = closes[-consolidation_period]
            pole_change = ((pole_end - pole_start) / pole_start) * 100
            
            # Require at least 5% move for flagpole
            if abs(pole_change) < 5:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            # Check consolidation (flag)
            consolidation_prices = closes[-consolidation_period:]
            consolidation_slope = np.polyfit(np.arange(len(consolidation_prices)), consolidation_prices, 1)[0]
            
            # Determine flag type
            if pole_change > 0 and consolidation_slope < 0:
                # Bullish flag (up move, down consolidation)
                flag_type = 'bullish'
                target = pole_end + abs(pole_end - pole_start)
            elif pole_change < 0 and consolidation_slope > 0:
                # Bearish flag (down move, up consolidation)
                flag_type = 'bearish'
                target = pole_end - abs(pole_end - pole_start)
            else:
                return {'success': True, 'symbol': symbol, 'pattern_detected': False}
            
            return {
                'success': True,
                'symbol': symbol,
                'pattern_detected': True,
                'pattern_type': f'flag_{flag_type}',
                'pattern_name': f'{flag_type.title()} Flag',
                'description': f'{flag_type.title()} continuation pattern',
                'flag_type': flag_type,
                'pole_start': round(pole_start, 2),
                'pole_end': round(pole_end, 2),
                'pole_change_percent': round(pole_change, 2),
                'current_price': round(closes[-1], 2),
                'target_price': round(target, 2),
                'status': 'forming',
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting flag: {str(e)}")
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def detect_all_patterns(self, symbol: str, days: int = 120) -> Dict[str, any]:
        """
        Detect all chart patterns for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with all detected patterns
        """
        try:
            patterns_detected = []
            
            # Detect each pattern type
            double_top = self.detect_double_top(symbol, days)
            if double_top.get('pattern_detected'):
                patterns_detected.append(double_top)
            
            double_bottom = self.detect_double_bottom(symbol, days)
            if double_bottom.get('pattern_detected'):
                patterns_detected.append(double_bottom)
            
            head_shoulders = self.detect_head_and_shoulders(symbol, days)
            if head_shoulders.get('pattern_detected'):
                patterns_detected.append(head_shoulders)
            
            triangle = self.detect_triangle(symbol, days)
            if triangle.get('pattern_detected'):
                patterns_detected.append(triangle)
            
            flag = self.detect_flag(symbol, days)
            if flag.get('pattern_detected'):
                patterns_detected.append(flag)
            
            return {
                'success': True,
                'symbol': symbol,
                'patterns_detected': patterns_detected,
                'total_patterns': len(patterns_detected),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting all patterns: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }


def detect_chart_patterns(db: Session, symbol: str, days: int = 120) -> Dict[str, any]:
    """
    Convenience function to detect chart patterns
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days to analyze
        
    Returns:
        Dictionary with detected patterns
    """
    detector = ChartPatternDetector(db)
    return detector.detect_all_patterns(symbol, days)
