"""
Stock Screener Component

This module provides multi-criteria stock screening functionality.
Filters stocks based on volume, beta, sector, technical, and fundamental criteria.
"""

import numpy as np
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
import logging

from app.models.stock import Stock
from app.models.sector import Sector

logger = logging.getLogger(__name__)


class StockScreener:
    """
    Stock Screener
    
    Screens stocks based on multiple criteria including:
    - Volume (liquidity)
    - Beta (volatility)
    - Sector (bullish sectors)
    - Technical indicators
    - Fundamental metrics
    """
    
    def __init__(self, db: Session):
        """
        Initialize screener with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def screen_stocks(self, criteria: Dict[str, any]) -> Dict[str, any]:
        """
        Screen stocks based on provided criteria
        
        Args:
            criteria: Dictionary with screening criteria
            
        Example criteria:
            {
                'min_volume_ratio': 1.5,
                'max_beta': 1.5,
                'min_beta': 0.5,
                'bullish_sector_only': True,
                'min_rsi': 30,
                'max_rsi': 70,
                'price_above_sma20': True,
                'min_pe_ratio': 0,
                'max_pe_ratio': 30,
                'min_roe': 10,
                'limit': 50
            }
            
        Returns:
            Dictionary with screened stocks
        """
        try:
            # Start with base query
            query = self.db.query(Stock).filter(
                and_(
                    Stock.is_active == True,
                    Stock.is_tradeable == True
                )
            )
            
            # Apply filters
            filters_applied = []
            
            # Volume filter
            if 'min_volume_ratio' in criteria:
                min_ratio = criteria['min_volume_ratio']
                query = query.filter(
                    and_(
                        Stock.volume != None,
                        Stock.avg_volume_30d != None,
                        Stock.avg_volume_30d > 0,
                        (Stock.volume / Stock.avg_volume_30d) >= min_ratio
                    )
                )
                filters_applied.append(f'volume_ratio >= {min_ratio}')
            
            # Beta filter
            if 'min_beta' in criteria:
                query = query.filter(
                    and_(
                        Stock.beta != None,
                        Stock.beta >= criteria['min_beta']
                    )
                )
                filters_applied.append(f'beta >= {criteria["min_beta"]}')
            
            if 'max_beta' in criteria:
                query = query.filter(
                    and_(
                        Stock.beta != None,
                        Stock.beta <= criteria['max_beta']
                    )
                )
                filters_applied.append(f'beta <= {criteria["max_beta"]}')
            
            # Sector filter
            if criteria.get('bullish_sector_only', False):
                query = query.filter(Stock.in_bullish_sector == True)
                filters_applied.append('bullish_sector_only')
            
            if 'sector_ids' in criteria and criteria['sector_ids']:
                query = query.filter(Stock.sector_id.in_(criteria['sector_ids']))
                filters_applied.append(f'sectors: {criteria["sector_ids"]}')
            
            # RSI filter
            if 'min_rsi' in criteria:
                query = query.filter(
                    and_(
                        Stock.rsi_14 != None,
                        Stock.rsi_14 >= criteria['min_rsi']
                    )
                )
                filters_applied.append(f'rsi >= {criteria["min_rsi"]}')
            
            if 'max_rsi' in criteria:
                query = query.filter(
                    and_(
                        Stock.rsi_14 != None,
                        Stock.rsi_14 <= criteria['max_rsi']
                    )
                )
                filters_applied.append(f'rsi <= {criteria["max_rsi"]}')
            
            # Moving average filter
            if criteria.get('price_above_sma20', False):
                query = query.filter(
                    and_(
                        Stock.ltp != None,
                        Stock.sma_20 != None,
                        Stock.ltp > Stock.sma_20
                    )
                )
                filters_applied.append('price > SMA20')
            
            if criteria.get('price_above_sma50', False):
                query = query.filter(
                    and_(
                        Stock.ltp != None,
                        Stock.sma_50 != None,
                        Stock.ltp > Stock.sma_50
                    )
                )
                filters_applied.append('price > SMA50')
            
            # MACD filter
            if criteria.get('macd_bullish', False):
                query = query.filter(
                    and_(
                        Stock.macd != None,
                        Stock.macd_signal != None,
                        Stock.macd > Stock.macd_signal
                    )
                )
                filters_applied.append('MACD bullish')
            
            # P/E ratio filter
            if 'min_pe_ratio' in criteria:
                query = query.filter(
                    and_(
                        Stock.pe_ratio != None,
                        Stock.pe_ratio >= criteria['min_pe_ratio']
                    )
                )
                filters_applied.append(f'P/E >= {criteria["min_pe_ratio"]}')
            
            if 'max_pe_ratio' in criteria:
                query = query.filter(
                    and_(
                        Stock.pe_ratio != None,
                        Stock.pe_ratio <= criteria['max_pe_ratio']
                    )
                )
                filters_applied.append(f'P/E <= {criteria["max_pe_ratio"]}')
            
            # ROE filter
            if 'min_roe' in criteria:
                query = query.filter(
                    and_(
                        Stock.roe != None,
                        Stock.roe >= criteria['min_roe']
                    )
                )
                filters_applied.append(f'ROE >= {criteria["min_roe"]}')
            
            # Dividend yield filter
            if 'min_dividend_yield' in criteria:
                query = query.filter(
                    and_(
                        Stock.dividend_yield != None,
                        Stock.dividend_yield >= criteria['min_dividend_yield']
                    )
                )
                filters_applied.append(f'dividend_yield >= {criteria["min_dividend_yield"]}')
            
            # Price range filter
            if 'min_price' in criteria:
                query = query.filter(
                    and_(
                        Stock.ltp != None,
                        Stock.ltp >= criteria['min_price']
                    )
                )
                filters_applied.append(f'price >= {criteria["min_price"]}')
            
            if 'max_price' in criteria:
                query = query.filter(
                    and_(
                        Stock.ltp != None,
                        Stock.ltp <= criteria['max_price']
                    )
                )
                filters_applied.append(f'price <= {criteria["max_price"]}')
            
            # Limit results
            limit = criteria.get('limit', 50)
            
            # Execute query
            stocks = query.limit(limit).all()
            
            # Calculate scores for each stock
            scored_stocks = []
            for stock in stocks:
                score = self._calculate_stock_score(stock, criteria)
                scored_stocks.append({
                    'symbol': stock.symbol,
                    'name': stock.name,
                    'sector': stock.sector.name if stock.sector else None,
                    'ltp': stock.ltp,
                    'change_percent': stock.change_percent,
                    'volume': stock.volume,
                    'volume_ratio': (stock.volume / stock.avg_volume_30d) if stock.volume and stock.avg_volume_30d and stock.avg_volume_30d > 0 else None,
                    'beta': stock.beta,
                    'rsi_14': stock.rsi_14,
                    'macd': stock.macd,
                    'macd_signal': stock.macd_signal,
                    'sma_20': stock.sma_20,
                    'sma_50': stock.sma_50,
                    'pe_ratio': stock.pe_ratio,
                    'roe': stock.roe,
                    'dividend_yield': stock.dividend_yield,
                    'score': score,
                    'passes_volume_filter': stock.passes_volume_filter,
                    'passes_beta_filter': stock.passes_beta_filter,
                    'in_bullish_sector': stock.in_bullish_sector
                })
            
            # Sort by score
            scored_stocks.sort(key=lambda x: x['score'], reverse=True)
            
            return {
                'success': True,
                'filters_applied': filters_applied,
                'total_results': len(scored_stocks),
                'stocks': scored_stocks,
                'screened_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error screening stocks: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_high_volume_stocks(self,
                              min_volume_ratio: float = 2.0,
                              limit: int = 20) -> Dict[str, any]:
        """
        Get stocks with high volume (liquidity hunters)
        
        Args:
            min_volume_ratio: Minimum volume / avg volume ratio
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with high volume stocks
        """
        criteria = {
            'min_volume_ratio': min_volume_ratio,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def get_momentum_stocks(self, limit: int = 20) -> Dict[str, any]:
        """
        Get stocks with strong momentum
        
        Criteria:
        - Price above SMA20 and SMA50
        - RSI between 50-70 (strong but not overbought)
        - MACD bullish
        - Above average volume
        
        Args:
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with momentum stocks
        """
        criteria = {
            'price_above_sma20': True,
            'price_above_sma50': True,
            'min_rsi': 50,
            'max_rsi': 70,
            'macd_bullish': True,
            'min_volume_ratio': 1.2,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def get_value_stocks(self, limit: int = 20) -> Dict[str, any]:
        """
        Get value stocks (fundamental screening)
        
        Criteria:
        - Low P/E ratio (< 20)
        - High ROE (> 15%)
        - Dividend yield > 2%
        
        Args:
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with value stocks
        """
        criteria = {
            'max_pe_ratio': 20,
            'min_roe': 15,
            'min_dividend_yield': 2,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def get_defensive_stocks(self, limit: int = 20) -> Dict[str, any]:
        """
        Get defensive stocks (low beta)
        
        Criteria:
        - Beta < 0.8
        - Stable dividend yield
        
        Args:
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with defensive stocks
        """
        criteria = {
            'max_beta': 0.8,
            'min_dividend_yield': 1,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def get_growth_stocks(self, limit: int = 20) -> Dict[str, any]:
        """
        Get growth stocks
        
        Criteria:
        - High ROE (> 20%)
        - In bullish sector
        - Strong momentum
        
        Args:
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with growth stocks
        """
        criteria = {
            'min_roe': 20,
            'bullish_sector_only': True,
            'price_above_sma20': True,
            'min_rsi': 45,
            'max_rsi': 75,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def get_oversold_stocks(self, limit: int = 20) -> Dict[str, any]:
        """
        Get potentially oversold stocks
        
        Criteria:
        - RSI < 30 (oversold)
        - In bullish sector (sector support)
        - Above average volume (interest)
        
        Args:
            limit: Maximum number of stocks
            
        Returns:
            Dictionary with oversold stocks
        """
        criteria = {
            'max_rsi': 30,
            'bullish_sector_only': True,
            'min_volume_ratio': 1.5,
            'limit': limit
        }
        return self.screen_stocks(criteria)
    
    def _calculate_stock_score(self, stock: Stock, criteria: Dict[str, any]) -> float:
        """
        Calculate a score for a stock based on how well it meets criteria
        
        Args:
            stock: Stock object
            criteria: Screening criteria
            
        Returns:
            Score (0-100)
        """
        score = 0
        max_score = 0
        
        # Volume score (0-20 points)
        max_score += 20
        if stock.volume and stock.avg_volume_30d and stock.avg_volume_30d > 0:
            volume_ratio = stock.volume / stock.avg_volume_30d
            if volume_ratio >= 2.0:
                score += 20
            elif volume_ratio >= 1.5:
                score += 15
            elif volume_ratio >= 1.0:
                score += 10
        
        # Beta score (0-15 points)
        max_score += 15
        if stock.beta is not None:
            if 0.8 <= stock.beta <= 1.2:
                score += 15  # Neutral beta is good
            elif 0.5 <= stock.beta < 0.8 or 1.2 < stock.beta <= 1.5:
                score += 10
            elif stock.beta >= 0:
                score += 5
        
        # RSI score (0-15 points)
        max_score += 15
        if stock.rsi_14 is not None:
            if 40 <= stock.rsi_14 <= 60:
                score += 15  # Neutral RSI
            elif 30 <= stock.rsi_14 < 40 or 60 < stock.rsi_14 <= 70:
                score += 10
            elif stock.rsi_14 < 30:
                score += 5  # Oversold (potential bounce)
        
        # Moving average score (0-15 points)
        max_score += 15
        if stock.ltp and stock.sma_20 and stock.ltp > stock.sma_20:
            score += 8
        if stock.ltp and stock.sma_50 and stock.ltp > stock.sma_50:
            score += 7
        
        # MACD score (0-10 points)
        max_score += 10
        if stock.macd and stock.macd_signal:
            if stock.macd > stock.macd_signal:
                score += 10
            elif stock.macd > stock.macd_signal * 0.95:
                score += 5
        
        # Sector score (0-10 points)
        max_score += 10
        if stock.in_bullish_sector:
            score += 10
        
        # Fundamental score (0-15 points)
        max_score += 15
        if stock.pe_ratio and 0 < stock.pe_ratio < 25:
            score += 5
        if stock.roe and stock.roe > 15:
            score += 5
        if stock.dividend_yield and stock.dividend_yield > 2:
            score += 5
        
        # Normalize to 0-100
        if max_score > 0:
            normalized_score = (score / max_score) * 100
        else:
            normalized_score = 0
        
        return round(normalized_score, 2)


def screen_stocks(db: Session, criteria: Dict[str, any]) -> Dict[str, any]:
    """
    Convenience function to screen stocks
    
    Args:
        db: Database session
        criteria: Screening criteria
        
    Returns:
        Dictionary with screened stocks
    """
    screener = StockScreener(db)
    return screener.screen_stocks(criteria)
