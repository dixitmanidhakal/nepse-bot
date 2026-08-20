"""
Components Package

This package contains trading strategy components:
- Sector Analyzer: Analyze sector performance and momentum ✅
- Stock Screener: Multi-criteria stock screening ✅
- Beta Calculator: Calculate beta and correlation metrics ✅
- Pattern Detector: Identify chart patterns ✅
- Support/Resistance Detector: Detect key price levels ✅
- Trend Analyzer: Analyze price trends ✅
- Chart Pattern Detector: Detect specific chart patterns ✅
- Market Depth Analyzer: Analyze order book ✅
- Floorsheet Analyzer: Analyze trade details ✅
- Broker Tracker: Track broker activity ✅

All components implemented in Days 5-7.
"""

from app.components.sector_analyzer import SectorAnalyzer, analyze_sectors
from app.components.stock_screener import StockScreener, screen_stocks
from app.components.beta_calculator import BetaCalculator, calculate_beta
from app.components.pattern_detector import PatternDetector, detect_patterns, get_trading_signals
from app.components.support_resistance import SupportResistanceDetector, detect_support_resistance
from app.components.trend_analyzer import TrendAnalyzer, analyze_trend
from app.components.chart_patterns import ChartPatternDetector, detect_chart_patterns
from app.components.market_depth_analyzer import MarketDepthAnalyzer, analyze_market_depth
from app.components.floorsheet_analyzer import FloorsheetAnalyzer, analyze_floorsheet
from app.components.broker_tracker import BrokerTracker, track_broker_activity

__all__ = [
    # Classes
    'SectorAnalyzer',
    'StockScreener',
    'BetaCalculator',
    'PatternDetector',
    'SupportResistanceDetector',
    'TrendAnalyzer',
    'ChartPatternDetector',
    'MarketDepthAnalyzer',
    'FloorsheetAnalyzer',
    'BrokerTracker',
    
    # Convenience functions
    'analyze_sectors',
    'screen_stocks',
    'calculate_beta',
    'detect_patterns',
    'get_trading_signals',
    'detect_support_resistance',
    'analyze_trend',
    'detect_chart_patterns',
    'analyze_market_depth',
    'analyze_floorsheet',
    'track_broker_activity'
]
