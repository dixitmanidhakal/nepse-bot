"""
Populate Database with Mock Data

This script populates the database with realistic mock data
for testing when the NEPSE API is not available.
"""

import sys
import os
from datetime import datetime

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models.sector import Sector
from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.models.market_depth import MarketDepth
from app.models.floorsheet import Floorsheet
from app.utils.mock_data_generator import (
    generate_mock_stocks,
    generate_mock_ohlcv,
    generate_mock_depth,
    generate_mock_floorsheet,
    generate_mock_sectors
)
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_tables():
    """Create all database tables"""
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Database tables created")


def populate_sectors(db: Session):
    """Populate sectors table"""
    logger.info("Populating sectors...")
    
    sectors_data = generate_mock_sectors()
    
    for sector_data in sectors_data:
        sector = Sector(
            name=sector_data["name"],
            symbol=sector_data["symbol"],
            index=sector_data["index"],
            change=sector_data["change"],
            change_percent=sector_data["change_percent"],
            turnover=sector_data["turnover"],
            volume=sector_data["volume"]
        )
        db.add(sector)
    
    db.commit()
    logger.info(f"✅ Added {len(sectors_data)} sectors")


def populate_stocks(db: Session):
    """Populate stocks table"""
    logger.info("Populating stocks...")
    
    stocks_data = generate_mock_stocks()
    
    for stock_data in stocks_data:
        # Get sector
        sector = db.query(Sector).filter(
            Sector.name == stock_data["sector"]
        ).first()
        
        if not sector:
            logger.warning(f"Sector not found: {stock_data['sector']}, skipping stock {stock_data['symbol']}")
            continue
        
        stock = Stock(
            symbol=stock_data["symbol"],
            name=stock_data["name"],
            sector_id=sector.id,
            ltp=stock_data["ltp"],
            previous_close=stock_data["previous_close"],
            open_price=stock_data["open_price"],
            high_price=stock_data["high_price"],
            low_price=stock_data["low_price"],
            change=stock_data["change"],
            change_percent=stock_data["change_percent"],
            volume=stock_data["volume"],
            turnover=stock_data["turnover"],
            total_trades=stock_data["total_trades"],
            market_cap=stock_data["market_cap"],
            outstanding_shares=stock_data["outstanding_shares"],
            eps=stock_data["eps"],
            pe_ratio=stock_data["pe_ratio"],
            book_value=stock_data["book_value"],
            pb_ratio=stock_data["pb_ratio"],
            is_active=stock_data["is_active"],
            is_tradeable=stock_data["is_tradeable"]
        )
        db.add(stock)
    
    db.commit()
    logger.info(f"✅ Added {len(stocks_data)} stocks")


def populate_ohlcv(db: Session):
    """Populate OHLCV data"""
    logger.info("Populating OHLCV data...")
    
    stocks = db.query(Stock).all()
    total_records = 0
    
    for stock in stocks:
        ohlcv_data = generate_mock_ohlcv(stock.symbol, days=100)
        
        for ohlcv in ohlcv_data:
            record = StockOHLCV(
                stock_id=stock.id,
                date=ohlcv["date"],
                open=ohlcv["open"],
                high=ohlcv["high"],
                low=ohlcv["low"],
                close=ohlcv["close"],
                volume=ohlcv["volume"],
                turnover=ohlcv["turnover"],
                total_trades=ohlcv["total_trades"]
            )
            db.add(record)
            total_records += 1
        
        if total_records % 500 == 0:
            db.commit()
            logger.info(f"  Added {total_records} OHLCV records...")
    
    db.commit()
    logger.info(f"✅ Added {total_records} OHLCV records")


def populate_market_depth(db: Session):
    """Populate market depth data"""
    logger.info("Populating market depth data...")
    
    stocks = db.query(Stock).all()
    total_records = 0
    
    for stock in stocks:
        depth_data = generate_mock_depth(stock.symbol)
        
        # Add buy orders
        for i, order in enumerate(depth_data["buy_orders"]):
            record = MarketDepth(
                stock_id=stock.id,
                order_type="buy",
                price=order["price"],
                quantity=order["quantity"],
                orders=order["orders"],
                position=i + 1,
                timestamp=depth_data["timestamp"]
            )
            db.add(record)
            total_records += 1
        
        # Add sell orders
        for i, order in enumerate(depth_data["sell_orders"]):
            record = MarketDepth(
                stock_id=stock.id,
                order_type="sell",
                price=order["price"],
                quantity=order["quantity"],
                orders=order["orders"],
                position=i + 1,
                timestamp=depth_data["timestamp"]
            )
            db.add(record)
            total_records += 1
    
    db.commit()
    logger.info(f"✅ Added {total_records} market depth records")


def populate_floorsheet(db: Session):
    """Populate floorsheet data"""
    logger.info("Populating floorsheet data...")
    
    stocks = db.query(Stock).all()
    total_records = 0
    
    for stock in stocks:
        floorsheet_data = generate_mock_floorsheet(stock.symbol, trades=50)
        
        for trade in floorsheet_data:
            record = Floorsheet(
                stock_id=stock.id,
                buyer_broker=trade["buyer_broker"],
                seller_broker=trade["seller_broker"],
                quantity=trade["quantity"],
                price=trade["price"],
                amount=trade["amount"],
                trade_time=trade["trade_time"]
            )
            db.add(record)
            total_records += 1
        
        if total_records % 500 == 0:
            db.commit()
            logger.info(f"  Added {total_records} floorsheet records...")
    
    db.commit()
    logger.info(f"✅ Added {total_records} floorsheet records")


def main():
    """Main function to populate all data"""
    logger.info("=" * 60)
    logger.info("NEPSE Trading Bot - Mock Data Population")
    logger.info("=" * 60)
    
    try:
        # Create tables
        create_tables()
        
        # Create database session
        db = SessionLocal()
        
        try:
            # Check if data already exists
            existing_stocks = db.query(Stock).count()
            if existing_stocks > 0:
                logger.warning(f"⚠️  Database already contains {existing_stocks} stocks")
                response = input("Do you want to clear existing data and repopulate? (yes/no): ")
                if response.lower() != 'yes':
                    logger.info("Aborted by user")
                    return
                
                # Clear existing data
                logger.info("Clearing existing data...")
                db.query(Floorsheet).delete()
                db.query(MarketDepth).delete()
                db.query(StockOHLCV).delete()
                db.query(Stock).delete()
                db.query(Sector).delete()
                db.commit()
                logger.info("✅ Existing data cleared")
            
            # Populate data
            populate_sectors(db)
            populate_stocks(db)
            populate_ohlcv(db)
            populate_market_depth(db)
            populate_floorsheet(db)
            
            # Summary
            logger.info("=" * 60)
            logger.info("📊 Data Population Summary:")
            logger.info(f"  Sectors: {db.query(Sector).count()}")
            logger.info(f"  Stocks: {db.query(Stock).count()}")
            logger.info(f"  OHLCV Records: {db.query(StockOHLCV).count()}")
            logger.info(f"  Market Depth Records: {db.query(MarketDepth).count()}")
            logger.info(f"  Floorsheet Records: {db.query(Floorsheet).count()}")
            logger.info("=" * 60)
            logger.info("✅ Mock data population completed successfully!")
            logger.info("=" * 60)
            
        finally:
            db.close()
    
    except Exception as e:
        logger.error(f"❌ Error populating data: {e}")
        raise


if __name__ == "__main__":
    main()
