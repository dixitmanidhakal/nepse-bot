"""
Beta Calculator Component

This module calculates beta (systematic risk) for stocks and sectors
relative to the NEPSE index.

Beta measures how much a stock moves relative to the market:
- Beta = 1: Moves with the market
- Beta > 1: More volatile than market (aggressive)
- Beta < 1: Less volatile than market (defensive)
- Beta < 0: Moves opposite to market (rare)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_
import logging

from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.models.sector import Sector

logger = logging.getLogger(__name__)


class BetaCalculator:
    """
    Beta Calculator
    
    Calculates beta and correlation metrics for stocks and sectors
    relative to the market (NEPSE) index.
    """
    
    def __init__(self, db: Session):
        """
        Initialize calculator with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.min_data_points = 30  # Minimum days of data required
    
    def calculate_stock_beta(self,
                            symbol: str,
                            days: int = 90,
                            market_symbol: str = "NEPSE") -> Dict[str, any]:
        """
        Calculate beta for a stock
        
        Formula:
            Beta = Covariance(Stock Returns, Market Returns) / Variance(Market Returns)
        
        Args:
            symbol: Stock symbol
            days: Number of days for calculation (default: 90)
            market_symbol: Market index symbol (default: NEPSE)
            
        Returns:
            Dictionary with beta, correlation, and related metrics
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Get stock returns
            stock_returns = self._get_returns(stock.id, days)
            
            if stock_returns is None or len(stock_returns) < self.min_data_points:
                return {
                    'success': False,
                    'error': f'Insufficient data for {symbol}. Need at least {self.min_data_points} days'
                }
            
            # Get market returns (using NEPSE index or a proxy)
            market_returns = self._get_market_returns(days)
            
            if market_returns is None or len(market_returns) < self.min_data_points:
                return {
                    'success': False,
                    'error': 'Insufficient market data'
                }
            
            # Align the data (same dates)
            stock_returns, market_returns = self._align_returns(stock_returns, market_returns)
            
            if len(stock_returns) < self.min_data_points:
                return {
                    'success': False,
                    'error': 'Insufficient aligned data points'
                }
            
            # Calculate beta
            covariance = np.cov(stock_returns, market_returns)[0, 1]
            market_variance = np.var(market_returns)
            
            if market_variance == 0:
                beta = 0
            else:
                beta = covariance / market_variance
            
            # Calculate correlation
            correlation = np.corrcoef(stock_returns, market_returns)[0, 1]
            
            # Calculate R-squared
            r_squared = correlation ** 2
            
            # Classify beta
            beta_classification = self._classify_beta(beta)
            
            # Calculate volatility metrics
            stock_volatility = np.std(stock_returns) * np.sqrt(252)  # Annualized
            market_volatility = np.std(market_returns) * np.sqrt(252)
            
            # Calculate alpha (excess return)
            stock_mean_return = np.mean(stock_returns)
            market_mean_return = np.mean(market_returns)
            alpha = stock_mean_return - (beta * market_mean_return)
            
            return {
                'success': True,
                'symbol': symbol,
                'beta': float(beta),
                'correlation': float(correlation),
                'r_squared': float(r_squared),
                'alpha': float(alpha),
                'classification': beta_classification,
                'stock_volatility': float(stock_volatility * 100),  # As percentage
                'market_volatility': float(market_volatility * 100),
                'data_points': len(stock_returns),
                'period_days': days,
                'calculated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error calculating beta for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def calculate_sector_beta(self,
                             sector_id: int,
                             days: int = 90) -> Dict[str, any]:
        """
        Calculate average beta for all stocks in a sector
        
        Args:
            sector_id: Sector ID
            days: Number of days for calculation
            
        Returns:
            Dictionary with sector beta metrics
        """
        try:
            # Get sector
            sector = self.db.query(Sector).filter(Sector.id == sector_id).first()
            
            if not sector:
                return {
                    'success': False,
                    'error': f'Sector not found: {sector_id}'
                }
            
            # Get all stocks in sector
            stocks = self.db.query(Stock).filter(
                and_(
                    Stock.sector_id == sector_id,
                    Stock.is_active == True,
                    Stock.is_tradeable == True
                )
            ).all()
            
            if not stocks:
                return {
                    'success': False,
                    'error': f'No stocks found in sector: {sector.name}'
                }
            
            # Calculate beta for each stock
            betas = []
            stock_betas = []
            
            for stock in stocks:
                result = self.calculate_stock_beta(stock.symbol, days)
                if result['success']:
                    betas.append(result['beta'])
                    stock_betas.append({
                        'symbol': stock.symbol,
                        'name': stock.name,
                        'beta': result['beta'],
                        'classification': result['classification']
                    })
            
            if not betas:
                return {
                    'success': False,
                    'error': f'Could not calculate beta for any stock in {sector.name}'
                }
            
            # Calculate sector statistics
            sector_beta = np.mean(betas)
            beta_std = np.std(betas)
            beta_min = np.min(betas)
            beta_max = np.max(betas)
            
            # Count stocks by beta classification
            defensive_count = sum(1 for b in betas if b < 0.8)
            neutral_count = sum(1 for b in betas if 0.8 <= b <= 1.2)
            aggressive_count = sum(1 for b in betas if b > 1.2)
            
            return {
                'success': True,
                'sector_id': sector_id,
                'sector_name': sector.name,
                'sector_beta': float(sector_beta),
                'beta_std': float(beta_std),
                'beta_min': float(beta_min),
                'beta_max': float(beta_max),
                'classification': self._classify_beta(sector_beta),
                'total_stocks': len(stocks),
                'stocks_analyzed': len(betas),
                'defensive_stocks': defensive_count,
                'neutral_stocks': neutral_count,
                'aggressive_stocks': aggressive_count,
                'stock_betas': stock_betas,
                'period_days': days,
                'calculated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error calculating sector beta: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_high_beta_stocks(self,
                            min_beta: float = 1.2,
                            limit: int = 20) -> Dict[str, any]:
        """
        Get stocks with high beta (aggressive stocks)
        
        Args:
            min_beta: Minimum beta threshold (default: 1.2)
            limit: Maximum number of stocks to return
            
        Returns:
            Dictionary with high beta stocks
        """
        try:
            stocks = (
                self.db.query(Stock)
                .filter(
                    and_(
                        Stock.is_active == True,
                        Stock.is_tradeable == True,
                        Stock.beta != None,
                        Stock.beta >= min_beta
                    )
                )
                .order_by(Stock.beta.desc())
                .limit(limit)
                .all()
            )
            
            high_beta_stocks = []
            for stock in stocks:
                high_beta_stocks.append({
                    'symbol': stock.symbol,
                    'name': stock.name,
                    'beta': stock.beta,
                    'sector': stock.sector.name if stock.sector else None,
                    'ltp': stock.ltp,
                    'volatility': stock.volatility
                })
            
            return {
                'success': True,
                'min_beta': min_beta,
                'count': len(high_beta_stocks),
                'stocks': high_beta_stocks
            }
            
        except Exception as e:
            logger.error(f"Error getting high beta stocks: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_low_beta_stocks(self,
                           max_beta: float = 0.8,
                           limit: int = 20) -> Dict[str, any]:
        """
        Get stocks with low beta (defensive stocks)
        
        Args:
            max_beta: Maximum beta threshold (default: 0.8)
            limit: Maximum number of stocks to return
            
        Returns:
            Dictionary with low beta stocks
        """
        try:
            stocks = (
                self.db.query(Stock)
                .filter(
                    and_(
                        Stock.is_active == True,
                        Stock.is_tradeable == True,
                        Stock.beta != None,
                        Stock.beta <= max_beta,
                        Stock.beta >= 0  # Exclude negative betas
                    )
                )
                .order_by(Stock.beta.asc())
                .limit(limit)
                .all()
            )
            
            low_beta_stocks = []
            for stock in stocks:
                low_beta_stocks.append({
                    'symbol': stock.symbol,
                    'name': stock.name,
                    'beta': stock.beta,
                    'sector': stock.sector.name if stock.sector else None,
                    'ltp': stock.ltp,
                    'volatility': stock.volatility
                })
            
            return {
                'success': True,
                'max_beta': max_beta,
                'count': len(low_beta_stocks),
                'stocks': low_beta_stocks
            }
            
        except Exception as e:
            logger.error(f"Error getting low beta stocks: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_returns(self, stock_id: int, days: int) -> Optional[pd.Series]:
        """
        Get daily returns for a stock
        
        Args:
            stock_id: Stock ID
            days: Number of days
            
        Returns:
            Pandas Series with returns or None
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)  # Extra buffer
            
            ohlcv_data = (
                self.db.query(StockOHLCV)
                .filter(
                    and_(
                        StockOHLCV.stock_id == stock_id,
                        StockOHLCV.date >= start_date,
                        StockOHLCV.date <= end_date
                    )
                )
                .order_by(StockOHLCV.date.asc())
                .all()
            )
            
            if len(ohlcv_data) < self.min_data_points:
                return None
            
            # Extract close prices
            closes = [record.close for record in ohlcv_data]
            dates = [record.date for record in ohlcv_data]
            
            # Calculate returns
            prices = pd.Series(closes, index=dates)
            returns = prices.pct_change().dropna()
            
            return returns
            
        except Exception as e:
            logger.error(f"Error getting returns for stock {stock_id}: {str(e)}")
            return None
    
    def _get_market_returns(self, days: int) -> Optional[pd.Series]:
        """
        Get market (NEPSE) returns
        
        For now, we'll use an average of all stocks as a proxy for NEPSE
        In production, this should use actual NEPSE index data
        
        Args:
            days: Number of days
            
        Returns:
            Pandas Series with market returns or None
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)
            
            # Get all active stocks
            stocks = (
                self.db.query(Stock)
                .filter(
                    and_(
                        Stock.is_active == True,
                        Stock.is_tradeable == True
                    )
                )
                .limit(50)  # Use top 50 stocks as market proxy
                .all()
            )
            
            if not stocks:
                return None
            
            # Get returns for each stock and average them
            all_returns = []
            
            for stock in stocks:
                returns = self._get_returns(stock.id, days)
                if returns is not None and len(returns) >= self.min_data_points:
                    all_returns.append(returns)
            
            if not all_returns:
                return None
            
            # Create DataFrame and calculate average
            returns_df = pd.DataFrame(all_returns).T
            market_returns = returns_df.mean(axis=1)
            
            return market_returns
            
        except Exception as e:
            logger.error(f"Error getting market returns: {str(e)}")
            return None
    
    def _align_returns(self,
                      stock_returns: pd.Series,
                      market_returns: pd.Series) -> Tuple[np.ndarray, np.ndarray]:
        """
        Align stock and market returns to same dates
        
        Args:
            stock_returns: Stock returns series
            market_returns: Market returns series
            
        Returns:
            Tuple of aligned numpy arrays
        """
        # Find common dates
        common_dates = stock_returns.index.intersection(market_returns.index)
        
        # Filter to common dates
        aligned_stock = stock_returns.loc[common_dates].values
        aligned_market = market_returns.loc[common_dates].values
        
        return aligned_stock, aligned_market
    
    def _classify_beta(self, beta: float) -> str:
        """
        Classify beta value
        
        Args:
            beta: Beta value
            
        Returns:
            Classification string
        """
        if beta < 0:
            return 'negative'  # Rare, moves opposite to market
        elif beta < 0.5:
            return 'very_defensive'  # Very low volatility
        elif beta < 0.8:
            return 'defensive'  # Low volatility
        elif beta <= 1.2:
            return 'neutral'  # Similar to market
        elif beta <= 1.5:
            return 'aggressive'  # High volatility
        else:
            return 'very_aggressive'  # Very high volatility


def calculate_beta(db: Session, symbol: str, days: int = 90) -> Dict[str, any]:
    """
    Convenience function to calculate stock beta
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days for calculation
        
    Returns:
        Dictionary with beta metrics
    """
    calculator = BetaCalculator(db)
    return calculator.calculate_stock_beta(symbol, days)
