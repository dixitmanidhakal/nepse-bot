"""
Sector Analyzer Component

This module analyzes sector performance, momentum, and relative strength.
Used for sector rotation strategies and identifying bullish/bearish sectors.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc
import logging

from app.models.sector import Sector
from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV

logger = logging.getLogger(__name__)


class SectorAnalyzer:
    """
    Sector Analyzer
    
    Analyzes sector performance, momentum, and identifies
    sector rotation opportunities.
    """
    
    def __init__(self, db: Session):
        """
        Initialize analyzer with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def analyze_all_sectors(self) -> Dict[str, any]:
        """
        Analyze all sectors and rank by performance
        
        Returns:
            Dictionary with sector analysis results
        """
        try:
            sectors = self.db.query(Sector).all()
            
            if not sectors:
                return {
                    'success': False,
                    'error': 'No sectors found in database'
                }
            
            sector_analysis = []

            for sector in sectors:
                analysis = self.analyze_sector(sector.id)
                if analysis['success']:
                    sector_analysis.append(analysis['data'])

            # Sort by 30-day momentum (treat NULL DB values as 0 so sort doesn't
            # TypeError on mixed None/float comparisons)
            def _m30(x):
                v = x.get('momentum_30d')
                return float(v) if v is not None else 0.0
            sector_analysis.sort(key=_m30, reverse=True)

            # Add ranks
            for i, sector in enumerate(sector_analysis, 1):
                sector['rank'] = i

            # Identify bullish and bearish sectors (same None-safe coercion)
            bullish_sectors = [s for s in sector_analysis if _m30(s) > 5]
            bearish_sectors = [s for s in sector_analysis if _m30(s) < -5]
            
            return {
                'success': True,
                'total_sectors': len(sector_analysis),
                'bullish_sectors': len(bullish_sectors),
                'bearish_sectors': len(bearish_sectors),
                'sectors': sector_analysis,
                'top_performers': sector_analysis[:5],
                'worst_performers': sector_analysis[-5:],
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing all sectors: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_sector(self, sector_id: int) -> Dict[str, any]:
        """
        Analyze a specific sector
        
        Args:
            sector_id: Sector ID
            
        Returns:
            Dictionary with sector analysis
        """
        try:
            sector = self.db.query(Sector).filter(Sector.id == sector_id).first()
            
            if not sector:
                return {
                    'success': False,
                    'error': f'Sector not found: {sector_id}'
                }
            
            # Get stocks in sector
            stocks = self.db.query(Stock).filter(
                and_(
                    Stock.sector_id == sector_id,
                    Stock.is_active == True
                )
            ).all()
            
            # Calculate sector metrics
            total_stocks = len(stocks)
            advancing = sum(1 for s in stocks if s.change_percent and s.change_percent > 0)
            declining = sum(1 for s in stocks if s.change_percent and s.change_percent < 0)
            unchanged = total_stocks - advancing - declining
            
            # Calculate breadth ratio
            breadth_ratio = (advancing / total_stocks * 100) if total_stocks > 0 else 0
            
            # Determine sector trend
            if breadth_ratio > 60:
                trend = 'bullish'
            elif breadth_ratio < 40:
                trend = 'bearish'
            else:
                trend = 'neutral'
            
            # Calculate average metrics (guard against empty filtered lists
            # which make np.mean return NaN → not JSON-serialisable)
            change_values = [s.change_percent for s in stocks if s.change_percent]
            avg_change = float(np.mean(change_values)) if change_values else 0.0

            volume_ratios = [
                s.volume / s.avg_volume_30d for s in stocks
                if s.volume and s.avg_volume_30d and s.avg_volume_30d > 0
            ]
            avg_volume_ratio = float(np.mean(volume_ratios)) if volume_ratios else 0.0
            
            return {
                'success': True,
                'data': {
                    'sector_id': sector.id,
                    'sector_name': sector.name,
                    'sector_code': sector.code,
                    'current_index': sector.current_index,
                    'change_percent': sector.change_percent,
                    'momentum_1d': sector.momentum_1d,
                    'momentum_5d': sector.momentum_5d,
                    'momentum_10d': sector.momentum_10d,
                    'momentum_20d': sector.momentum_20d,
                    'momentum_30d': sector.momentum_30d,
                    'relative_strength_30d': sector.relative_strength_30d,
                    'total_stocks': total_stocks,
                    'advancing_stocks': advancing,
                    'declining_stocks': declining,
                    'unchanged_stocks': unchanged,
                    'breadth_ratio': float(breadth_ratio),
                    'trend': trend,
                    'avg_change_percent': float(avg_change),
                    'avg_volume_ratio': float(avg_volume_ratio),
                    'total_volume': sector.total_volume,
                    'total_turnover': sector.total_turnover,
                    'rank': sector.rank,
                    'last_updated': sector.last_updated.isoformat() if sector.last_updated else None
                }
            }
            
        except Exception as e:
            logger.error(f"Error analyzing sector {sector_id}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_top_sectors(self, limit: int = 5, metric: str = 'momentum_30d') -> Dict[str, any]:
        """
        Get top performing sectors
        
        Args:
            limit: Number of sectors to return
            metric: Metric to sort by (momentum_30d, change_percent, etc.)
            
        Returns:
            Dictionary with top sectors
        """
        try:
            # Map metric to column
            sort_column = getattr(Sector, metric, Sector.momentum_30d)
            
            sectors = (
                self.db.query(Sector)
                .filter(sort_column != None)
                .order_by(desc(sort_column))
                .limit(limit)
                .all()
            )
            
            top_sectors = []
            for sector in sectors:
                top_sectors.append({
                    'sector_id': sector.id,
                    'sector_name': sector.name,
                    'sector_code': sector.code,
                    'current_index': sector.current_index,
                    'change_percent': sector.change_percent,
                    'momentum_30d': sector.momentum_30d,
                    'relative_strength_30d': sector.relative_strength_30d,
                    'total_stocks': sector.total_stocks,
                    'advancing_stocks': sector.advancing_stocks,
                    'rank': sector.rank
                })
            
            return {
                'success': True,
                'metric': metric,
                'count': len(top_sectors),
                'sectors': top_sectors
            }
            
        except Exception as e:
            logger.error(f"Error getting top sectors: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_bullish_sectors(self, min_momentum: float = 5.0) -> Dict[str, any]:
        """
        Get sectors with bullish momentum
        
        Args:
            min_momentum: Minimum 30-day momentum percentage
            
        Returns:
            Dictionary with bullish sectors
        """
        try:
            sectors = (
                self.db.query(Sector)
                .filter(
                    and_(
                        Sector.momentum_30d != None,
                        Sector.momentum_30d >= min_momentum
                    )
                )
                .order_by(desc(Sector.momentum_30d))
                .all()
            )
            
            bullish_sectors = []
            for sector in sectors:
                # Calculate breadth ratio
                breadth_ratio = 0
                if sector.total_stocks and sector.total_stocks > 0:
                    breadth_ratio = (sector.advancing_stocks / sector.total_stocks * 100) if sector.advancing_stocks else 0
                
                bullish_sectors.append({
                    'sector_id': sector.id,
                    'sector_name': sector.name,
                    'momentum_30d': sector.momentum_30d,
                    'relative_strength_30d': sector.relative_strength_30d,
                    'breadth_ratio': float(breadth_ratio),
                    'total_stocks': sector.total_stocks,
                    'advancing_stocks': sector.advancing_stocks
                })
            
            return {
                'success': True,
                'min_momentum': min_momentum,
                'count': len(bullish_sectors),
                'sectors': bullish_sectors
            }
            
        except Exception as e:
            logger.error(f"Error getting bullish sectors: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def calculate_sector_rotation(self) -> Dict[str, any]:
        """
        Identify sector rotation opportunities
        
        Looks for sectors gaining momentum vs losing momentum
        
        Returns:
            Dictionary with rotation analysis
        """
        try:
            sectors = self.db.query(Sector).all()
            
            if not sectors:
                return {
                    'success': False,
                    'error': 'No sectors found'
                }
            
            # Categorize sectors
            gaining_momentum = []
            losing_momentum = []
            stable = []
            
            for sector in sectors:
                if sector.momentum_30d is None:
                    continue
                
                # Compare short-term vs long-term momentum
                short_term = sector.momentum_5d or 0
                long_term = sector.momentum_30d or 0
                
                momentum_change = short_term - (long_term / 6)  # Normalize
                
                sector_data = {
                    'sector_id': sector.id,
                    'sector_name': sector.name,
                    'momentum_5d': sector.momentum_5d,
                    'momentum_30d': sector.momentum_30d,
                    'momentum_change': float(momentum_change),
                    'relative_strength_30d': sector.relative_strength_30d
                }
                
                if momentum_change > 2:
                    gaining_momentum.append(sector_data)
                elif momentum_change < -2:
                    losing_momentum.append(sector_data)
                else:
                    stable.append(sector_data)
            
            # Sort by momentum change
            gaining_momentum.sort(key=lambda x: x['momentum_change'], reverse=True)
            losing_momentum.sort(key=lambda x: x['momentum_change'])
            
            return {
                'success': True,
                'gaining_momentum': gaining_momentum,
                'losing_momentum': losing_momentum,
                'stable': stable,
                'rotation_opportunities': len(gaining_momentum),
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error calculating sector rotation: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_sector_stocks(self,
                         sector_id: int,
                         sort_by: str = 'change_percent',
                         limit: Optional[int] = None) -> Dict[str, any]:
        """
        Get stocks in a sector with sorting
        
        Args:
            sector_id: Sector ID
            sort_by: Field to sort by (change_percent, volume, beta, etc.)
            limit: Maximum number of stocks to return
            
        Returns:
            Dictionary with sector stocks
        """
        try:
            sector = self.db.query(Sector).filter(Sector.id == sector_id).first()
            
            if not sector:
                return {
                    'success': False,
                    'error': f'Sector not found: {sector_id}'
                }
            
            # Build query
            query = self.db.query(Stock).filter(
                and_(
                    Stock.sector_id == sector_id,
                    Stock.is_active == True,
                    Stock.is_tradeable == True
                )
            )
            
            # Sort
            sort_column = getattr(Stock, sort_by, Stock.change_percent)
            query = query.order_by(desc(sort_column))
            
            # Limit
            if limit:
                query = query.limit(limit)
            
            stocks = query.all()
            
            stock_list = []
            for stock in stocks:
                stock_list.append({
                    'symbol': stock.symbol,
                    'name': stock.name,
                    'ltp': stock.ltp,
                    'change_percent': stock.change_percent,
                    'volume': stock.volume,
                    'beta': stock.beta,
                    'rsi_14': stock.rsi_14,
                    'passes_volume_filter': stock.passes_volume_filter,
                    'passes_beta_filter': stock.passes_beta_filter
                })
            
            return {
                'success': True,
                'sector_id': sector_id,
                'sector_name': sector.name,
                'sort_by': sort_by,
                'count': len(stock_list),
                'stocks': stock_list
            }
            
        except Exception as e:
            logger.error(f"Error getting sector stocks: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


def analyze_sectors(db: Session) -> Dict[str, any]:
    """
    Convenience function to analyze all sectors
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with sector analysis
    """
    analyzer = SectorAnalyzer(db)
    return analyzer.analyze_all_sectors()
