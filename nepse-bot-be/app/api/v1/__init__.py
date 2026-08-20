"""
API Version 1 Package

This package contains version 1 of the API routes.
"""

from fastapi import APIRouter
from app.api.v1 import (
    data_routes,
    indicator_routes,
    sector_routes,
    pattern_routes,
    depth_routes,
    depth_live_routes,
    floorsheet_routes,
    calendar_routes,
    quant_routes,
    quant_advanced_routes,
    recommendation_routes,
    enhanced_recommendation_routes,
    free_sources_routes,
    smc_routes,
    bot_routes,
)

# Create main v1 router
router = APIRouter()

# Include sub-routers
router.include_router(data_routes.router)
router.include_router(indicator_routes.router)
router.include_router(sector_routes.router)
router.include_router(sector_routes.screener_router)
router.include_router(pattern_routes.router)
router.include_router(depth_routes.router)
router.include_router(depth_live_routes.router)
router.include_router(floorsheet_routes.router)
router.include_router(calendar_routes.router)
router.include_router(quant_routes.router)
router.include_router(quant_advanced_routes.router)
router.include_router(recommendation_routes.router)
router.include_router(enhanced_recommendation_routes.router)
router.include_router(free_sources_routes.router)
router.include_router(smc_routes.router)
router.include_router(bot_routes.router)

__all__ = ["router"]
