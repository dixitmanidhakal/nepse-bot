"""
Base API Client Module

This module defines the abstract base class for all API clients.
This architecture allows easy switching between different API providers.

Architecture Benefits:
- Interface segregation: Define what methods an API client must implement
- Easy to add new API providers: Just implement the interface
- Dependency inversion: Code depends on abstraction, not concrete implementation
- Testability: Easy to mock API clients for testing
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import datetime


class BaseAPIClient(ABC):
    """
    Abstract Base Class for API Clients
    
    All API clients (NEPSE, alternative providers) must implement this interface.
    This ensures consistency and makes it easy to switch between providers.
    
    Usage:
        class NepseAPIClient(BaseAPIClient):
            def fetch_market_indices(self):
                # Implementation specific to NEPSE API
                pass
    """
    
    def __init__(self, base_url: str, timeout: int = 30, retry_attempts: int = 3):
        """
        Initialize API client
        
        Args:
            base_url: Base URL for the API
            timeout: Request timeout in seconds
            retry_attempts: Number of retry attempts for failed requests
        """
        self.base_url = base_url
        self.timeout = timeout
        self.retry_attempts = retry_attempts
    
    @abstractmethod
    def fetch_market_indices(self) -> Dict[str, Any]:
        """
        Fetch market indices data
        
        Returns:
            Dict containing market indices data (NEPSE index, sub-indices, etc.)
            
        Example return format:
        {
            "nepse_index": 2100.50,
            "timestamp": "2024-01-15T10:30:00",
            "sub_indices": {
                "banking": 1500.25,
                "hydropower": 2300.75,
                ...
            }
        }
        """
        pass
    
    @abstractmethod
    def fetch_stock_list(self) -> List[Dict[str, Any]]:
        """
        Fetch list of all stocks
        
        Returns:
            List of dictionaries containing stock information
            
        Example return format:
        [
            {
                "symbol": "NABIL",
                "name": "Nabil Bank Limited",
                "sector": "Commercial Banks",
                "ltp": 1200.00,
                ...
            },
            ...
        ]
        """
        pass
    
    @abstractmethod
    def fetch_stock_ohlcv(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV (Open, High, Low, Close, Volume) data for a stock
        
        Args:
            symbol: Stock symbol (e.g., "NABIL")
            start_date: Start date for historical data
            end_date: End date for historical data
            
        Returns:
            List of OHLCV data dictionaries
            
        Example return format:
        [
            {
                "date": "2024-01-15",
                "open": 1200.00,
                "high": 1250.00,
                "low": 1190.00,
                "close": 1230.00,
                "volume": 50000
            },
            ...
        ]
        """
        pass
    
    @abstractmethod
    def fetch_market_depth(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch market depth (order book) for a stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary containing buy and sell orders
            
        Example return format:
        {
            "symbol": "NABIL",
            "timestamp": "2024-01-15T10:30:00",
            "buy_orders": [
                {"price": 1200.00, "quantity": 100},
                {"price": 1199.00, "quantity": 200},
                ...
            ],
            "sell_orders": [
                {"price": 1201.00, "quantity": 150},
                {"price": 1202.00, "quantity": 250},
                ...
            ]
        }
        """
        pass
    
    @abstractmethod
    def fetch_floorsheet(
        self,
        symbol: Optional[str] = None,
        date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch floorsheet (trade details) data
        
        Args:
            symbol: Stock symbol (optional, if None fetch all)
            date: Date for floorsheet (optional, if None fetch today)
            
        Returns:
            List of trade details
            
        Example return format:
        [
            {
                "symbol": "NABIL",
                "buyer_broker": 10,
                "seller_broker": 25,
                "quantity": 100,
                "price": 1200.00,
                "amount": 120000.00,
                "trade_time": "2024-01-15T10:30:00"
            },
            ...
        ]
        """
        pass
    
    @abstractmethod
    def fetch_stock_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch fundamental data for a stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary containing fundamental data
            
        Example return format:
        {
            "symbol": "NABIL",
            "eps": 45.50,
            "pe_ratio": 26.37,
            "book_value": 250.00,
            "pb_ratio": 4.80,
            "roe": 18.20,
            "debt_to_equity": 1.50,
            "dividend_yield": 5.20,
            "market_cap": 50000000000,
            ...
        }
        """
        pass
    
    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if API is accessible
        
        Returns:
            bool: True if API is accessible, False otherwise
        """
        pass
    
    def get_base_url(self) -> str:
        """
        Get base URL of the API
        
        Returns:
            str: Base URL
        """
        return self.base_url
    
    def get_timeout(self) -> int:
        """
        Get request timeout
        
        Returns:
            int: Timeout in seconds
        """
        return self.timeout
    
    def get_retry_attempts(self) -> int:
        """
        Get number of retry attempts
        
        Returns:
            int: Number of retry attempts
        """
        return self.retry_attempts
