"""Bot implementations — all 8 NEPSE strategy bots."""

from app.components.bots.smc_bot import SMCBot
from app.components.bots.recommendation_bot import RecommendationBot
from app.components.bots.momentum_bot import MomentumBot
from app.components.bots.ema_crossover_bot import EMACrossoverBot
from app.components.bots.mean_reversion_bot import MeanReversionBot
from app.components.bots.sector_rotation_bot import SectorRotationBot
from app.components.bots.volume_breakout_bot import VolumeBreakoutBot
from app.components.bots.quant_composite_bot import QuantCompositeBot

BOT_REGISTRY = {
    "smc":              SMCBot,
    "recommendation":   RecommendationBot,
    "momentum":         MomentumBot,
    "ema_crossover":    EMACrossoverBot,
    "mean_reversion":   MeanReversionBot,
    "sector_rotation":  SectorRotationBot,
    "volume_breakout":  VolumeBreakoutBot,
    "quant_composite":  QuantCompositeBot,
}

__all__ = [
    "SMCBot", "RecommendationBot", "MomentumBot",
    "EMACrossoverBot", "MeanReversionBot",
    "SectorRotationBot", "VolumeBreakoutBot",
    "QuantCompositeBot",
    "BOT_REGISTRY",
]
