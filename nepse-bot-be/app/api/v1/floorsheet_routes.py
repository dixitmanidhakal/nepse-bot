"""
Floorsheet API Routes

This module provides REST API endpoints for floorsheet (trade) analysis:
- Recent trades
- Institutional trade detection
- Cross trade detection
- Broker activity analysis
- Top brokers
- Accumulation/Distribution analysis
- Broker sentiment
- Broker rankings
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.database import get_db
from app.components.floorsheet_analyzer import FloorsheetAnalyzer
from app.components.broker_tracker import BrokerTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/floorsheet", tags=["Floorsheet"])


@router.get("/{symbol}/trades")
async def get_recent_trades(
    symbol: str,
    days: int = Query(1, description="Number of days of history", ge=1, le=30),
    limit: int = Query(100, description="Maximum number of trades", ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """
    Get recent trades for a stock
    
    Returns individual trade details from the floorsheet including:
    - Buyer and seller broker information
    - Trade quantity, rate, and amount
    - Trade date and time
    - Institutional and cross-trade flags
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.get_recent_trades(symbol, days, limit)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No trade data found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting recent trades for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/analysis")
async def get_trade_analysis(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive floorsheet analysis
    
    Includes:
    - Recent trades summary
    - Institutional trades
    - Cross trades
    - Top brokers
    - Accumulation/Distribution patterns
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        
        result = {
            "symbol": symbol,
            "period_days": days,
            "recent_trades": analyzer.get_recent_trades(symbol, days, 50),
            "institutional_trades": analyzer.detect_institutional_trades(symbol, days),
            "cross_trades": analyzer.detect_cross_trades(symbol, days),
            "top_brokers": analyzer.get_top_brokers(symbol, days, 10),
            "accumulation_distribution": analyzer.analyze_accumulation_distribution(symbol, days)
        }
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Error analyzing floorsheet for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/institutional")
async def get_institutional_trades(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    quantity_threshold: float = Query(1000, description="Minimum quantity for institutional trade", ge=100),
    db: Session = Depends(get_db)
):
    """
    Detect institutional trades (large trades)
    
    Institutional trades are typically large in size and indicate
    smart money activity. These trades can signal important market moves.
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.detect_institutional_trades(symbol, days, quantity_threshold)
        
        return result
        
    except Exception as e:
        logger.error(f"Error detecting institutional trades for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/cross-trades")
async def get_cross_trades(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    db: Session = Depends(get_db)
):
    """
    Detect cross trades (same broker buy/sell)
    
    Cross trades occur when the same broker is on both sides of a trade.
    This can indicate manipulation or internal transfers.
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.detect_cross_trades(symbol, days)
        
        return result
        
    except Exception as e:
        logger.error(f"Error detecting cross trades for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/brokers")
async def get_broker_activity(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    limit: int = Query(10, description="Number of top brokers", ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Get top active brokers for a stock
    
    Returns brokers ranked by trading volume, showing:
    - Buy and sell activity
    - Net position (accumulating/distributing)
    - Total volume and amount
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.get_top_brokers(symbol, days, limit)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No broker data found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting broker activity for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/broker/{broker_id}")
async def get_specific_broker_activity(
    symbol: str,
    broker_id: str,
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Analyze specific broker's activity for a stock
    
    Shows detailed activity of a particular broker including:
    - Buy and sell trades
    - Net position
    - Average rates
    - Position sentiment (bullish/bearish)
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.analyze_broker_activity(symbol, broker_id, days)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No broker activity found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing broker {broker_id} for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/accumulation")
async def get_accumulation_distribution(
    symbol: str,
    days: int = Query(30, description="Number of days to analyze", ge=7, le=365),
    db: Session = Depends(get_db)
):
    """
    Analyze accumulation/distribution patterns
    
    Identifies whether a stock is being accumulated (bought) or
    distributed (sold) based on trading patterns over time.
    
    Phases:
    - Strong Accumulation: Increasing volume with rising prices
    - Accumulation: Increasing volume
    - Distribution: Decreasing volume
    - Strong Distribution: Decreasing volume with falling prices
    - Consolidation: Stable volume and prices
    """
    try:
        analyzer = FloorsheetAnalyzer(db)
        result = analyzer.analyze_accumulation_distribution(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'Analysis failed')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing accumulation/distribution for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brokers/ranking")
async def get_broker_rankings(
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    limit: int = Query(20, description="Number of top brokers", ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Get broker rankings by activity across all stocks
    
    Returns top brokers ranked by total trading volume.
    """
    try:
        tracker = BrokerTracker(db)
        result = tracker.get_broker_rankings(days, limit)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No broker data found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting broker rankings: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brokers/{broker_id}/track")
async def track_broker(
    broker_id: str,
    symbol: Optional[str] = Query(None, description="Optional stock symbol filter"),
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Track specific broker's activity
    
    Shows broker's trading activity across all stocks or for a specific stock.
    Useful for following institutional brokers or smart money.
    """
    try:
        tracker = BrokerTracker(db)
        result = tracker.track_broker(broker_id, symbol, days)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No broker activity found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error tracking broker {broker_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/broker-sentiment")
async def get_broker_sentiment(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    db: Session = Depends(get_db)
):
    """
    Analyze overall broker sentiment for a stock
    
    Shows percentage of brokers that are bullish (accumulating)
    vs bearish (distributing) on the stock.
    """
    try:
        tracker = BrokerTracker(db)
        result = tracker.analyze_broker_sentiment(symbol, days)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'Analysis failed')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing broker sentiment for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brokers/institutional")
async def get_institutional_brokers(
    days: int = Query(30, description="Number of days to analyze", ge=7, le=365),
    min_trades: int = Query(100, description="Minimum number of trades", ge=10),
    min_volume: float = Query(100000, description="Minimum total volume", ge=1000),
    db: Session = Depends(get_db)
):
    """
    Identify institutional brokers
    
    Identifies brokers that meet institutional criteria:
    - High trade volume
    - Large average trade size
    - Consistent activity
    """
    try:
        tracker = BrokerTracker(db)
        result = tracker.identify_institutional_brokers(days, min_trades, min_volume)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'No institutional brokers found')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error identifying institutional brokers: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/broker-pairs")
async def get_broker_pairs(
    symbol: str,
    days: int = Query(7, description="Number of days to analyze", ge=1, le=90),
    min_trades: int = Query(5, description="Minimum trades between pair", ge=2),
    db: Session = Depends(get_db)
):
    """
    Analyze broker pair trading patterns
    
    Identifies brokers that frequently trade with each other,
    which can indicate coordinated activity or manipulation.
    """
    try:
        tracker = BrokerTracker(db)
        result = tracker.analyze_broker_pairs(symbol, days, min_trades)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', 'Analysis failed')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing broker pairs for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
