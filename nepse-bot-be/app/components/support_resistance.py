"""
Support and Resistance Detector

This module identifies support and resistance levels from price data.
Uses multiple methods including:
- Local minima/maxima
- Pivot points
- Volume profile
- Historical price clusters
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date
from sqlalchemy.orm import Session
from scipy.signal import argrelextrema
import logging

from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.models.pattern import Pattern, PatternType, PatternStatus

logger = logging.getLogger(__name__)


class SupportResistanceDetector:
    """
    Support and Resistance Level Detector
    
    Identifies key price levels where stock tends to find support or resistance.
    """
    
    def __init__(self, db: Session):
        """
        Initialize detector with database session
        
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
            
            # Fetch OHLCV data
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
            df = df.sort_values('date').reset_index(drop=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {str(e)}")
            return None
    
    def find_local_extrema(self, 
                          prices: np.ndarray, 
                          order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """
        Find local minima and maxima in price data
        
        Args:
            prices: Price array
            order: Number of points on each side to compare
            
        Returns:
            Tuple of (minima_indices, maxima_indices)
        """
        try:
            # Find local minima (support)
            minima = argrelextrema(prices, np.less_equal, order=order)[0]
            
            # Find local maxima (resistance)
            maxima = argrelextrema(prices, np.greater_equal, order=order)[0]
            
            return minima, maxima
            
        except Exception as e:
            logger.error(f"Error finding local extrema: {str(e)}")
            return np.array([]), np.array([])
    
    def cluster_levels(self, 
                      levels: List[float], 
                      tolerance: float = 0.02) -> List[Dict]:
        """
        Cluster similar price levels together
        
        Args:
            levels: List of price levels
            tolerance: Percentage tolerance for clustering (default: 2%)
            
        Returns:
            List of clustered levels with strength
        """
        if not levels:
            return []
        
        try:
            levels = sorted(levels)
            clusters = []
            current_cluster = [levels[0]]
            
            for level in levels[1:]:
                # Check if level is within tolerance of cluster mean
                cluster_mean = np.mean(current_cluster)
                if abs(level - cluster_mean) / cluster_mean <= tolerance:
                    current_cluster.append(level)
                else:
                    # Save current cluster and start new one
                    clusters.append({
                        'level': np.mean(current_cluster),
                        'touches': len(current_cluster),
                        'min': min(current_cluster),
                        'max': max(current_cluster)
                    })
                    current_cluster = [level]
            
            # Add last cluster
            if current_cluster:
                clusters.append({
                    'level': np.mean(current_cluster),
                    'touches': len(current_cluster),
                    'min': min(current_cluster),
                    'max': max(current_cluster)
                })
            
            # Sort by number of touches (strength)
            clusters.sort(key=lambda x: x['touches'], reverse=True)
            
            return clusters
            
        except Exception as e:
            logger.error(f"Error clustering levels: {str(e)}")
            return []
    
    def calculate_level_strength(self,
                                 level: float,
                                 prices: np.ndarray,
                                 volumes: np.ndarray,
                                 tolerance: float = 0.02) -> float:
        """
        Calculate strength of a support/resistance level
        
        Args:
            level: Price level
            prices: Price array
            volumes: Volume array
            tolerance: Percentage tolerance
            
        Returns:
            Strength score (0-1)
        """
        try:
            # Count touches
            touches = np.sum(np.abs(prices - level) / level <= tolerance)
            
            # Calculate volume at level
            mask = np.abs(prices - level) / level <= tolerance
            volume_at_level = np.sum(volumes[mask]) if np.any(mask) else 0
            avg_volume = np.mean(volumes)
            
            # Normalize scores
            touch_score = min(touches / 10, 1.0)  # Max 10 touches = 1.0
            volume_score = min(volume_at_level / (avg_volume * 5), 1.0)
            
            # Combined strength
            strength = (touch_score * 0.7 + volume_score * 0.3)
            
            return float(strength)
            
        except Exception as e:
            logger.error(f"Error calculating level strength: {str(e)}")
            return 0.0
    
    def detect_support_levels(self,
                             symbol: str,
                             days: int = 180,
                             min_touches: int = 2,
                             max_levels: int = 5) -> Dict[str, any]:
        """
        Detect support levels for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            min_touches: Minimum touches to consider valid
            max_levels: Maximum number of levels to return
            
        Returns:
            Dictionary with support levels and metadata
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 20:
                return {
                    'success': False,
                    'error': 'Insufficient data',
                    'symbol': symbol
                }
            
            # Find local minima (potential support)
            lows = df['low'].values
            minima_idx, _ = self.find_local_extrema(lows, order=5)
            
            if len(minima_idx) == 0:
                return {
                    'success': True,
                    'symbol': symbol,
                    'support_levels': [],
                    'message': 'No support levels detected'
                }
            
            # Get support prices
            support_prices = lows[minima_idx].tolist()
            
            # Cluster similar levels
            clusters = self.cluster_levels(support_prices, tolerance=0.02)
            
            # Filter by minimum touches
            valid_clusters = [c for c in clusters if c['touches'] >= min_touches]
            
            # Calculate strength for each level
            support_levels = []
            for cluster in valid_clusters[:max_levels]:
                strength = self.calculate_level_strength(
                    cluster['level'],
                    df['close'].values,
                    df['volume'].values
                )
                
                support_levels.append({
                    'level': round(cluster['level'], 2),
                    'touches': cluster['touches'],
                    'strength': round(strength, 3),
                    'min_price': round(cluster['min'], 2),
                    'max_price': round(cluster['max'], 2),
                    'type': 'support'
                })
            
            # Get current price for context
            current_price = float(df['close'].iloc[-1])
            
            # Classify levels relative to current price
            for level in support_levels:
                distance = ((current_price - level['level']) / current_price) * 100
                level['distance_percent'] = round(distance, 2)
                level['status'] = 'below' if current_price > level['level'] else 'above'
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'support_levels': support_levels,
                'total_levels': len(support_levels),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting support levels: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def detect_resistance_levels(self,
                                symbol: str,
                                days: int = 180,
                                min_touches: int = 2,
                                max_levels: int = 5) -> Dict[str, any]:
        """
        Detect resistance levels for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            min_touches: Minimum touches to consider valid
            max_levels: Maximum number of levels to return
            
        Returns:
            Dictionary with resistance levels and metadata
        """
        try:
            df = self.get_ohlcv_data(symbol, days)
            
            if df is None or len(df) < 20:
                return {
                    'success': False,
                    'error': 'Insufficient data',
                    'symbol': symbol
                }
            
            # Find local maxima (potential resistance)
            highs = df['high'].values
            _, maxima_idx = self.find_local_extrema(highs, order=5)
            
            if len(maxima_idx) == 0:
                return {
                    'success': True,
                    'symbol': symbol,
                    'resistance_levels': [],
                    'message': 'No resistance levels detected'
                }
            
            # Get resistance prices
            resistance_prices = highs[maxima_idx].tolist()
            
            # Cluster similar levels
            clusters = self.cluster_levels(resistance_prices, tolerance=0.02)
            
            # Filter by minimum touches
            valid_clusters = [c for c in clusters if c['touches'] >= min_touches]
            
            # Calculate strength for each level
            resistance_levels = []
            for cluster in valid_clusters[:max_levels]:
                strength = self.calculate_level_strength(
                    cluster['level'],
                    df['close'].values,
                    df['volume'].values
                )
                
                resistance_levels.append({
                    'level': round(cluster['level'], 2),
                    'touches': cluster['touches'],
                    'strength': round(strength, 3),
                    'min_price': round(cluster['min'], 2),
                    'max_price': round(cluster['max'], 2),
                    'type': 'resistance'
                })
            
            # Get current price for context
            current_price = float(df['close'].iloc[-1])
            
            # Classify levels relative to current price
            for level in resistance_levels:
                distance = ((level['level'] - current_price) / current_price) * 100
                level['distance_percent'] = round(distance, 2)
                level['status'] = 'above' if current_price < level['level'] else 'below'
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'resistance_levels': resistance_levels,
                'total_levels': len(resistance_levels),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting resistance levels: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def detect_all_levels(self,
                         symbol: str,
                         days: int = 180,
                         min_touches: int = 2) -> Dict[str, any]:
        """
        Detect both support and resistance levels
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            min_touches: Minimum touches to consider valid
            
        Returns:
            Dictionary with all levels
        """
        try:
            support_result = self.detect_support_levels(symbol, days, min_touches)
            resistance_result = self.detect_resistance_levels(symbol, days, min_touches)
            
            if not support_result['success'] or not resistance_result['success']:
                return {
                    'success': False,
                    'error': 'Failed to detect levels',
                    'symbol': symbol
                }
            
            # Combine results
            all_levels = (
                support_result.get('support_levels', []) +
                resistance_result.get('resistance_levels', [])
            )
            
            # Sort by strength
            all_levels.sort(key=lambda x: x['strength'], reverse=True)
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': support_result.get('current_price'),
                'support_levels': support_result.get('support_levels', []),
                'resistance_levels': resistance_result.get('resistance_levels', []),
                'all_levels': all_levels,
                'total_support': len(support_result.get('support_levels', [])),
                'total_resistance': len(resistance_result.get('resistance_levels', [])),
                'analyzed_days': days,
                'detected_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error detecting all levels: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol
            }
    
    def save_levels_to_db(self, symbol: str, levels: List[Dict]) -> Dict[str, any]:
        """
        Save detected levels to database as patterns
        
        Args:
            symbol: Stock symbol
            levels: List of detected levels
            
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
            
            for level_data in levels:
                # Create pattern record
                pattern = Pattern(
                    stock_id=stock.id,
                    pattern_type=PatternType.SUPPORT if level_data['type'] == 'support' else PatternType.RESISTANCE,
                    status=PatternStatus.CONFIRMED,
                    pattern_name=f"{level_data['type'].title()} at {level_data['level']}",
                    description=f"Detected {level_data['type']} level with {level_data['touches']} touches",
                    level_1=level_data['level'],
                    strength=level_data['strength'],
                    touches=level_data['touches'],
                    first_detected=date.today(),
                    last_updated=date.today(),
                    timeframe='Daily'
                )
                
                self.db.add(pattern)
                saved_count += 1
            
            self.db.commit()
            
            return {
                'success': True,
                'saved_count': saved_count,
                'message': f'Saved {saved_count} levels to database'
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving levels to database: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


def detect_support_resistance(db: Session, 
                              symbol: str, 
                              days: int = 180) -> Dict[str, any]:
    """
    Convenience function to detect support and resistance levels
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days to analyze
        
    Returns:
        Dictionary with detected levels
    """
    detector = SupportResistanceDetector(db)
    return detector.detect_all_levels(symbol, days)
