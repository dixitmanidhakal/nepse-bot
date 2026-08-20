"""
Pattern Detector Orchestrator

This module orchestrates all pattern detection components:
- Support/Resistance levels
- Trend analysis
- Chart patterns
- Breakout detection

Provides a unified interface for pattern detection.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime, date
from sqlalchemy.orm import Session
import logging

from app.models.stock import Stock
from app.models.pattern import Pattern, PatternType, PatternStatus
from app.components.support_resistance import SupportResistanceDetector
from app.components.trend_analyzer import TrendAnalyzer
from app.components.chart_patterns import ChartPatternDetector

logger = logging.getLogger(__name__)


class PatternDetector:
    """
    Pattern Detector Orchestrator
    
    Coordinates all pattern detection components and provides
    a unified interface for comprehensive pattern analysis.
    """
    
    def __init__(self, db: Session):
        """
        Initialize detector with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.sr_detector = SupportResistanceDetector(db)
        self.trend_analyzer = TrendAnalyzer(db)
        self.chart_detector = ChartPatternDetector(db)
    
    def detect_all_patterns(self,
                           symbol: str,
                           days: int = 120,
                           save_to_db: bool = False) -> Dict[str, any]:
        """
        Detect all patterns for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            save_to_db: Whether to save patterns to database
            
        Returns:
            Dictionary with all detected patterns
        """
        try:
            # Detect support and resistance levels
            sr_levels = self.sr_detector.detect_all_levels(symbol, days)
            
            # Analyze trends
            trend_analysis = self.trend_analyzer.get_comprehensive_trend_analysis(symbol, days)
            
            # Detect chart patterns
            chart_patterns = self.chart_detector.detect_all_patterns(symbol, days)
            
            # Combine all results
            result = {
                'success': True,
                'symbol': symbol,
                'support_resistance': sr_levels,
                'trend_analysis': trend_analysis,
                'chart_patterns': chart_patterns,
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
            # Save to database if requested
            if save_to_db:
                save_result = self.save_patterns_to_db(symbol, result)
                result['saved_to_db'] = save_result
            
            return result
            
        except Exception as e:
            logger.error(f"Error detecting all patterns: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def get_pattern_summary(self, symbol: str, days: int = 90) -> Dict[str, any]:
        """
        Get a summary of key patterns for quick analysis
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with pattern summary
        """
        try:
            # Get support/resistance
            sr_result = self.sr_detector.detect_all_levels(symbol, days)
            
            # Get trend
            trend_result = self.trend_analyzer.analyze_trend(symbol, days)
            
            # Get chart patterns
            chart_result = self.chart_detector.detect_all_patterns(symbol, days)
            
            # Extract key information
            nearest_support = None
            nearest_resistance = None
            
            if sr_result.get('success'):
                support_levels = sr_result.get('support_levels', [])
                resistance_levels = sr_result.get('resistance_levels', [])
                current_price = sr_result.get('current_price', 0)
                
                # Find nearest support (below current price)
                supports_below = [s for s in support_levels if s['level'] < current_price]
                if supports_below:
                    nearest_support = max(supports_below, key=lambda x: x['level'])
                
                # Find nearest resistance (above current price)
                resistances_above = [r for r in resistance_levels if r['level'] > current_price]
                if resistances_above:
                    nearest_resistance = min(resistances_above, key=lambda x: x['level'])
            
            # Extract trend info
            primary_trend = None
            if trend_result.get('success') and trend_result.get('primary_trend'):
                primary_trend = {
                    'type': trend_result['primary_trend']['type'],
                    'strength': trend_result['primary_trend']['strength'],
                    'angle': trend_result['primary_trend']['angle']
                }
            
            # Extract chart patterns
            active_patterns = []
            if chart_result.get('success'):
                active_patterns = chart_result.get('patterns_detected', [])
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': sr_result.get('current_price'),
                'nearest_support': nearest_support,
                'nearest_resistance': nearest_resistance,
                'primary_trend': primary_trend,
                'short_term_trend': trend_result.get('short_term_trend'),
                'medium_term_trend': trend_result.get('medium_term_trend'),
                'active_patterns': active_patterns,
                'total_patterns': len(active_patterns),
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting pattern summary: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def detect_breakouts(self, symbol: str, days: int = 60) -> Dict[str, any]:
        """
        Detect breakout patterns (price breaking through support/resistance)
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with breakout information
        """
        try:
            # Get support/resistance levels
            sr_result = self.sr_detector.detect_all_levels(symbol, days)
            
            if not sr_result.get('success'):
                return {
                    'success': False,
                    'error': 'Failed to detect support/resistance',
                    'symbol': symbol
                }
            
            current_price = sr_result.get('current_price', 0)
            support_levels = sr_result.get('support_levels', [])
            resistance_levels = sr_result.get('resistance_levels', [])
            
            breakouts = []
            
            # Check for resistance breakouts (bullish)
            for resistance in resistance_levels:
                level = resistance['level']
                distance_percent = ((current_price - level) / level) * 100
                
                # If price is within 2% above resistance, it's a potential breakout
                if 0 < distance_percent <= 2:
                    breakouts.append({
                        'type': 'bullish_breakout',
                        'level': level,
                        'current_price': current_price,
                        'distance_percent': round(distance_percent, 2),
                        'strength': resistance['strength'],
                        'status': 'breaking_out',
                        'description': f'Price breaking above resistance at {level}'
                    })
                # If price is significantly above (>2%), breakout confirmed
                elif distance_percent > 2:
                    breakouts.append({
                        'type': 'bullish_breakout',
                        'level': level,
                        'current_price': current_price,
                        'distance_percent': round(distance_percent, 2),
                        'strength': resistance['strength'],
                        'status': 'confirmed',
                        'description': f'Confirmed breakout above resistance at {level}'
                    })
            
            # Check for support breakdowns (bearish)
            for support in support_levels:
                level = support['level']
                distance_percent = ((level - current_price) / level) * 100
                
                # If price is within 2% below support, it's a potential breakdown
                if 0 < distance_percent <= 2:
                    breakouts.append({
                        'type': 'bearish_breakdown',
                        'level': level,
                        'current_price': current_price,
                        'distance_percent': round(distance_percent, 2),
                        'strength': support['strength'],
                        'status': 'breaking_down',
                        'description': f'Price breaking below support at {level}'
                    })
                # If price is significantly below (>2%), breakdown confirmed
                elif distance_percent > 2:
                    breakouts.append({
                        'type': 'bearish_breakdown',
                        'level': level,
                        'current_price': current_price,
                        'distance_percent': round(distance_percent, 2),
                        'strength': support['strength'],
                        'status': 'confirmed',
                        'description': f'Confirmed breakdown below support at {level}'
                    })
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': current_price,
                'breakouts': breakouts,
                'total_breakouts': len(breakouts),
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting breakouts: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def get_trading_signals(self, symbol: str, days: int = 90) -> Dict[str, any]:
        """
        Generate trading signals based on detected patterns
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with trading signals
        """
        try:
            # Get pattern summary
            summary = self.get_pattern_summary(symbol, days)
            
            if not summary.get('success'):
                return {
                    'success': False,
                    'error': 'Failed to get pattern summary',
                    'symbol': symbol
                }
            
            signals = []
            signal_strength = 0
            
            # Analyze trend
            if summary.get('primary_trend'):
                trend = summary['primary_trend']
                if trend['type'] == 'uptrend' and trend['strength'] in ['strong', 'moderate']:
                    signals.append({
                        'type': 'bullish',
                        'source': 'trend',
                        'description': f"Strong {trend['type']} detected",
                        'weight': 2 if trend['strength'] == 'strong' else 1
                    })
                    signal_strength += 2 if trend['strength'] == 'strong' else 1
                elif trend['type'] == 'downtrend' and trend['strength'] in ['strong', 'moderate']:
                    signals.append({
                        'type': 'bearish',
                        'source': 'trend',
                        'description': f"Strong {trend['type']} detected",
                        'weight': -2 if trend['strength'] == 'strong' else -1
                    })
                    signal_strength -= 2 if trend['strength'] == 'strong' else 1
            
            # Analyze support/resistance
            current_price = summary.get('current_price', 0)
            nearest_support = summary.get('nearest_support')
            nearest_resistance = summary.get('nearest_resistance')
            
            if nearest_support and current_price:
                distance_to_support = ((current_price - nearest_support['level']) / current_price) * 100
                if distance_to_support < 2:  # Within 2% of support
                    signals.append({
                        'type': 'bullish',
                        'source': 'support',
                        'description': f"Price near strong support at {nearest_support['level']}",
                        'weight': 1
                    })
                    signal_strength += 1
            
            if nearest_resistance and current_price:
                distance_to_resistance = ((nearest_resistance['level'] - current_price) / current_price) * 100
                if distance_to_resistance < 2:  # Within 2% of resistance
                    signals.append({
                        'type': 'bearish',
                        'source': 'resistance',
                        'description': f"Price near strong resistance at {nearest_resistance['level']}",
                        'weight': -1
                    })
                    signal_strength -= 1
            
            # Analyze chart patterns
            for pattern in summary.get('active_patterns', []):
                pattern_type = pattern.get('pattern_type', '')
                
                if 'double_bottom' in pattern_type or 'inverse' in pattern_type or 'ascending' in pattern_type:
                    signals.append({
                        'type': 'bullish',
                        'source': 'pattern',
                        'description': f"{pattern.get('pattern_name')} detected",
                        'weight': 2
                    })
                    signal_strength += 2
                elif 'double_top' in pattern_type or 'head_and_shoulders' in pattern_type or 'descending' in pattern_type:
                    signals.append({
                        'type': 'bearish',
                        'source': 'pattern',
                        'description': f"{pattern.get('pattern_name')} detected",
                        'weight': -2
                    })
                    signal_strength -= 2
            
            # Determine overall signal
            if signal_strength >= 3:
                overall_signal = 'strong_buy'
            elif signal_strength >= 1:
                overall_signal = 'buy'
            elif signal_strength <= -3:
                overall_signal = 'strong_sell'
            elif signal_strength <= -1:
                overall_signal = 'sell'
            else:
                overall_signal = 'neutral'
            
            return {
                'success': True,
                'symbol': symbol,
                'overall_signal': overall_signal,
                'signal_strength': signal_strength,
                'signals': signals,
                'total_signals': len(signals),
                'current_price': current_price,
                'generated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error generating trading signals: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def save_patterns_to_db(self, symbol: str, patterns_data: Dict) -> Dict[str, any]:
        """
        Save detected patterns to database
        
        Args:
            symbol: Stock symbol
            patterns_data: Dictionary with pattern detection results
            
        Returns:
            Dictionary with save results
        """
        try:
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': 'Stock not found'
                }
            
            saved_count = 0
            
            # Save support/resistance levels
            sr_data = patterns_data.get('support_resistance', {})
            if sr_data.get('success'):
                all_levels = sr_data.get('all_levels', [])
                for level in all_levels[:10]:  # Save top 10 levels
                    pattern = Pattern(
                        stock_id=stock.id,
                        pattern_type=PatternType.SUPPORT if level['type'] == 'support' else PatternType.RESISTANCE,
                        status=PatternStatus.CONFIRMED,
                        pattern_name=f"{level['type'].title()} at {level['level']}",
                        description=f"Detected {level['type']} with {level['touches']} touches",
                        level_1=level['level'],
                        strength=level['strength'],
                        touches=level['touches'],
                        first_detected=date.today(),
                        timeframe='Daily'
                    )
                    self.db.add(pattern)
                    saved_count += 1
            
            # Save chart patterns
            chart_data = patterns_data.get('chart_patterns', {})
            if chart_data.get('success'):
                for pattern_data in chart_data.get('patterns_detected', []):
                    # Map pattern type to enum
                    pattern_type_map = {
                        'double_top': PatternType.DOUBLE_TOP,
                        'double_bottom': PatternType.DOUBLE_BOTTOM,
                        'head_and_shoulders': PatternType.HEAD_SHOULDERS,
                        'triangle_ascending': PatternType.TRIANGLE_ASCENDING,
                        'triangle_descending': PatternType.TRIANGLE_DESCENDING,
                        'triangle_symmetrical': PatternType.TRIANGLE_SYMMETRICAL,
                        'flag_bullish': PatternType.FLAG_BULLISH,
                        'flag_bearish': PatternType.FLAG_BEARISH
                    }
                    
                    pattern_type_str = pattern_data.get('pattern_type', '')
                    pattern_type_enum = pattern_type_map.get(pattern_type_str)
                    
                    if pattern_type_enum:
                        pattern = Pattern(
                            stock_id=stock.id,
                            pattern_type=pattern_type_enum,
                            status=PatternStatus.FORMING,
                            pattern_name=pattern_data.get('pattern_name', ''),
                            description=pattern_data.get('description', ''),
                            first_detected=date.today(),
                            timeframe='Daily'
                        )
                        self.db.add(pattern)
                        saved_count += 1
            
            self.db.commit()
            
            return {
                'success': True,
                'saved_count': saved_count,
                'message': f'Saved {saved_count} patterns to database'
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving patterns to database: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


def detect_patterns(db: Session, symbol: str, days: int = 120) -> Dict[str, any]:
    """
    Convenience function to detect all patterns
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days to analyze
        
    Returns:
        Dictionary with all detected patterns
    """
    detector = PatternDetector(db)
    return detector.detect_all_patterns(symbol, days)


def get_trading_signals(db: Session, symbol: str) -> Dict[str, any]:
    """
    Convenience function to get trading signals
    
    Args:
        db: Database session
        symbol: Stock symbol
        
    Returns:
        Dictionary with trading signals
    """
    detector = PatternDetector(db)
    return detector.get_trading_signals(symbol)
