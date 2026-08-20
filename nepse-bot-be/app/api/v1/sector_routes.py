"""
Sector Analysis API Routes

This module provides API endpoints for sector analysis and stock screening.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.database import get_db
from app.components.sector_analyzer import SectorAnalyzer
from app.components.stock_screener import StockScreener
from app.components.beta_calculator import BetaCalculator

router = APIRouter(prefix="/sectors", tags=["Sector Analysis"])
screener_router = APIRouter(prefix="/stocks", tags=["Stock Screening"])


# Pydantic models for request/response
class ScreeningCriteria(BaseModel):
    """Stock screening criteria"""
    min_volume_ratio: Optional[float] = Field(None, description="Minimum volume/avg volume ratio")
    max_beta: Optional[float] = Field(None, description="Maximum beta")
    min_beta: Optional[float] = Field(None, description="Minimum beta")
    bullish_sector_only: Optional[bool] = Field(False, description="Only stocks in bullish sectors")
    sector_ids: Optional[List[int]] = Field(None, description="List of sector IDs to filter")
    min_rsi: Optional[float] = Field(None, description="Minimum RSI value")
    max_rsi: Optional[float] = Field(None, description="Maximum RSI value")
    price_above_sma20: Optional[bool] = Field(False, description="Price above SMA20")
    price_above_sma50: Optional[bool] = Field(False, description="Price above SMA50")
    macd_bullish: Optional[bool] = Field(False, description="MACD above signal line")
    min_pe_ratio: Optional[float] = Field(None, description="Minimum P/E ratio")
    max_pe_ratio: Optional[float] = Field(None, description="Maximum P/E ratio")
    min_roe: Optional[float] = Field(None, description="Minimum ROE percentage")
    min_dividend_yield: Optional[float] = Field(None, description="Minimum dividend yield")
    min_price: Optional[float] = Field(None, description="Minimum stock price")
    max_price: Optional[float] = Field(None, description="Maximum stock price")
    limit: Optional[int] = Field(50, description="Maximum number of results")
    
    class Config:
        json_schema_extra = {
            "example": {
                "min_volume_ratio": 1.5,
                "max_beta": 1.5,
                "min_beta": 0.5,
                "bullish_sector_only": True,
                "min_rsi": 30,
                "max_rsi": 70,
                "price_above_sma20": True,
                "limit": 20
            }
        }


# Sector Analysis Endpoints

@router.get("/", summary="Get all sectors")
async def get_all_sectors(
    sort_by: str = Query("momentum_30d", description="Field to sort by"),
    limit: Optional[int] = Query(None, description="Limit number of results"),
    db: Session = Depends(get_db)
):
    """
    Get all sectors with their metrics
    
    Returns list of all sectors sorted by specified metric.
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.analyze_all_sectors()
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'Failed to analyze sectors'))
        
        # Apply limit if specified
        if limit and 'sectors' in result:
            result['sectors'] = result['sectors'][:limit]
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/top-performers", summary="Get top performing sectors")
async def get_top_performers(
    limit: int = Query(5, description="Number of sectors to return"),
    metric: str = Query("momentum_30d", description="Metric to rank by"),
    db: Session = Depends(get_db)
):
    """
    Get top performing sectors
    
    Returns sectors ranked by specified metric (momentum, change_percent, etc.)
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.get_top_sectors(limit, metric)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No sectors found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{sector_id}", summary="Get sector details")
async def get_sector_details(
    sector_id: int,
    db: Session = Depends(get_db)
):
    """
    Get detailed analysis for a specific sector
    
    Returns comprehensive sector metrics including:
    - Performance metrics
    - Momentum indicators
    - Breadth analysis
    - Stock statistics
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.analyze_sector(sector_id)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'Sector not found'))
        
        return result['data']
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{sector_id}/stocks", summary="Get stocks in sector")
async def get_sector_stocks(
    sector_id: int,
    sort_by: str = Query("change_percent", description="Field to sort by"),
    limit: Optional[int] = Query(None, description="Limit number of results"),
    db: Session = Depends(get_db)
):
    """
    Get all stocks in a specific sector
    
    Returns list of stocks with their current metrics, sorted by specified field.
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.get_sector_stocks(sector_id, sort_by, limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'Sector not found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/analysis/complete", summary="Complete sector analysis")
async def get_complete_analysis(db: Session = Depends(get_db)):
    """
    Get complete sector analysis including:
    - All sectors ranked by performance
    - Bullish sectors
    - Sector rotation opportunities
    - Top and worst performers
    """
    try:
        analyzer = SectorAnalyzer(db)
        
        # Get all sectors analysis
        all_sectors = analyzer.analyze_all_sectors()
        
        # Get bullish sectors
        bullish = analyzer.get_bullish_sectors()
        
        # Get sector rotation
        rotation = analyzer.calculate_sector_rotation()
        
        return {
            'success': True,
            'all_sectors': all_sectors,
            'bullish_sectors': bullish,
            'sector_rotation': rotation
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/analysis/rotation", summary="Sector rotation analysis")
async def get_sector_rotation(db: Session = Depends(get_db)):
    """
    Identify sector rotation opportunities
    
    Returns sectors gaining momentum vs losing momentum,
    useful for sector rotation strategies.
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.calculate_sector_rotation()
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'Failed to calculate rotation'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/analysis/bullish", summary="Get bullish sectors")
async def get_bullish_sectors(
    min_momentum: float = Query(5.0, description="Minimum 30-day momentum"),
    db: Session = Depends(get_db)
):
    """
    Get sectors with bullish momentum
    
    Returns sectors with positive momentum above threshold.
    """
    try:
        analyzer = SectorAnalyzer(db)
        result = analyzer.get_bullish_sectors(min_momentum)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No bullish sectors found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# Stock Screening Endpoints

@screener_router.post("/screen", summary="Screen stocks with custom criteria")
async def screen_stocks(
    criteria: ScreeningCriteria,
    db: Session = Depends(get_db)
):
    """
    Screen stocks based on custom criteria
    
    Supports multiple filters including:
    - Volume and liquidity
    - Beta (volatility)
    - Sector
    - Technical indicators (RSI, MACD, Moving Averages)
    - Fundamental metrics (P/E, ROE, Dividend Yield)
    
    Returns scored and ranked stocks matching criteria.
    """
    try:
        screener = StockScreener(db)
        result = screener.screen_stocks(criteria.dict(exclude_none=True))
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result.get('error', 'Screening failed'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/high-volume", summary="Get high volume stocks")
async def get_high_volume_stocks(
    min_volume_ratio: float = Query(2.0, description="Minimum volume/avg ratio"),
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get stocks with high trading volume (liquidity hunters)
    
    Returns stocks with volume significantly above average.
    """
    try:
        screener = StockScreener(db)
        result = screener.get_high_volume_stocks(min_volume_ratio, limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/momentum", summary="Get momentum stocks")
async def get_momentum_stocks(
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get stocks with strong momentum
    
    Criteria:
    - Price above SMA20 and SMA50
    - RSI between 50-70
    - MACD bullish
    - Above average volume
    """
    try:
        screener = StockScreener(db)
        result = screener.get_momentum_stocks(limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/value", summary="Get value stocks")
async def get_value_stocks(
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get value stocks (fundamental screening)
    
    Criteria:
    - Low P/E ratio (< 20)
    - High ROE (> 15%)
    - Dividend yield > 2%
    """
    try:
        screener = StockScreener(db)
        result = screener.get_value_stocks(limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/defensive", summary="Get defensive stocks")
async def get_defensive_stocks(
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get defensive stocks (low beta)
    
    Criteria:
    - Beta < 0.8
    - Stable dividend yield
    """
    try:
        screener = StockScreener(db)
        result = screener.get_defensive_stocks(limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/growth", summary="Get growth stocks")
async def get_growth_stocks(
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get growth stocks
    
    Criteria:
    - High ROE (> 20%)
    - In bullish sector
    - Strong momentum
    """
    try:
        screener = StockScreener(db)
        result = screener.get_growth_stocks(limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/screen/oversold", summary="Get oversold stocks")
async def get_oversold_stocks(
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get potentially oversold stocks
    
    Criteria:
    - RSI < 30 (oversold)
    - In bullish sector
    - Above average volume
    """
    try:
        screener = StockScreener(db)
        result = screener.get_oversold_stocks(limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# Beta Calculation Endpoints

@screener_router.get("/{symbol}/beta", summary="Calculate stock beta")
async def calculate_stock_beta(
    symbol: str,
    days: int = Query(90, description="Number of days for calculation"),
    db: Session = Depends(get_db)
):
    """
    Calculate beta for a stock
    
    Beta measures systematic risk relative to the market:
    - Beta = 1: Moves with market
    - Beta > 1: More volatile (aggressive)
    - Beta < 1: Less volatile (defensive)
    
    Returns beta, correlation, alpha, and volatility metrics.
    """
    try:
        calculator = BetaCalculator(db)
        result = calculator.calculate_stock_beta(symbol, days)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'Failed to calculate beta'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/beta/high", summary="Get high beta stocks")
async def get_high_beta_stocks(
    min_beta: float = Query(1.2, description="Minimum beta"),
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get stocks with high beta (aggressive stocks)
    
    Returns stocks with beta above threshold, sorted by beta.
    """
    try:
        calculator = BetaCalculator(db)
        result = calculator.get_high_beta_stocks(min_beta, limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@screener_router.get("/beta/low", summary="Get low beta stocks")
async def get_low_beta_stocks(
    max_beta: float = Query(0.8, description="Maximum beta"),
    limit: int = Query(20, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Get stocks with low beta (defensive stocks)
    
    Returns stocks with beta below threshold, sorted by beta.
    """
    try:
        calculator = BetaCalculator(db)
        result = calculator.get_low_beta_stocks(max_beta, limit)
        
        if not result['success']:
            raise HTTPException(status_code=404, detail=result.get('error', 'No stocks found'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
