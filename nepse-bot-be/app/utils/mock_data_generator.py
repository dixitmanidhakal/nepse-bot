"""
Mock Data Generator

Generates realistic mock data for testing the NEPSE Trading Bot
when the actual NEPSE API is not available.
"""

import random
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class MockDataGenerator:
    """Generate realistic mock data for NEPSE stocks"""
    
    # Real NEPSE stock symbols and names
    STOCKS = [
        {"symbol": "NABIL", "name": "Nabil Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "SCB", "name": "Standard Chartered Bank Nepal Limited", "sector": "Commercial Banks"},
        {"symbol": "HBL", "name": "Himalayan Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "EBL", "name": "Everest Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "NICA", "name": "NIC Asia Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "NBL", "name": "Nepal Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "SBI", "name": "Nepal SBI Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "PRVU", "name": "Prabhu Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "GBIME", "name": "Global IME Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "CZBIL", "name": "Citizen Bank International Limited", "sector": "Commercial Banks"},
        {"symbol": "SANIMA", "name": "Sanima Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "MEGA", "name": "Mega Bank Nepal Limited", "sector": "Commercial Banks"},
        {"symbol": "NICA", "name": "NIC Asia Bank Limited", "sector": "Commercial Banks"},
        {"symbol": "ADBL", "name": "Agricultural Development Bank Limited", "sector": "Development Banks"},
        {"symbol": "NLIC", "name": "Nepal Life Insurance Co. Ltd.", "sector": "Life Insurance"},
        {"symbol": "NLICL", "name": "NLG Insurance Company Ltd.", "sector": "Non Life Insurance"},
        {"symbol": "NIFRA", "name": "Nepal Infrastructure Bank Limited", "sector": "Development Banks"},
        {"symbol": "UPPER", "name": "Upper Tamakoshi Hydropower Ltd", "sector": "Hydro Power"},
        {"symbol": "CHCL", "name": "Chilime Hydropower Company Limited", "sector": "Hydro Power"},
        {"symbol": "NHPC", "name": "National Hydropower Company Limited", "sector": "Hydro Power"},
    ]
    
    SECTORS = [
        "Commercial Banks",
        "Development Banks",
        "Finance",
        "Life Insurance",
        "Non Life Insurance",
        "Hydro Power",
        "Manufacturing",
        "Hotels",
        "Trading",
        "Others"
    ]
    
    @staticmethod
    def generate_stock_data() -> List[Dict[str, Any]]:
        """Generate mock stock data"""
        stocks = []
        
        for stock_info in MockDataGenerator.STOCKS:
            base_price = random.uniform(200, 2000)
            
            stock = {
                "symbol": stock_info["symbol"],
                "name": stock_info["name"],
                "sector": stock_info["sector"],
                "ltp": round(base_price, 2),
                "previous_close": round(base_price * random.uniform(0.98, 1.02), 2),
                "open_price": round(base_price * random.uniform(0.99, 1.01), 2),
                "high_price": round(base_price * random.uniform(1.00, 1.05), 2),
                "low_price": round(base_price * random.uniform(0.95, 1.00), 2),
                "volume": random.randint(10000, 500000),
                "turnover": round(base_price * random.randint(10000, 500000), 2),
                "total_trades": random.randint(100, 5000),
                "market_cap": round(base_price * random.randint(1000000, 10000000), 2),
                "outstanding_shares": random.randint(1000000, 10000000),
                "eps": round(random.uniform(10, 100), 2),
                "pe_ratio": round(random.uniform(10, 50), 2),
                "book_value": round(random.uniform(100, 300), 2),
                "pb_ratio": round(random.uniform(1, 5), 2),
                "is_active": True,
                "is_tradeable": True
            }
            
            # Calculate change
            stock["change"] = round(stock["ltp"] - stock["previous_close"], 2)
            stock["change_percent"] = round((stock["change"] / stock["previous_close"]) * 100, 2)
            
            stocks.append(stock)
        
        return stocks
    
    @staticmethod
    def generate_ohlcv_data(symbol: str, days: int = 100) -> List[Dict[str, Any]]:
        """Generate mock OHLCV data for a stock"""
        ohlcv_data = []
        base_price = random.uniform(200, 2000)
        
        for i in range(days):
            date = datetime.now() - timedelta(days=days - i)
            
            # Add some trend and volatility
            trend = random.uniform(-0.02, 0.02)
            base_price = base_price * (1 + trend)
            
            open_price = base_price * random.uniform(0.98, 1.02)
            close_price = base_price * random.uniform(0.98, 1.02)
            high_price = max(open_price, close_price) * random.uniform(1.00, 1.03)
            low_price = min(open_price, close_price) * random.uniform(0.97, 1.00)
            volume = random.randint(10000, 500000)
            
            ohlcv = {
                "date": date.date(),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": volume,
                "turnover": round(close_price * volume, 2),
                "total_trades": random.randint(100, 5000)
            }
            
            ohlcv_data.append(ohlcv)
        
        return ohlcv_data
    
    @staticmethod
    def generate_market_depth(symbol: str) -> Dict[str, Any]:
        """Generate mock market depth data"""
        base_price = random.uniform(200, 2000)
        
        buy_orders = []
        sell_orders = []
        
        # Generate 5 buy orders
        for i in range(5):
            price = base_price * (1 - (i + 1) * 0.001)
            buy_orders.append({
                "price": round(price, 2),
                "quantity": random.randint(100, 10000),
                "orders": random.randint(1, 50)
            })
        
        # Generate 5 sell orders
        for i in range(5):
            price = base_price * (1 + (i + 1) * 0.001)
            sell_orders.append({
                "price": round(price, 2),
                "quantity": random.randint(100, 10000),
                "orders": random.randint(1, 50)
            })
        
        return {
            "symbol": symbol,
            "timestamp": datetime.now(),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders
        }
    
    @staticmethod
    def generate_floorsheet(symbol: str, trades: int = 100) -> List[Dict[str, Any]]:
        """Generate mock floorsheet data"""
        floorsheet_data = []
        base_price = random.uniform(200, 2000)
        
        for i in range(trades):
            trade_time = datetime.now() - timedelta(minutes=random.randint(0, 360))
            price = base_price * random.uniform(0.98, 1.02)
            quantity = random.randint(10, 1000)
            
            trade = {
                "symbol": symbol,
                "buyer_broker": random.randint(1, 100),
                "seller_broker": random.randint(1, 100),
                "quantity": quantity,
                "price": round(price, 2),
                "amount": round(price * quantity, 2),
                "trade_time": trade_time
            }
            
            floorsheet_data.append(trade)
        
        return floorsheet_data
    
    @staticmethod
    def generate_sectors() -> List[Dict[str, Any]]:
        """Generate mock sector data"""
        sectors = []
        
        for sector_name in MockDataGenerator.SECTORS:
            base_index = random.uniform(1000, 3000)
            
            sector = {
                "name": sector_name,
                "symbol": sector_name.upper().replace(" ", "_"),
                "index": round(base_index, 2),
                "change": round(random.uniform(-50, 50), 2),
                "change_percent": round(random.uniform(-3, 3), 2),
                "turnover": round(random.uniform(1000000, 10000000), 2),
                "volume": random.randint(100000, 1000000)
            }
            
            sectors.append(sector)
        
        return sectors


# Convenience functions
def generate_mock_stocks() -> List[Dict[str, Any]]:
    """Generate mock stock data"""
    return MockDataGenerator.generate_stock_data()


def generate_mock_ohlcv(symbol: str, days: int = 100) -> List[Dict[str, Any]]:
    """Generate mock OHLCV data"""
    return MockDataGenerator.generate_ohlcv_data(symbol, days)


def generate_mock_depth(symbol: str) -> Dict[str, Any]:
    """Generate mock market depth"""
    return MockDataGenerator.generate_market_depth(symbol)


def generate_mock_floorsheet(symbol: str, trades: int = 100) -> List[Dict[str, Any]]:
    """Generate mock floorsheet data"""
    return MockDataGenerator.generate_floorsheet(symbol, trades)


def generate_mock_sectors() -> List[Dict[str, Any]]:
    """Generate mock sector data"""
    return MockDataGenerator.generate_sectors()
