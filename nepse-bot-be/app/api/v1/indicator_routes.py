"""
Indicator API Routes

This module provides REST API endpoints for technical indicators.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import logging

from app.database import get_db
from app.indicators.calculator import IndicatorCalculator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/indicators", tags=["Indicators"])


@router.get("/{symbol}")
async def get_all_indicators(
    symbol: str,
    days: int = Query(default=200, ge=30, le=1000, description="Number of days of historical data"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get all technical indicators for a stock
    
    This endpoint calculates and returns all available technical indicators:
    - Moving Averages (SMA, EMA, WMA)
    - Momentum Indicators (RSI, MACD, Stochastic, ROC, CCI)
    - Volatility Indicators (ATR, Bollinger Bands, Std Dev, Historical Volatility)
    - Volume Indicators (OBV, Volume MA, MFI, A/D Line, CMF)
    
    Args:
        symbol: Stock symbol (e.g., "NABIL", "NICA")
        days: Number of days of historical data (30-1000, default: 200)
        db: Database session (injected)
        
    Returns:
        Dictionary with all calculated indicators
        
    Example:
        GET /api/v1/indicators/NABIL?days=100
    """
    try:
        calculator = IndicatorCalculator(db)
        result = calculator.calculate_all(symbol.upper(), days)
        
        if not result.get('success', False):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', f'Failed to calculate indicators for {symbol}')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating indicators for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{symbol}/summary")
async def get_indicator_summary(
    symbol: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get a summary of key indicators for quick analysis
    
    Returns only the most important indicator values:
    - Current price
    - SMA 20, 50
    - EMA 20
    - RSI 14
    - MACD
    - ATR 14
    - Bollinger Bands
    - Volume analysis
    
    Args:
        symbol: Stock symbol (e.g., "NABIL", "NICA")
        db: Database session (injected)
        
    Returns:
        Dictionary with key indicator values
        
    Example:
        GET /api/v1/indicators/NABIL/summary
    """
    try:
        calculator = IndicatorCalculator(db)
        result = calculator.get_indicator_summary(symbol.upper())
        
        if not result.get('success', False):
            raise HTTPException(
                status_code=404,
                detail=result.get('error', f'Failed to get indicator summary for {symbol}')
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting indicator summary for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{symbol}/moving-averages")
async def get_moving_averages(
    symbol: str,
    days: int = Query(default=200, ge=30, le=1000, description="Number of days of historical data"),
    sma_periods: Optional[str] = Query(default="5,10,20,50,200", description="SMA periods (comma-separated)"),
    ema_periods: Optional[str] = Query(default="5,10,20,50,200", description="EMA periods (comma-separated)"),
    wma_periods: Optional[str] = Query(default="10,20", description="WMA periods (comma-separated)"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get moving average indicators for a stock
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data
        sma_periods: SMA periods (comma-separated, e.g., "5,10,20")
        ema_periods: EMA periods (comma-separated)
        wma_periods: WMA periods (comma-separated)
        db: Database session (injected)
        
    Returns:
        Dictionary with moving average indicators
        
    Example:
        GET /api/v1/indicators/NABIL/moving-averages?days=100&sma_periods=10,20,50
    """
    try:
        # Parse periods
        custom_periods = {
            'sma': [int(p.strip()) for p in sma_periods.split(',') if p.strip()],
            'ema': [int(p.strip()) for p in ema_periods.split(',') if p.strip()],
            'wma': [int(p.strip()) for p in wma_periods.split(',') if p.strip()]
        }
        
        calculator = IndicatorCalculator(db)
        df = calculator.get_ohlcv_data(symbol.upper(), days)
        
        if df is None or len(df) == 0:
            raise HTTPException(
                status_code=404,
                detail=f'No OHLCV data found for {symbol}'
            )
        
        result = calculator.calculate_moving_averages(df, custom_periods)
        
        return {
            'success': True,
            'symbol': symbol.upper(),
            'data_points': len(df),
            'indicators': result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating moving averages for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{symbol}/momentum")
async def get_momentum_indicators(
    symbol: str,
    days: int = Query(default=200, ge=30, le=1000, description="Number of days of historical data"),
    rsi_period: int = Query(default=14, ge=5, le=50, description="RSI period"),
    macd_fast: int = Query(default=12, ge=5, le=50, description="MACD fast period"),
    macd_slow: int = Query(default=26, ge=10, le=100, description="MACD slow period"),
    macd_signal: int = Query(default=9, ge=5, le=50, description="MACD signal period"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get momentum indicators for a stock
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data
        rsi_period: RSI period (default: 14)
        macd_fast: MACD fast EMA period (default: 12)
        macd_slow: MACD slow EMA period (default: 26)
        macd_signal: MACD signal period (default: 9)
        db: Database session (injected)
        
    Returns:
        Dictionary with momentum indicators
        
    Example:
        GET /api/v1/indicators/NABIL/momentum?days=100&rsi_period=14
    """
    try:
        custom_periods = {
            'rsi': rsi_period,
            'macd_fast': macd_fast,
            'macd_slow': macd_slow,
            'macd_signal': macd_signal,
            'stoch_k': 14,
            'stoch_d': 3,
            'roc': 12,
            'cci': 20
        }
        
        calculator = IndicatorCalculator(db)
        df = calculator.get_ohlcv_data(symbol.upper(), days)
        
        if df is None or len(df) == 0:
            raise HTTPException(
                status_code=404,
                detail=f'No OHLCV data found for {symbol}'
            )
        
        result = calculator.calculate_momentum(df, custom_periods)
        
        return {
            'success': True,
            'symbol': symbol.upper(),
            'data_points': len(df),
            'indicators': result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating momentum indicators for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{symbol}/volatility")
async def get_volatility_indicators(
    symbol: str,
    days: int = Query(default=200, ge=30, le=1000, description="Number of days of historical data"),
    atr_period: int = Query(default=14, ge=5, le=50, description="ATR period"),
    bb_period: int = Query(default=20, ge=10, le=100, description="Bollinger Bands period"),
    bb_std: float = Query(default=2.0, ge=1.0, le=3.0, description="Bollinger Bands std dev"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get volatility indicators for a stock
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data
        atr_period: ATR period (default: 14)
        bb_period: Bollinger Bands period (default: 20)
        bb_std: Bollinger Bands standard deviation (default: 2.0)
        db: Database session (injected)
        
    Returns:
        Dictionary with volatility indicators
        
    Example:
        GET /api/v1/indicators/NABIL/volatility?days=100&atr_period=14
    """
    try:
        custom_periods = {
            'atr': atr_period,
            'bb': bb_period,
            'bb_std': bb_std,
            'std_dev': 20,
            'hv': 20,
            'keltner': 20,
            'keltner_atr': 10,
            'keltner_mult': 2.0
        }
        
        calculator = IndicatorCalculator(db)
        df = calculator.get_ohlcv_data(symbol.upper(), days)
        
        if df is None or len(df) == 0:
            raise HTTPException(
                status_code=404,
                detail=f'No OHLCV data found for {symbol}'
            )
        
        result = calculator.calculate_volatility(df, custom_periods)
        
        return {
            'success': True,
            'symbol': symbol.upper(),
            'data_points': len(df),
            'indicators': result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating volatility indicators for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{symbol}/volume")
async def get_volume_indicators(
    symbol: str,
    days: int = Query(default=200, ge=30, le=1000, description="Number of days of historical data"),
    vol_sma_period: int = Query(default=20, ge=5, le=100, description="Volume SMA period"),
    mfi_period: int = Query(default=14, ge=5, le=50, description="MFI period"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get volume indicators for a stock
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data
        vol_sma_period: Volume SMA period (default: 20)
        mfi_period: Money Flow Index period (default: 14)
        db: Database session (injected)
        
    Returns:
        Dictionary with volume indicators
        
    Example:
        GET /api/v1/indicators/NABIL/volume?days=100&mfi_period=14
    """
    try:
        custom_periods = {
            'vol_sma': vol_sma_period,
            'vol_roc': 12,
            'mfi': mfi_period,
            'cmf': 20,
            'vol_ratio': 20
        }
        
        calculator = IndicatorCalculator(db)
        df = calculator.get_ohlcv_data(symbol.upper(), days)
        
        if df is None or len(df) == 0:
            raise HTTPException(
                status_code=404,
                detail=f'No OHLCV data found for {symbol}'
            )
        
        result = calculator.calculate_volume(df, custom_periods)
        
        return {
            'success': True,
            'symbol': symbol.upper(),
            'data_points': len(df),
            'indicators': result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating volume indicators for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
