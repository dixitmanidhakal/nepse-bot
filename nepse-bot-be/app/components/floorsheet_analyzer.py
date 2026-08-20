"""
Floorsheet Analyzer Module

This module analyzes trade data (floorsheet) to identify:
- Institutional trades
- Broker accumulation/distribution
- Cross trades (manipulation)
- Trade clustering
- Broker sentiment
- Top active brokers

Used for detecting institutional activity and market manipulation.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, func
from collections import defaultdict
import logging

from app.models.stock import Stock
from app.models.floorsheet import Floorsheet

logger = logging.getLogger(__name__)


class FloorsheetAnalyzer:
    """
    Floorsheet Analyzer
    
    Analyzes trade data to detect institutional activity,
    broker behavior, and potential manipulation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize analyzer with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_recent_trades(self, 
                         symbol: str, 
                         days: int = 1,
                         limit: int = 100) -> Dict[str, any]:
        """
        Get recent trades for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days of history
            limit: Maximum number of trades to return
            
        Returns:
            Dictionary with recent trades
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                logger.error(f"Stock not found: {symbol}")
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch trades
            trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .order_by(desc(Floorsheet.trade_date), desc(Floorsheet.trade_time))
                .limit(limit)
                .all()
            )
            
            if not trades:
                return {
                    'success': False,
                    'error': f'No trade data found for {symbol}'
                }
            
            # Convert to list
            trade_list = [trade.to_dict() for trade in trades]
            
            # Calculate summary
            total_quantity = sum(t['quantity'] for t in trade_list)
            total_amount = sum(t['amount'] for t in trade_list)
            avg_rate = total_amount / total_quantity if total_quantity > 0 else 0
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'total_trades': len(trade_list),
                'trades': trade_list,
                'summary': {
                    'total_quantity': total_quantity,
                    'total_amount': total_amount,
                    'average_rate': avg_rate,
                    'min_rate': min(t['rate'] for t in trade_list),
                    'max_rate': max(t['rate'] for t in trade_list)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting recent trades for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def detect_institutional_trades(self,
                                   symbol: str,
                                   days: int = 7,
                                   quantity_threshold: float = 1000) -> Dict[str, any]:
        """
        Detect institutional trades (large trades)
        
        Institutional trades are typically large in size and can
        indicate smart money activity.
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            quantity_threshold: Minimum quantity to consider institutional
            
        Returns:
            Dictionary with institutional trades
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch institutional trades
            trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date,
                        Floorsheet.quantity >= quantity_threshold
                    )
                )
                .order_by(desc(Floorsheet.quantity))
                .all()
            )
            
            if not trades:
                return {
                    'success': True,
                    'symbol': symbol,
                    'period_days': days,
                    'institutional_trades': [],
                    'total_trades': 0,
                    'message': 'No institutional trades detected'
                }
            
            # Analyze trades
            trade_list = []
            total_buy_qty = 0
            total_sell_qty = 0
            
            for trade in trades:
                trade_dict = trade.to_dict()
                trade_list.append(trade_dict)
                
                # Classify as buy or sell based on broker activity
                # (This is simplified - in reality, need more context)
                total_buy_qty += trade.quantity / 2
                total_sell_qty += trade.quantity / 2
            
            # Calculate net position
            net_position = total_buy_qty - total_sell_qty
            sentiment = 'bullish' if net_position > 0 else 'bearish' if net_position < 0 else 'neutral'
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'quantity_threshold': quantity_threshold,
                'institutional_trades': trade_list,
                'total_trades': len(trade_list),
                'total_quantity': sum(t['quantity'] for t in trade_list),
                'total_amount': sum(t['amount'] for t in trade_list),
                'sentiment': sentiment,
                'interpretation': self._interpret_institutional_activity(sentiment, len(trade_list))
            }
            
        except Exception as e:
            logger.error(f"Error detecting institutional trades for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def detect_cross_trades(self, symbol: str, days: int = 7) -> Dict[str, any]:
        """
        Detect cross trades (same broker buy/sell)
        
        Cross trades can indicate manipulation or internal transfers.
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with cross trades
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch cross trades
            trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date,
                        Floorsheet.is_cross_trade == True
                    )
                )
                .order_by(desc(Floorsheet.trade_date))
                .all()
            )
            
            if not trades:
                return {
                    'success': True,
                    'symbol': symbol,
                    'period_days': days,
                    'cross_trades': [],
                    'total_trades': 0,
                    'message': 'No cross trades detected'
                }
            
            # Analyze cross trades
            trade_list = [trade.to_dict() for trade in trades]
            
            # Group by broker
            broker_cross_trades = defaultdict(list)
            for trade in trade_list:
                broker_id = trade['buyer_broker_id']
                broker_cross_trades[broker_id].append(trade)
            
            # Calculate statistics
            total_quantity = sum(t['quantity'] for t in trade_list)
            total_amount = sum(t['amount'] for t in trade_list)
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'cross_trades': trade_list,
                'total_trades': len(trade_list),
                'total_quantity': total_quantity,
                'total_amount': total_amount,
                'brokers_involved': len(broker_cross_trades),
                'broker_breakdown': {
                    broker: {
                        'trades': len(trades),
                        'quantity': sum(t['quantity'] for t in trades),
                        'amount': sum(t['amount'] for t in trades)
                    }
                    for broker, trades in broker_cross_trades.items()
                },
                'interpretation': self._interpret_cross_trades(len(trade_list), total_quantity)
            }
            
        except Exception as e:
            logger.error(f"Error detecting cross trades for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_broker_activity(self,
                               symbol: str,
                               broker_id: str,
                               days: int = 30) -> Dict[str, any]:
        """
        Analyze specific broker's activity
        
        Args:
            symbol: Stock symbol
            broker_id: Broker ID
            days: Number of days to analyze
            
        Returns:
            Dictionary with broker activity analysis
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch broker's buy trades
            buy_trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date,
                        Floorsheet.buyer_broker_id == broker_id
                    )
                )
                .all()
            )
            
            # Fetch broker's sell trades
            sell_trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date,
                        Floorsheet.seller_broker_id == broker_id
                    )
                )
                .all()
            )
            
            # Calculate statistics
            total_buy_qty = sum(t.quantity for t in buy_trades)
            total_sell_qty = sum(t.quantity for t in sell_trades)
            total_buy_amount = sum(t.amount for t in buy_trades)
            total_sell_amount = sum(t.amount for t in sell_trades)
            
            avg_buy_rate = total_buy_amount / total_buy_qty if total_buy_qty > 0 else 0
            avg_sell_rate = total_sell_amount / total_sell_qty if total_sell_qty > 0 else 0
            
            net_quantity = total_buy_qty - total_sell_qty
            net_amount = total_buy_amount - total_sell_amount
            
            # Determine position
            if net_quantity > 0:
                position = 'accumulating'
                sentiment = 'bullish'
            elif net_quantity < 0:
                position = 'distributing'
                sentiment = 'bearish'
            else:
                position = 'neutral'
                sentiment = 'neutral'
            
            return {
                'success': True,
                'symbol': symbol,
                'broker_id': broker_id,
                'period_days': days,
                'buy_trades': {
                    'count': len(buy_trades),
                    'quantity': total_buy_qty,
                    'amount': total_buy_amount,
                    'avg_rate': avg_buy_rate
                },
                'sell_trades': {
                    'count': len(sell_trades),
                    'quantity': total_sell_qty,
                    'amount': total_sell_amount,
                    'avg_rate': avg_sell_rate
                },
                'net_position': {
                    'quantity': net_quantity,
                    'amount': net_amount,
                    'position': position,
                    'sentiment': sentiment
                },
                'interpretation': self._interpret_broker_activity(position, net_quantity)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing broker activity for {symbol}, broker {broker_id}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_top_brokers(self,
                       symbol: str,
                       days: int = 7,
                       limit: int = 10) -> Dict[str, any]:
        """
        Get top active brokers
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            limit: Number of top brokers to return
            
        Returns:
            Dictionary with top brokers
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch all trades
            trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .all()
            )
            
            if not trades:
                return {
                    'success': False,
                    'error': f'No trade data found for {symbol}'
                }
            
            # Aggregate broker activity
            broker_stats = defaultdict(lambda: {
                'buy_quantity': 0,
                'sell_quantity': 0,
                'buy_amount': 0,
                'sell_amount': 0,
                'buy_trades': 0,
                'sell_trades': 0
            })
            
            for trade in trades:
                # Buyer
                buyer_id = trade.buyer_broker_id
                broker_stats[buyer_id]['buy_quantity'] += trade.quantity
                broker_stats[buyer_id]['buy_amount'] += trade.amount
                broker_stats[buyer_id]['buy_trades'] += 1
                
                # Seller
                seller_id = trade.seller_broker_id
                broker_stats[seller_id]['sell_quantity'] += trade.quantity
                broker_stats[seller_id]['sell_amount'] += trade.amount
                broker_stats[seller_id]['sell_trades'] += 1
            
            # Calculate net positions and sort
            broker_list = []
            for broker_id, stats in broker_stats.items():
                net_qty = stats['buy_quantity'] - stats['sell_quantity']
                net_amount = stats['buy_amount'] - stats['sell_amount']
                total_volume = stats['buy_quantity'] + stats['sell_quantity']
                
                broker_list.append({
                    'broker_id': broker_id,
                    'buy_quantity': stats['buy_quantity'],
                    'sell_quantity': stats['sell_quantity'],
                    'net_quantity': net_qty,
                    'total_volume': total_volume,
                    'buy_amount': stats['buy_amount'],
                    'sell_amount': stats['sell_amount'],
                    'net_amount': net_amount,
                    'buy_trades': stats['buy_trades'],
                    'sell_trades': stats['sell_trades'],
                    'position': 'accumulating' if net_qty > 0 else 'distributing' if net_qty < 0 else 'neutral'
                })
            
            # Sort by total volume
            broker_list.sort(key=lambda x: x['total_volume'], reverse=True)
            
            # Get top brokers
            top_brokers = broker_list[:limit]
            
            # Separate top buyers and sellers
            top_buyers = sorted(broker_list, key=lambda x: x['buy_quantity'], reverse=True)[:5]
            top_sellers = sorted(broker_list, key=lambda x: x['sell_quantity'], reverse=True)[:5]
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'top_brokers_by_volume': top_brokers,
                'top_buyers': top_buyers,
                'top_sellers': top_sellers,
                'total_brokers': len(broker_list)
            }
            
        except Exception as e:
            logger.error(f"Error getting top brokers for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_accumulation_distribution(self,
                                         symbol: str,
                                         days: int = 30) -> Dict[str, any]:
        """
        Analyze accumulation/distribution patterns
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with accumulation/distribution analysis
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Fetch trades grouped by date
            trades_by_date = (
                self.db.query(
                    Floorsheet.trade_date,
                    func.sum(Floorsheet.quantity).label('total_quantity'),
                    func.sum(Floorsheet.amount).label('total_amount'),
                    func.count(Floorsheet.id).label('trade_count'),
                    func.avg(Floorsheet.rate).label('avg_rate')
                )
                .filter(
                    and_(
                        Floorsheet.stock_id == stock.id,
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .group_by(Floorsheet.trade_date)
                .order_by(Floorsheet.trade_date.asc())
                .all()
            )
            
            if not trades_by_date:
                return {
                    'success': False,
                    'error': f'No trade data found for {symbol}'
                }
            
            # Analyze daily patterns
            daily_data = []
            for row in trades_by_date:
                daily_data.append({
                    'date': row.trade_date.isoformat(),
                    'quantity': float(row.total_quantity),
                    'amount': float(row.total_amount),
                    'trades': row.trade_count,
                    'avg_rate': float(row.avg_rate)
                })
            
            # Calculate trends
            quantities = [d['quantity'] for d in daily_data]
            avg_rates = [d['avg_rate'] for d in daily_data]
            
            # Simple trend analysis
            recent_qty = np.mean(quantities[-7:]) if len(quantities) >= 7 else np.mean(quantities)
            earlier_qty = np.mean(quantities[:7]) if len(quantities) >= 14 else np.mean(quantities)
            
            recent_rate = np.mean(avg_rates[-7:]) if len(avg_rates) >= 7 else np.mean(avg_rates)
            earlier_rate = np.mean(avg_rates[:7]) if len(avg_rates) >= 14 else np.mean(avg_rates)
            
            # Determine phase
            if recent_qty > earlier_qty and recent_rate > earlier_rate:
                phase = 'strong_accumulation'
            elif recent_qty > earlier_qty:
                phase = 'accumulation'
            elif recent_qty < earlier_qty and recent_rate < earlier_rate:
                phase = 'strong_distribution'
            elif recent_qty < earlier_qty:
                phase = 'distribution'
            else:
                phase = 'consolidation'
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'phase': phase,
                'daily_data': daily_data,
                'statistics': {
                    'avg_daily_quantity': float(np.mean(quantities)),
                    'avg_daily_amount': float(np.mean([d['amount'] for d in daily_data])),
                    'recent_avg_quantity': float(recent_qty),
                    'earlier_avg_quantity': float(earlier_qty),
                    'quantity_trend': 'increasing' if recent_qty > earlier_qty else 'decreasing',
                    'price_trend': 'increasing' if recent_rate > earlier_rate else 'decreasing'
                },
                'interpretation': self._interpret_accumulation_distribution(phase)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing accumulation/distribution for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _interpret_institutional_activity(self, sentiment: str, trade_count: int) -> str:
        """Generate interpretation for institutional activity"""
        if sentiment == 'bullish':
            return f"Institutional buying detected ({trade_count} large trades). Smart money accumulating."
        elif sentiment == 'bearish':
            return f"Institutional selling detected ({trade_count} large trades). Smart money distributing."
        else:
            return f"Balanced institutional activity ({trade_count} large trades). No clear direction."
    
    def _interpret_cross_trades(self, count: int, quantity: float) -> str:
        """Generate interpretation for cross trades"""
        if count > 10:
            return f"High number of cross trades ({count}). Potential manipulation or internal transfers."
        elif count > 5:
            return f"Moderate cross trade activity ({count}). Monitor for manipulation."
        else:
            return f"Low cross trade activity ({count}). Normal trading pattern."
    
    def _interpret_broker_activity(self, position: str, net_quantity: float) -> str:
        """Generate interpretation for broker activity"""
        if position == 'accumulating':
            return f"Broker is accumulating (net: +{net_quantity:.0f} shares). Bullish signal."
        elif position == 'distributing':
            return f"Broker is distributing (net: {net_quantity:.0f} shares). Bearish signal."
        else:
            return "Broker has neutral position. No clear directional bias."
    
    def _interpret_accumulation_distribution(self, phase: str) -> str:
        """Generate interpretation for accumulation/distribution"""
        if phase == 'strong_accumulation':
            return "Strong accumulation phase. Increasing volume with rising prices. Very bullish."
        elif phase == 'accumulation':
            return "Accumulation phase. Increasing volume. Bullish signal."
        elif phase == 'strong_distribution':
            return "Strong distribution phase. Decreasing volume with falling prices. Very bearish."
        elif phase == 'distribution':
            return "Distribution phase. Decreasing volume. Bearish signal."
        else:
            return "Consolidation phase. Stable volume and prices. Neutral."


# Convenience functions
def analyze_floorsheet(db: Session, symbol: str, days: int = 7) -> Dict[str, any]:
    """
    Quick floorsheet analysis
    
    Args:
        db: Database session
        symbol: Stock symbol
        days: Number of days to analyze
        
    Returns:
        Comprehensive floorsheet analysis
    """
    analyzer = FloorsheetAnalyzer(db)
    
    return {
        'recent_trades': analyzer.get_recent_trades(symbol, days),
        'institutional_trades': analyzer.detect_institutional_trades(symbol, days),
        'cross_trades': analyzer.detect_cross_trades(symbol, days),
        'top_brokers': analyzer.get_top_brokers(symbol, days),
        'accumulation_distribution': analyzer.analyze_accumulation_distribution(symbol, days)
    }
