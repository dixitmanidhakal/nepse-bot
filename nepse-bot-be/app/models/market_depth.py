"""
Market Depth Model

This model stores order book data (market depth) for stocks.
Used for analyzing buy/sell pressure and detecting institutional activity.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Index, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class MarketDepth(Base):
    """
    Market Depth Model
    
    Stores order book data showing buy and sell orders at different price levels.
    Critical for detecting support/resistance and institutional activity.
    """
    
    __tablename__ = "market_depth"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign Key
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Timestamp
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Current Price
    ltp = Column(Float, nullable=True)  # Last Traded Price
    
    # Buy Orders (Bids) - Top 5 levels
    buy_price_1 = Column(Float, nullable=True)
    buy_quantity_1 = Column(Float, nullable=True)
    buy_orders_1 = Column(Integer, nullable=True)
    
    buy_price_2 = Column(Float, nullable=True)
    buy_quantity_2 = Column(Float, nullable=True)
    buy_orders_2 = Column(Integer, nullable=True)
    
    buy_price_3 = Column(Float, nullable=True)
    buy_quantity_3 = Column(Float, nullable=True)
    buy_orders_3 = Column(Integer, nullable=True)
    
    buy_price_4 = Column(Float, nullable=True)
    buy_quantity_4 = Column(Float, nullable=True)
    buy_orders_4 = Column(Integer, nullable=True)
    
    buy_price_5 = Column(Float, nullable=True)
    buy_quantity_5 = Column(Float, nullable=True)
    buy_orders_5 = Column(Integer, nullable=True)
    
    # Sell Orders (Asks) - Top 5 levels
    sell_price_1 = Column(Float, nullable=True)
    sell_quantity_1 = Column(Float, nullable=True)
    sell_orders_1 = Column(Integer, nullable=True)
    
    sell_price_2 = Column(Float, nullable=True)
    sell_quantity_2 = Column(Float, nullable=True)
    sell_orders_2 = Column(Integer, nullable=True)
    
    sell_price_3 = Column(Float, nullable=True)
    sell_quantity_3 = Column(Float, nullable=True)
    sell_orders_3 = Column(Integer, nullable=True)
    
    sell_price_4 = Column(Float, nullable=True)
    sell_quantity_4 = Column(Float, nullable=True)
    sell_orders_4 = Column(Integer, nullable=True)
    
    sell_price_5 = Column(Float, nullable=True)
    sell_quantity_5 = Column(Float, nullable=True)
    sell_orders_5 = Column(Integer, nullable=True)
    
    # Aggregated Metrics
    total_buy_quantity = Column(Float, nullable=True)  # Sum of all buy quantities
    total_sell_quantity = Column(Float, nullable=True)  # Sum of all sell quantities
    total_buy_orders = Column(Integer, nullable=True)  # Total number of buy orders
    total_sell_orders = Column(Integer, nullable=True)  # Total number of sell orders
    
    # Calculated Metrics
    order_imbalance = Column(Float, nullable=True)  # (Buy - Sell) / (Buy + Sell)
    bid_ask_spread = Column(Float, nullable=True)  # Sell Price 1 - Buy Price 1
    bid_ask_spread_percent = Column(Float, nullable=True)  # Spread as % of LTP
    
    # Liquidity Metrics
    liquidity_score = Column(Float, nullable=True)  # Custom liquidity score
    depth_ratio = Column(Float, nullable=True)  # Buy depth / Sell depth
    
    # Flags
    has_bid_wall = Column(Integer, default=False, nullable=False)  # Large buy order detected
    has_ask_wall = Column(Integer, default=False, nullable=False)  # Large sell order detected
    
    # Raw Data (for flexibility)
    raw_data = Column(JSON, nullable=True)  # Store complete order book if needed
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    stock = relationship("Stock", back_populates="market_depth")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_market_depth_stock_id', 'stock_id'),
        Index('idx_market_depth_timestamp', 'timestamp'),
        Index('idx_market_depth_stock_timestamp', 'stock_id', 'timestamp'),
        Index('idx_market_depth_imbalance', 'order_imbalance'),
        Index('idx_market_depth_walls', 'has_bid_wall', 'has_ask_wall'),
    )
    
    def __repr__(self):
        return f"<MarketDepth(stock_id={self.stock_id}, timestamp={self.timestamp}, imbalance={self.order_imbalance})>"
    
    def to_dict(self, include_stock=False):
        """Convert model to dictionary"""
        data = {
            "id": self.id,
            "stock_id": self.stock_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "ltp": self.ltp,
            "buy_orders": [
                {
                    "price": self.buy_price_1,
                    "quantity": self.buy_quantity_1,
                    "orders": self.buy_orders_1
                },
                {
                    "price": self.buy_price_2,
                    "quantity": self.buy_quantity_2,
                    "orders": self.buy_orders_2
                },
                {
                    "price": self.buy_price_3,
                    "quantity": self.buy_quantity_3,
                    "orders": self.buy_orders_3
                },
                {
                    "price": self.buy_price_4,
                    "quantity": self.buy_quantity_4,
                    "orders": self.buy_orders_4
                },
                {
                    "price": self.buy_price_5,
                    "quantity": self.buy_quantity_5,
                    "orders": self.buy_orders_5
                }
            ],
            "sell_orders": [
                {
                    "price": self.sell_price_1,
                    "quantity": self.sell_quantity_1,
                    "orders": self.sell_orders_1
                },
                {
                    "price": self.sell_price_2,
                    "quantity": self.sell_quantity_2,
                    "orders": self.sell_orders_2
                },
                {
                    "price": self.sell_price_3,
                    "quantity": self.sell_quantity_3,
                    "orders": self.sell_orders_3
                },
                {
                    "price": self.sell_price_4,
                    "quantity": self.sell_quantity_4,
                    "orders": self.sell_orders_4
                },
                {
                    "price": self.sell_price_5,
                    "quantity": self.sell_quantity_5,
                    "orders": self.sell_orders_5
                }
            ],
            "total_buy_quantity": self.total_buy_quantity,
            "total_sell_quantity": self.total_sell_quantity,
            "total_buy_orders": self.total_buy_orders,
            "total_sell_orders": self.total_sell_orders,
            "order_imbalance": self.order_imbalance,
            "bid_ask_spread": self.bid_ask_spread,
            "bid_ask_spread_percent": self.bid_ask_spread_percent,
            "liquidity_score": self.liquidity_score,
            "depth_ratio": self.depth_ratio,
            "has_bid_wall": self.has_bid_wall,
            "has_ask_wall": self.has_ask_wall,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
        
        if include_stock and self.stock:
            data["stock"] = {
                "symbol": self.stock.symbol,
                "name": self.stock.name
            }
        
        return data
