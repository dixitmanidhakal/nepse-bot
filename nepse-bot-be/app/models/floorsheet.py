"""
Floorsheet Model

This model stores individual trade details from the floorsheet.
Used for broker analysis, manipulation detection, and institutional tracking.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Floorsheet(Base):
    """
    Floorsheet Model
    
    Stores individual trade details including broker information.
    Essential for tracking institutional activity and detecting manipulation.
    """
    
    __tablename__ = "floorsheet"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign Key
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Trade Information
    trade_date = Column(Date, nullable=False, index=True)
    trade_time = Column(DateTime(timezone=True), nullable=True)
    contract_id = Column(String(50), nullable=True, unique=True, index=True)
    
    # Broker Information
    buyer_broker_id = Column(String(20), nullable=False, index=True)
    buyer_broker_name = Column(String(200), nullable=True)
    seller_broker_id = Column(String(20), nullable=False, index=True)
    seller_broker_name = Column(String(200), nullable=True)
    
    # Trade Details
    quantity = Column(Float, nullable=False)
    rate = Column(Float, nullable=False)  # Price per share
    amount = Column(Float, nullable=False)  # Total amount (quantity * rate)
    
    # Trade Classification
    is_institutional = Column(Integer, default=False, nullable=False)  # Large trade flag
    is_cross_trade = Column(Integer, default=False, nullable=False)  # Same broker buy/sell
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    stock = relationship("Stock", back_populates="floorsheet")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_floorsheet_stock_id', 'stock_id'),
        Index('idx_floorsheet_trade_date', 'trade_date'),
        Index('idx_floorsheet_stock_date', 'stock_id', 'trade_date'),
        Index('idx_floorsheet_buyer_broker', 'buyer_broker_id'),
        Index('idx_floorsheet_seller_broker', 'seller_broker_id'),
        Index('idx_floorsheet_brokers', 'buyer_broker_id', 'seller_broker_id'),
        Index('idx_floorsheet_amount', 'amount'),
        Index('idx_floorsheet_institutional', 'is_institutional'),
    )
    
    def __repr__(self):
        return f"<Floorsheet(stock_id={self.stock_id}, date={self.trade_date}, buyer={self.buyer_broker_id}, seller={self.seller_broker_id})>"
    
    def to_dict(self, include_stock=False):
        """Convert model to dictionary"""
        data = {
            "id": self.id,
            "stock_id": self.stock_id,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "trade_time": self.trade_time.isoformat() if self.trade_time else None,
            "contract_id": self.contract_id,
            "buyer_broker_id": self.buyer_broker_id,
            "buyer_broker_name": self.buyer_broker_name,
            "seller_broker_id": self.seller_broker_id,
            "seller_broker_name": self.seller_broker_name,
            "quantity": self.quantity,
            "rate": self.rate,
            "amount": self.amount,
            "is_institutional": self.is_institutional,
            "is_cross_trade": self.is_cross_trade,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
        
        if include_stock and self.stock:
            data["stock"] = {
                "symbol": self.stock.symbol,
                "name": self.stock.name
            }
        
        return data
