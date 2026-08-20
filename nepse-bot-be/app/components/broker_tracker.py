"""
Broker Tracker Module

This module tracks individual broker activity and identifies:
- Broker accumulation/distribution patterns
- Institutional brokers
- Broker sentiment
- Broker rankings
- Broker pair analysis

Used for following smart money and institutional activity.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
from collections import defaultdict
import logging

from app.models.stock import Stock
from app.models.floorsheet import Floorsheet

logger = logging.getLogger(__name__)


class BrokerTracker:
    """
    Broker Tracker
    
    Tracks and analyzes individual broker activity to identify
    institutional players and smart money movements.
    """
    
    def __init__(self, db: Session):
        """
        Initialize tracker with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def track_broker(self,
                    broker_id: str,
                    symbol: Optional[str] = None,
                    days: int = 30) -> Dict[str, any]:
        """
        Track specific broker's activity
        
        Args:
            broker_id: Broker ID to track
            symbol: Optional stock symbol (if None, tracks all stocks)
            days: Number of days to analyze
            
        Returns:
            Dictionary with broker activity
        """
        try:
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Build query
            query = self.db.query(Floorsheet).filter(
                and_(
                    Floorsheet.trade_date >= start_date,
                    Floorsheet.trade_date <= end_date,
                    or_(
                        Floorsheet.buyer_broker_id == broker_id,
                        Floorsheet.seller_broker_id == broker_id
                    )
                )
            )
            
            # Add stock filter if provided
            if symbol:
                stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
                if stock:
                    query = query.filter(Floorsheet.stock_id == stock.id)
            
            trades = query.all()
            
            if not trades:
                return {
                    'success': False,
                    'error': f'No trades found for broker {broker_id}'
                }
            
            # Analyze trades
            buy_trades = [t for t in trades if t.buyer_broker_id == broker_id]
            sell_trades = [t for t in trades if t.seller_broker_id == broker_id]
            
            # Calculate statistics
            total_buy_qty = sum(t.quantity for t in buy_trades)
            total_sell_qty = sum(t.quantity for t in sell_trades)
            total_buy_amount = sum(t.amount for t in buy_trades)
            total_sell_amount = sum(t.amount for t in sell_trades)
            
            net_quantity = total_buy_qty - total_sell_qty
            net_amount = total_buy_amount - total_sell_amount
            
            # Determine position
            if net_quantity > 0:
                position = 'long'
                sentiment = 'bullish'
            elif net_quantity < 0:
                position = 'short'
                sentiment = 'bearish'
            else:
                position = 'neutral'
                sentiment = 'neutral'
            
            # Get stock-wise breakdown
            stock_breakdown = defaultdict(lambda: {
                'buy_qty': 0,
                'sell_qty': 0,
                'buy_amount': 0,
                'sell_amount': 0
            })
            
            for trade in trades:
                stock_symbol = trade.stock.symbol if trade.stock else 'Unknown'
                if trade.buyer_broker_id == broker_id:
                    stock_breakdown[stock_symbol]['buy_qty'] += trade.quantity
                    stock_breakdown[stock_symbol]['buy_amount'] += trade.amount
                if trade.seller_broker_id == broker_id:
                    stock_breakdown[stock_symbol]['sell_qty'] += trade.quantity
                    stock_breakdown[stock_symbol]['sell_amount'] += trade.amount
            
            return {
                'success': True,
                'broker_id': broker_id,
                'period_days': days,
                'symbol': symbol,
                'buy_activity': {
                    'trades': len(buy_trades),
                    'quantity': total_buy_qty,
                    'amount': total_buy_amount,
                    'avg_rate': total_buy_amount / total_buy_qty if total_buy_qty > 0 else 0
                },
                'sell_activity': {
                    'trades': len(sell_trades),
                    'quantity': total_sell_qty,
                    'amount': total_sell_amount,
                    'avg_rate': total_sell_amount / total_sell_qty if total_sell_qty > 0 else 0
                },
                'net_position': {
                    'quantity': net_quantity,
                    'amount': net_amount,
                    'position': position,
                    'sentiment': sentiment
                },
                'stock_breakdown': dict(stock_breakdown),
                'interpretation': self._interpret_broker_position(position, net_quantity)
            }
            
        except Exception as e:
            logger.error(f"Error tracking broker {broker_id}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def identify_institutional_brokers(self,
                                      days: int = 30,
                                      min_trades: int = 100,
                                      min_volume: float = 100000) -> Dict[str, any]:
        """
        Identify institutional brokers based on activity
        
        Institutional brokers typically have:
        - High trade volume
        - Large average trade size
        - Consistent activity
        
        Args:
            days: Number of days to analyze
            min_trades: Minimum number of trades
            min_volume: Minimum total volume
            
        Returns:
            Dictionary with institutional brokers
        """
        try:
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Aggregate broker activity
            buyer_stats = (
                self.db.query(
                    Floorsheet.buyer_broker_id.label('broker_id'),
                    func.count(Floorsheet.id).label('trade_count'),
                    func.sum(Floorsheet.quantity).label('total_quantity'),
                    func.sum(Floorsheet.amount).label('total_amount'),
                    func.avg(Floorsheet.quantity).label('avg_quantity')
                )
                .filter(
                    and_(
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .group_by(Floorsheet.buyer_broker_id)
                .all()
            )
            
            seller_stats = (
                self.db.query(
                    Floorsheet.seller_broker_id.label('broker_id'),
                    func.count(Floorsheet.id).label('trade_count'),
                    func.sum(Floorsheet.quantity).label('total_quantity'),
                    func.sum(Floorsheet.amount).label('total_amount'),
                    func.avg(Floorsheet.quantity).label('avg_quantity')
                )
                .filter(
                    and_(
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .group_by(Floorsheet.seller_broker_id)
                .all()
            )
            
            # Combine statistics
            broker_stats = defaultdict(lambda: {
                'buy_trades': 0,
                'sell_trades': 0,
                'buy_volume': 0,
                'sell_volume': 0,
                'buy_amount': 0,
                'sell_amount': 0
            })
            
            for row in buyer_stats:
                broker_stats[row.broker_id]['buy_trades'] = row.trade_count
                broker_stats[row.broker_id]['buy_volume'] = float(row.total_quantity)
                broker_stats[row.broker_id]['buy_amount'] = float(row.total_amount)
            
            for row in seller_stats:
                broker_stats[row.broker_id]['sell_trades'] = row.trade_count
                broker_stats[row.broker_id]['sell_volume'] = float(row.total_quantity)
                broker_stats[row.broker_id]['sell_amount'] = float(row.total_amount)
            
            # Identify institutional brokers
            institutional_brokers = []
            
            for broker_id, stats in broker_stats.items():
                total_trades = stats['buy_trades'] + stats['sell_trades']
                total_volume = stats['buy_volume'] + stats['sell_volume']
                avg_trade_size = total_volume / total_trades if total_trades > 0 else 0
                
                # Check criteria
                if total_trades >= min_trades and total_volume >= min_volume:
                    institutional_brokers.append({
                        'broker_id': broker_id,
                        'total_trades': total_trades,
                        'total_volume': total_volume,
                        'total_amount': stats['buy_amount'] + stats['sell_amount'],
                        'avg_trade_size': avg_trade_size,
                        'buy_trades': stats['buy_trades'],
                        'sell_trades': stats['sell_trades'],
                        'net_volume': stats['buy_volume'] - stats['sell_volume'],
                        'position': 'long' if stats['buy_volume'] > stats['sell_volume'] else 'short'
                    })
            
            # Sort by total volume
            institutional_brokers.sort(key=lambda x: x['total_volume'], reverse=True)
            
            return {
                'success': True,
                'period_days': days,
                'criteria': {
                    'min_trades': min_trades,
                    'min_volume': min_volume
                },
                'institutional_brokers': institutional_brokers,
                'total_identified': len(institutional_brokers)
            }
            
        except Exception as e:
            logger.error(f"Error identifying institutional brokers: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_broker_sentiment(self,
                                symbol: str,
                                days: int = 7) -> Dict[str, any]:
        """
        Analyze overall broker sentiment for a stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dictionary with broker sentiment analysis
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
            
            # Get all trades
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
            
            # Count unique brokers
            unique_buyers = set(t.buyer_broker_id for t in trades)
            unique_sellers = set(t.seller_broker_id for t in trades)
            unique_brokers = unique_buyers.union(unique_sellers)
            
            # Calculate net positions
            broker_positions = defaultdict(lambda: {'buy': 0, 'sell': 0})
            
            for trade in trades:
                broker_positions[trade.buyer_broker_id]['buy'] += trade.quantity
                broker_positions[trade.seller_broker_id]['sell'] += trade.quantity
            
            # Classify brokers
            bullish_brokers = 0
            bearish_brokers = 0
            neutral_brokers = 0
            
            for broker_id, position in broker_positions.items():
                net = position['buy'] - position['sell']
                if net > 0:
                    bullish_brokers += 1
                elif net < 0:
                    bearish_brokers += 1
                else:
                    neutral_brokers += 1
            
            # Calculate overall sentiment
            total_brokers = len(unique_brokers)
            bullish_percent = (bullish_brokers / total_brokers * 100) if total_brokers > 0 else 0
            bearish_percent = (bearish_brokers / total_brokers * 100) if total_brokers > 0 else 0
            
            # Determine sentiment
            if bullish_percent > 60:
                sentiment = 'strongly_bullish'
            elif bullish_percent > 50:
                sentiment = 'bullish'
            elif bearish_percent > 60:
                sentiment = 'strongly_bearish'
            elif bearish_percent > 50:
                sentiment = 'bearish'
            else:
                sentiment = 'neutral'
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'total_brokers': total_brokers,
                'bullish_brokers': bullish_brokers,
                'bearish_brokers': bearish_brokers,
                'neutral_brokers': neutral_brokers,
                'bullish_percent': bullish_percent,
                'bearish_percent': bearish_percent,
                'sentiment': sentiment,
                'interpretation': self._interpret_sentiment(sentiment, bullish_percent, bearish_percent)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing broker sentiment for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_broker_rankings(self,
                           days: int = 30,
                           limit: int = 20) -> Dict[str, any]:
        """
        Get broker rankings by activity
        
        Args:
            days: Number of days to analyze
            limit: Number of top brokers to return
            
        Returns:
            Dictionary with broker rankings
        """
        try:
            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Aggregate broker activity
            trades = (
                self.db.query(Floorsheet)
                .filter(
                    and_(
                        Floorsheet.trade_date >= start_date,
                        Floorsheet.trade_date <= end_date
                    )
                )
                .all()
            )
            
            if not trades:
                return {
                    'success': False,
                    'error': 'No trade data found'
                }
            
            # Calculate broker statistics
            broker_stats = defaultdict(lambda: {
                'buy_trades': 0,
                'sell_trades': 0,
                'buy_volume': 0,
                'sell_volume': 0,
                'buy_amount': 0,
                'sell_amount': 0
            })
            
            for trade in trades:
                # Buyer
                broker_stats[trade.buyer_broker_id]['buy_trades'] += 1
                broker_stats[trade.buyer_broker_id]['buy_volume'] += trade.quantity
                broker_stats[trade.buyer_broker_id]['buy_amount'] += trade.amount
                
                # Seller
                broker_stats[trade.seller_broker_id]['sell_trades'] += 1
                broker_stats[trade.seller_broker_id]['sell_volume'] += trade.quantity
                broker_stats[trade.seller_broker_id]['sell_amount'] += trade.amount
            
            # Create ranking list
            rankings = []
            for broker_id, stats in broker_stats.items():
                total_trades = stats['buy_trades'] + stats['sell_trades']
                total_volume = stats['buy_volume'] + stats['sell_volume']
                total_amount = stats['buy_amount'] + stats['sell_amount']
                net_volume = stats['buy_volume'] - stats['sell_volume']
                
                rankings.append({
                    'broker_id': broker_id,
                    'rank': 0,  # Will be set after sorting
                    'total_trades': total_trades,
                    'total_volume': total_volume,
                    'total_amount': total_amount,
                    'net_volume': net_volume,
                    'buy_trades': stats['buy_trades'],
                    'sell_trades': stats['sell_trades'],
                    'position': 'long' if net_volume > 0 else 'short' if net_volume < 0 else 'neutral'
                })
            
            # Sort by total volume
            rankings.sort(key=lambda x: x['total_volume'], reverse=True)
            
            # Assign ranks
            for i, broker in enumerate(rankings[:limit], 1):
                broker['rank'] = i
            
            return {
                'success': True,
                'period_days': days,
                'top_brokers': rankings[:limit],
                'total_brokers': len(rankings)
            }
            
        except Exception as e:
            logger.error(f"Error getting broker rankings: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_broker_pairs(self,
                            symbol: str,
                            days: int = 7,
                            min_trades: int = 5) -> Dict[str, any]:
        """
        Analyze broker pair trading patterns
        
        Identifies brokers that frequently trade with each other,
        which can indicate coordinated activity.
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            min_trades: Minimum trades between pair
            
        Returns:
            Dictionary with broker pair analysis
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
            
            # Get trades
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
            
            # Count broker pairs
            pair_counts = defaultdict(lambda: {
                'trades': 0,
                'quantity': 0,
                'amount': 0
            })
            
            for trade in trades:
                pair = tuple(sorted([trade.buyer_broker_id, trade.seller_broker_id]))
                pair_counts[pair]['trades'] += 1
                pair_counts[pair]['quantity'] += trade.quantity
                pair_counts[pair]['amount'] += trade.amount
            
            # Filter and format pairs
            significant_pairs = []
            for pair, stats in pair_counts.items():
                if stats['trades'] >= min_trades:
                    significant_pairs.append({
                        'broker_1': pair[0],
                        'broker_2': pair[1],
                        'trades': stats['trades'],
                        'quantity': stats['quantity'],
                        'amount': stats['amount'],
                        'avg_trade_size': stats['quantity'] / stats['trades']
                    })
            
            # Sort by number of trades
            significant_pairs.sort(key=lambda x: x['trades'], reverse=True)
            
            return {
                'success': True,
                'symbol': symbol,
                'period_days': days,
                'min_trades': min_trades,
                'broker_pairs': significant_pairs,
                'total_pairs': len(significant_pairs),
                'interpretation': self._interpret_broker_pairs(len(significant_pairs))
            }
            
        except Exception as e:
            logger.error(f"Error analyzing broker pairs for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _interpret_broker_position(self, position: str, net_quantity: float) -> str:
        """Generate interpretation for broker position"""
        if position == 'long':
            return f"Broker is long (net: +{net_quantity:.0f} shares). Bullish positioning."
        elif position == 'short':
            return f"Broker is short (net: {net_quantity:.0f} shares). Bearish positioning."
        else:
            return "Broker has neutral position. No directional bias."
    
    def _interpret_sentiment(self, sentiment: str, bullish_pct: float, bearish_pct: float) -> str:
        """Generate interpretation for broker sentiment"""
        if sentiment == 'strongly_bullish':
            return f"Strong bullish sentiment ({bullish_pct:.1f}% bullish brokers). High conviction buying."
        elif sentiment == 'bullish':
            return f"Bullish sentiment ({bullish_pct:.1f}% bullish brokers). More buyers than sellers."
        elif sentiment == 'strongly_bearish':
            return f"Strong bearish sentiment ({bearish_pct:.1f}% bearish brokers). High conviction selling."
        elif sentiment == 'bearish':
            return f"Bearish sentiment ({bearish_pct:.1f}% bearish brokers). More sellers than buyers."
        else:
            return "Neutral sentiment. Balanced broker positioning."
    
    def _interpret_broker_pairs(self, pair_count: int) -> str:
        """Generate interpretation for broker pairs"""
        if pair_count > 10:
            return f"High number of active broker pairs ({pair_count}). Possible coordinated activity."
        elif pair_count > 5:
            return f"Moderate broker pair activity ({pair_count}). Normal trading patterns."
        else:
            return f"Low broker pair activity ({pair_count}). Diverse trading counterparties."


# Convenience functions
def track_broker_activity(db: Session, broker_id: str, days: int = 30) -> Dict[str, any]:
    """
    Quick broker tracking
    
    Args:
        db: Database session
        broker_id: Broker ID to track
        days: Number of days to analyze
        
    Returns:
        Broker activity analysis
    """
    tracker = BrokerTracker(db)
    return tracker.track_broker(broker_id, days=days)
