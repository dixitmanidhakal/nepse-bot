"""
Pattern Detection API Routes

This module provides API endpoints for pattern detection and analysis.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.components.pattern_detector import PatternDetector
from app.components.support_resistance import SupportResistanceDetector
from app.components.trend_analyzer import TrendAnalyzer
from app.components.chart_patterns import ChartPatternDetector

router = APIRouter(prefix="/patterns", tags=["Pattern Detection"])


@router.get("/{symbol}/all", summary="Detect all patterns")
async def detect_all_patterns(
    symbol: str,
    days: int = Query(120, description="Number of days to analyze"),
    save_to_db: bool = Query(False, description="Save patterns to database"),
    db: Session = Depends(get_db)
):
    """
    Detect all patterns for a stock including:
    - Support and resistance levels
    - Trend analysis
    - Chart patterns
    
    Returns comprehensive pattern analysis.
    """
    try:
        detector = PatternDetector(db)
        result = detector.detect_all_patterns(symbol, days, save_to_db)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Pattern detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/summary", summary="Get pattern summary")
async def get_pattern_summary(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Get a quick summary of key patterns for a stock.
    
    Returns:
    - Nearest support and resistance
    - Primary trend
    - Active chart patterns
    """
    try:
        detector = PatternDetector(db)
        result = detector.get_pattern_summary(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Failed to get summary'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/support-resistance", summary="Detect support and resistance")
async def detect_support_resistance(
    symbol: str,
    days: int = Query(180, description="Number of days to analyze"),
    min_touches: int = Query(2, description="Minimum touches for valid level"),
    db: Session = Depends(get_db)
):
    """
    Detect support and resistance levels for a stock.
    
    Returns:
    - Support levels (price floors)
    - Resistance levels (price ceilings)
    - Level strength and touches
    """
    try:
        detector = SupportResistanceDetector(db)
        result = detector.detect_all_levels(symbol, days, min_touches)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/support", summary="Detect support levels only")
async def detect_support(
    symbol: str,
    days: int = Query(180, description="Number of days to analyze"),
    min_touches: int = Query(2, description="Minimum touches"),
    max_levels: int = Query(5, description="Maximum levels to return"),
    db: Session = Depends(get_db)
):
    """
    Detect support levels (price floors) for a stock.
    """
    try:
        detector = SupportResistanceDetector(db)
        result = detector.detect_support_levels(symbol, days, min_touches, max_levels)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/resistance", summary="Detect resistance levels only")
async def detect_resistance(
    symbol: str,
    days: int = Query(180, description="Number of days to analyze"),
    min_touches: int = Query(2, description="Minimum touches"),
    max_levels: int = Query(5, description="Maximum levels to return"),
    db: Session = Depends(get_db)
):
    """
    Detect resistance levels (price ceilings) for a stock.
    """
    try:
        detector = SupportResistanceDetector(db)
        result = detector.detect_resistance_levels(symbol, days, min_touches, max_levels)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/trend", summary="Analyze trend")
async def analyze_trend(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Analyze price trend for a stock.
    
    Returns:
    - Primary trend (uptrend/downtrend)
    - Trend strength
    - Short-term and medium-term trends
    """
    try:
        analyzer = TrendAnalyzer(db)
        result = analyzer.analyze_trend(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Analysis failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/trend/channel", summary="Detect trend channel")
async def detect_trend_channel(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect trend channel (parallel support and resistance lines).
    
    Returns:
    - Channel type (ascending/descending/horizontal)
    - Upper and lower channel lines
    - Current position in channel
    """
    try:
        analyzer = TrendAnalyzer(db)
        result = analyzer.detect_trend_channel(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/trend/reversal", summary="Detect trend reversal")
async def detect_trend_reversal(
    symbol: str,
    days: int = Query(60, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect potential trend reversals.
    
    Returns:
    - Reversal detected (yes/no)
    - Reversal type (bullish/bearish)
    - Reversal strength
    """
    try:
        analyzer = TrendAnalyzer(db)
        result = analyzer.detect_trend_reversal(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns", summary="Detect chart patterns")
async def detect_chart_patterns(
    symbol: str,
    days: int = Query(120, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect chart patterns including:
    - Double Top/Bottom
    - Head and Shoulders
    - Triangles (Ascending/Descending/Symmetrical)
    - Flags (Bullish/Bearish)
    - Pennants
    - Wedges
    
    Returns all detected patterns with details.
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_all_patterns(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns/double-top", summary="Detect double top")
async def detect_double_top(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    tolerance: float = Query(0.03, description="Price tolerance (3%)"),
    db: Session = Depends(get_db)
):
    """
    Detect Double Top pattern (bearish reversal).
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_double_top(symbol, days, tolerance)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns/double-bottom", summary="Detect double bottom")
async def detect_double_bottom(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    tolerance: float = Query(0.03, description="Price tolerance (3%)"),
    db: Session = Depends(get_db)
):
    """
    Detect Double Bottom pattern (bullish reversal).
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_double_bottom(symbol, days, tolerance)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns/head-shoulders", summary="Detect head and shoulders")
async def detect_head_shoulders(
    symbol: str,
    days: int = Query(120, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect Head and Shoulders pattern (bearish reversal).
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_head_and_shoulders(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns/triangle", summary="Detect triangle patterns")
async def detect_triangle(
    symbol: str,
    days: int = Query(60, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect Triangle patterns (Ascending/Descending/Symmetrical).
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_triangle(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/chart-patterns/flag", summary="Detect flag patterns")
async def detect_flag(
    symbol: str,
    days: int = Query(40, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect Flag patterns (Bullish/Bearish continuation).
    """
    try:
        detector = ChartPatternDetector(db)
        result = detector.detect_flag(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/breakouts", summary="Detect breakouts")
async def detect_breakouts(
    symbol: str,
    days: int = Query(60, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Detect breakout patterns (price breaking through support/resistance).
    
    Returns:
    - Bullish breakouts (above resistance)
    - Bearish breakdowns (below support)
    - Breakout status and strength
    """
    try:
        detector = PatternDetector(db)
        result = detector.detect_breakouts(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Detection failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{symbol}/signals", summary="Get trading signals")
async def get_trading_signals(
    symbol: str,
    days: int = Query(90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Generate trading signals based on detected patterns.
    
    Returns:
    - Overall signal (strong_buy/buy/neutral/sell/strong_sell)
    - Signal strength
    - Individual signals from patterns, trends, and levels
    """
    try:
        detector = PatternDetector(db)
        result = detector.get_trading_signals(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(status_code=404, detail=result.get('error', 'Signal generation failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
