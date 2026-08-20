"""
Market Depth Analyzer Module

This module analyzes order book data (market depth) to identify:
- Order imbalances
- Bid/Ask walls
- Liquidity levels
- Support/Resistance from order book
- Price pressure
- Spread analysis

Used for detecting institutional activity and predicting short-term price movements.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Union
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_
import logging

from app.models.stock import Stock
from app.models.market_depth import MarketDepth

logger = logging.getLogger(__name__)


class MarketDepthAnalyzer:
    """
    Market Depth Analyzer
    
    Analyzes order book data to detect buying/selling pressure,
    liquidity, and potential price movements.
    """
    
    def __init__(self, db: Session):
        """
        Initialize analyzer with database session
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_current_depth(self, symbol: str) -> Optional[Dict[str, any]]:
        """
        Get current market depth for a stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with current order book data
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                logger.error(f"Stock not found: {symbol}")
                return None
            
            # Get latest market depth
            depth = (
                self.db.query(MarketDepth)
                .filter(MarketDepth.stock_id == stock.id)
                .order_by(desc(MarketDepth.timestamp))
                .first()
            )
            
            if not depth:
                logger.warning(f"No market depth data found for {symbol}")
                return None
            
            return depth.to_dict(include_stock=True)
            
        except Exception as e:
            logger.error(f"Error getting current depth for {symbol}: {str(e)}")
            return None
    
    def analyze_order_imbalance(self, symbol: str) -> Dict[str, any]:
        """
        Analyze order book imbalance
        
        Order imbalance indicates buying or selling pressure.
        Positive imbalance = More buying pressure
        Negative imbalance = More selling pressure
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with imbalance analysis
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            total_buy_qty = depth_data.get('total_buy_quantity', 0) or 0
            total_sell_qty = depth_data.get('total_sell_quantity', 0) or 0
            imbalance = depth_data.get('order_imbalance', 0) or 0
            
            # Calculate imbalance ratio
            if total_buy_qty + total_sell_qty > 0:
                buy_ratio = total_buy_qty / (total_buy_qty + total_sell_qty)
                sell_ratio = total_sell_qty / (total_buy_qty + total_sell_qty)
            else:
                buy_ratio = 0.5
                sell_ratio = 0.5
            
            # Determine pressure
            if imbalance > 0.2:
                pressure = 'strong_buying'
            elif imbalance > 0.05:
                pressure = 'buying'
            elif imbalance < -0.2:
                pressure = 'strong_selling'
            elif imbalance < -0.05:
                pressure = 'selling'
            else:
                pressure = 'balanced'
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': depth_data.get('ltp'),
                'order_imbalance': imbalance,
                'total_buy_quantity': total_buy_qty,
                'total_sell_quantity': total_sell_qty,
                'buy_ratio': buy_ratio,
                'sell_ratio': sell_ratio,
                'pressure': pressure,
                'interpretation': self._interpret_imbalance(imbalance)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing order imbalance for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def detect_walls(self, symbol: str, wall_threshold: float = 2.0) -> Dict[str, any]:
        """
        Detect bid/ask walls (large orders)
        
        Walls are large orders that can act as support/resistance.
        
        Args:
            symbol: Stock symbol
            wall_threshold: Multiplier of average order size to consider as wall
            
        Returns:
            Dictionary with detected walls
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            buy_orders = depth_data.get('buy_orders', [])
            sell_orders = depth_data.get('sell_orders', [])
            
            # Calculate average order sizes
            buy_quantities = [o['quantity'] for o in buy_orders if o['quantity']]
            sell_quantities = [o['quantity'] for o in sell_orders if o['quantity']]
            
            avg_buy_qty = np.mean(buy_quantities) if buy_quantities else 0
            avg_sell_qty = np.mean(sell_quantities) if sell_quantities else 0
            
            # Detect walls
            bid_walls = []
            ask_walls = []
            
            for order in buy_orders:
                if order['quantity'] and order['quantity'] >= avg_buy_qty * wall_threshold:
                    bid_walls.append({
                        'price': order['price'],
                        'quantity': order['quantity'],
                        'orders': order['orders'],
                        'strength': order['quantity'] / avg_buy_qty if avg_buy_qty > 0 else 0
                    })
            
            for order in sell_orders:
                if order['quantity'] and order['quantity'] >= avg_sell_qty * wall_threshold:
                    ask_walls.append({
                        'price': order['price'],
                        'quantity': order['quantity'],
                        'orders': order['orders'],
                        'strength': order['quantity'] / avg_sell_qty if avg_sell_qty > 0 else 0
                    })
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': depth_data.get('ltp'),
                'bid_walls': bid_walls,
                'ask_walls': ask_walls,
                'has_bid_wall': len(bid_walls) > 0,
                'has_ask_wall': len(ask_walls) > 0,
                'interpretation': self._interpret_walls(bid_walls, ask_walls)
            }
            
        except Exception as e:
            logger.error(f"Error detecting walls for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_liquidity(self, symbol: str) -> Dict[str, any]:
        """
        Analyze market liquidity
        
        Liquidity indicates how easily a stock can be bought/sold.
        High liquidity = Tight spreads, deep order book
        Low liquidity = Wide spreads, thin order book
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with liquidity analysis
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            ltp = depth_data.get('ltp', 0) or 0
            spread = depth_data.get('bid_ask_spread', 0) or 0
            spread_percent = depth_data.get('bid_ask_spread_percent', 0) or 0
            total_buy_qty = depth_data.get('total_buy_quantity', 0) or 0
            total_sell_qty = depth_data.get('total_sell_quantity', 0) or 0
            liquidity_score = depth_data.get('liquidity_score', 0) or 0
            
            # Calculate depth
            total_depth = total_buy_qty + total_sell_qty
            
            # Determine liquidity level
            if spread_percent < 0.1 and total_depth > 10000:
                liquidity_level = 'very_high'
            elif spread_percent < 0.5 and total_depth > 5000:
                liquidity_level = 'high'
            elif spread_percent < 1.0 and total_depth > 1000:
                liquidity_level = 'medium'
            elif spread_percent < 2.0:
                liquidity_level = 'low'
            else:
                liquidity_level = 'very_low'
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': ltp,
                'bid_ask_spread': spread,
                'spread_percent': spread_percent,
                'total_depth': total_depth,
                'buy_depth': total_buy_qty,
                'sell_depth': total_sell_qty,
                'liquidity_score': liquidity_score,
                'liquidity_level': liquidity_level,
                'interpretation': self._interpret_liquidity(liquidity_level, spread_percent)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing liquidity for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_support_resistance_from_depth(self, symbol: str) -> Dict[str, any]:
        """
        Identify support/resistance levels from order book
        
        Large orders in the order book can act as support/resistance.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with support/resistance levels
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            buy_orders = depth_data.get('buy_orders', [])
            sell_orders = depth_data.get('sell_orders', [])
            ltp = depth_data.get('ltp', 0) or 0
            
            # Extract support levels (buy orders)
            support_levels = []
            for order in buy_orders:
                if order['price'] and order['quantity']:
                    support_levels.append({
                        'price': order['price'],
                        'quantity': order['quantity'],
                        'distance_percent': ((ltp - order['price']) / ltp * 100) if ltp > 0 else 0,
                        'strength': 'strong' if order['quantity'] > 1000 else 'moderate' if order['quantity'] > 500 else 'weak'
                    })
            
            # Extract resistance levels (sell orders)
            resistance_levels = []
            for order in sell_orders:
                if order['price'] and order['quantity']:
                    resistance_levels.append({
                        'price': order['price'],
                        'quantity': order['quantity'],
                        'distance_percent': ((order['price'] - ltp) / ltp * 100) if ltp > 0 else 0,
                        'strength': 'strong' if order['quantity'] > 1000 else 'moderate' if order['quantity'] > 500 else 'weak'
                    })
            
            # Find nearest levels
            nearest_support = support_levels[0] if support_levels else None
            nearest_resistance = resistance_levels[0] if resistance_levels else None
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': ltp,
                'support_levels': support_levels,
                'resistance_levels': resistance_levels,
                'nearest_support': nearest_support,
                'nearest_resistance': nearest_resistance
            }
            
        except Exception as e:
            logger.error(f"Error getting support/resistance from depth for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_spread(self, symbol: str) -> Dict[str, any]:
        """
        Analyze bid-ask spread
        
        Spread indicates transaction cost and liquidity.
        Tight spread = High liquidity, low cost
        Wide spread = Low liquidity, high cost
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with spread analysis
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            buy_orders = depth_data.get('buy_orders', [])
            sell_orders = depth_data.get('sell_orders', [])
            ltp = depth_data.get('ltp', 0) or 0
            spread = depth_data.get('bid_ask_spread', 0) or 0
            spread_percent = depth_data.get('bid_ask_spread_percent', 0) or 0
            
            # Get best bid and ask
            best_bid = buy_orders[0]['price'] if buy_orders and buy_orders[0]['price'] else None
            best_ask = sell_orders[0]['price'] if sell_orders and sell_orders[0]['price'] else None
            
            # Determine spread quality
            if spread_percent < 0.1:
                spread_quality = 'very_tight'
            elif spread_percent < 0.5:
                spread_quality = 'tight'
            elif spread_percent < 1.0:
                spread_quality = 'moderate'
            elif spread_percent < 2.0:
                spread_quality = 'wide'
            else:
                spread_quality = 'very_wide'
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': ltp,
                'best_bid': best_bid,
                'best_ask': best_ask,
                'spread': spread,
                'spread_percent': spread_percent,
                'spread_quality': spread_quality,
                'interpretation': self._interpret_spread(spread_quality, spread_percent)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing spread for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def calculate_price_pressure(self, symbol: str) -> Dict[str, any]:
        """
        Calculate price pressure from order book
        
        Price pressure indicates likely direction of next price movement.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with price pressure analysis
        """
        try:
            depth_data = self.get_current_depth(symbol)
            
            if not depth_data:
                return {
                    'success': False,
                    'error': f'No market depth data for {symbol}'
                }
            
            total_buy_qty = depth_data.get('total_buy_quantity', 0) or 0
            total_sell_qty = depth_data.get('total_sell_quantity', 0) or 0
            depth_ratio = depth_data.get('depth_ratio', 1.0) or 1.0
            imbalance = depth_data.get('order_imbalance', 0) or 0
            
            # Calculate pressure score (-1 to +1)
            # Positive = Upward pressure, Negative = Downward pressure
            pressure_score = imbalance
            
            # Determine pressure direction
            if pressure_score > 0.3:
                direction = 'strong_upward'
            elif pressure_score > 0.1:
                direction = 'upward'
            elif pressure_score < -0.3:
                direction = 'strong_downward'
            elif pressure_score < -0.1:
                direction = 'downward'
            else:
                direction = 'neutral'
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': depth_data.get('timestamp'),
                'ltp': depth_data.get('ltp'),
                'pressure_score': pressure_score,
                'direction': direction,
                'buy_pressure': total_buy_qty,
                'sell_pressure': total_sell_qty,
                'depth_ratio': depth_ratio,
                'interpretation': self._interpret_pressure(direction, pressure_score)
            }
            
        except Exception as e:
            logger.error(f"Error calculating price pressure for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_depth_history(self, symbol: str, hours: int = 24) -> Dict[str, any]:
        """
        Get historical market depth data
        
        Args:
            symbol: Stock symbol
            hours: Number of hours of history
            
        Returns:
            Dictionary with historical depth data
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter(Stock.symbol == symbol).first()
            
            if not stock:
                return {
                    'success': False,
                    'error': f'Stock not found: {symbol}'
                }
            
            # Calculate time range
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            # Fetch historical depth
            depth_records = (
                self.db.query(MarketDepth)
                .filter(
                    and_(
                        MarketDepth.stock_id == stock.id,
                        MarketDepth.timestamp >= start_time,
                        MarketDepth.timestamp <= end_time
                    )
                )
                .order_by(MarketDepth.timestamp.asc())
                .all()
            )
            
            if not depth_records:
                return {
                    'success': False,
                    'error': f'No historical depth data for {symbol}'
                }
            
            # Convert to list
            history = [record.to_dict() for record in depth_records]
            
            # Calculate statistics
            imbalances = [r['order_imbalance'] for r in history if r['order_imbalance'] is not None]
            spreads = [r['bid_ask_spread_percent'] for r in history if r['bid_ask_spread_percent'] is not None]
            
            return {
                'success': True,
                'symbol': symbol,
                'period_hours': hours,
                'data_points': len(history),
                'history': history,
                'statistics': {
                    'avg_imbalance': float(np.mean(imbalances)) if imbalances else None,
                    'avg_spread_percent': float(np.mean(spreads)) if spreads else None,
                    'max_imbalance': float(np.max(imbalances)) if imbalances else None,
                    'min_imbalance': float(np.min(imbalances)) if imbalances else None
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting depth history for {symbol}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _interpret_imbalance(self, imbalance: float) -> str:
        """Generate interpretation for order imbalance"""
        if imbalance > 0.3:
            return "Strong buying pressure detected. Price likely to move up."
        elif imbalance > 0.1:
            return "Moderate buying pressure. Bullish sentiment."
        elif imbalance < -0.3:
            return "Strong selling pressure detected. Price likely to move down."
        elif imbalance < -0.1:
            return "Moderate selling pressure. Bearish sentiment."
        else:
            return "Balanced order book. No clear directional bias."
    
    def _interpret_walls(self, bid_walls: List, ask_walls: List) -> str:
        """Generate interpretation for walls"""
        if bid_walls and not ask_walls:
            return "Bid wall detected. Strong support level, potential accumulation."
        elif ask_walls and not bid_walls:
            return "Ask wall detected. Strong resistance level, potential distribution."
        elif bid_walls and ask_walls:
            return "Both bid and ask walls present. Price likely to consolidate."
        else:
            return "No significant walls detected. Normal order flow."
    
    def _interpret_liquidity(self, level: str, spread_percent: float) -> str:
        """Generate interpretation for liquidity"""
        if level == 'very_high':
            return f"Excellent liquidity with {spread_percent:.2f}% spread. Easy to trade."
        elif level == 'high':
            return f"Good liquidity with {spread_percent:.2f}% spread. Favorable for trading."
        elif level == 'medium':
            return f"Moderate liquidity with {spread_percent:.2f}% spread. Acceptable for trading."
        elif level == 'low':
            return f"Low liquidity with {spread_percent:.2f}% spread. Higher transaction costs."
        else:
            return f"Very low liquidity with {spread_percent:.2f}% spread. Difficult to trade."
    
    def _interpret_spread(self, quality: str, spread_percent: float) -> str:
        """Generate interpretation for spread"""
        if quality == 'very_tight':
            return f"Very tight spread ({spread_percent:.2f}%). Excellent for trading."
        elif quality == 'tight':
            return f"Tight spread ({spread_percent:.2f}%). Good for trading."
        elif quality == 'moderate':
            return f"Moderate spread ({spread_percent:.2f}%). Acceptable transaction cost."
        elif quality == 'wide':
            return f"Wide spread ({spread_percent:.2f}%). Higher transaction cost."
        else:
            return f"Very wide spread ({spread_percent:.2f}%). Significant transaction cost."
    
    def _interpret_pressure(self, direction: str, score: float) -> str:
        """Generate interpretation for price pressure"""
        if direction == 'strong_upward':
            return f"Strong upward pressure (score: {score:.2f}). Price likely to rise."
        elif direction == 'upward':
            return f"Upward pressure (score: {score:.2f}). Bullish bias."
        elif direction == 'strong_downward':
            return f"Strong downward pressure (score: {score:.2f}). Price likely to fall."
        elif direction == 'downward':
            return f"Downward pressure (score: {score:.2f}). Bearish bias."
        else:
            return f"Neutral pressure (score: {score:.2f}). No clear direction."


# Convenience functions
def analyze_market_depth(db: Session, symbol: str) -> Dict[str, any]:
    """
    Quick market depth analysis
    
    Args:
        db: Database session
        symbol: Stock symbol
        
    Returns:
        Comprehensive depth analysis
    """
    analyzer = MarketDepthAnalyzer(db)
    
    return {
        'current_depth': analyzer.get_current_depth(symbol),
        'imbalance': analyzer.analyze_order_imbalance(symbol),
        'walls': analyzer.detect_walls(symbol),
        'liquidity': analyzer.analyze_liquidity(symbol),
        'pressure': analyzer.calculate_price_pressure(symbol)
    }
