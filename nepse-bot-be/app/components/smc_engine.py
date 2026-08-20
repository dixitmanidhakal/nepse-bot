"""
SMC (Smart Money Concepts) Engine
==================================

Implements the core Smart Money Concepts methodology for NEPSE:

Concepts implemented:
    1. Swing Highs / Swing Lows  — significant pivot points in price action
    2. Break of Structure (BOS)  — price closes beyond a swing high/low,
                                   confirming trend continuation
    3. Change of Character (ChoCH) — first BOS in the *opposite* direction,
                                     signalling a potential trend reversal
    4. Order Blocks (OB)         — last down-candle before a bullish BOS;
                                   last up-candle before a bearish BOS.
                                   These zones act as institutional demand/supply.
    5. Fair Value Gaps (FVG)     — 3-candle imbalance; bullish: gap between
                                   candle[i].high and candle[i+2].low;
                                   bearish: gap between candle[i].low and
                                   candle[i+2].high. Price tends to fill these.
    6. Liquidity Sweeps          — wick above swing high then close back below
                                   (bearish stop-hunt) or wick below swing low
                                   then close back above (bullish stop-hunt).
    7. Premium / Discount Zones  — relative position of current price within
                                   the most recent swing range (HTF equilibrium).
                                   Discount < 50%, Premium > 50%.

SMC Signal logic
----------------
    BUY  : current price in discount zone (< 42%)
             AND bullish OB not yet invalidated
             AND recent bullish FVG still open
             AND last BOS/ChoCH is bullish
             AND no recent bearish liquidity sweep
    SELL : mirror of above
    WATCH: mixed or insufficient signals

All functions are pure (no DB / no I/O) and work on a list-of-dicts OHLCV
input so they can be called directly from the route without any ORM.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── tunables ─────────────────────────────────────────────────────────────────

SWING_LEFT = 3          # bars left of a swing high/low pivot
SWING_RIGHT = 3         # bars right of a swing high/low pivot (lag)
MIN_BARS = 30           # minimum bars required for any analysis
FVG_MIN_GAP_PCT = 0.001 # minimum gap size as fraction of close price (0.1%)
OB_LOOKBACK = 60        # how far back to search for order blocks
SWEEP_WICK_FACTOR = 0.3 # wick must be at least 30% of candle range to count


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" | "low"
    date: Optional[str] = None


@dataclass
class BOS:
    index: int           # bar where BOS confirmed
    direction: str       # "bullish" | "bearish"
    broken_level: float  # price level that was broken
    close: float
    is_choch: bool = False
    date: Optional[str] = None


@dataclass
class OrderBlock:
    index: int
    ob_high: float
    ob_low: float
    kind: str            # "bullish" | "bearish"
    active: bool = True  # False when price closes through the OB
    date: Optional[str] = None

    @property
    def ob_mid(self) -> float:
        return (self.ob_high + self.ob_low) / 2.0


@dataclass
class FVG:
    index: int           # middle candle index
    fvg_high: float
    fvg_low: float
    kind: str            # "bullish" | "bearish"
    filled: bool = False
    date: Optional[str] = None

    @property
    def size_pct(self) -> float:
        mid = (self.fvg_high + self.fvg_low) / 2.0
        return abs(self.fvg_high - self.fvg_low) / mid * 100.0 if mid else 0.0


@dataclass
class LiquiditySweep:
    index: int
    swept_level: float
    direction: str   # "bullish" (swept lows = stop hunt → expect up) | "bearish"
    date: Optional[str] = None


@dataclass
class SMCResult:
    symbol: str
    signal: str                          # "BUY" | "SELL" | "WATCH"
    score: float                         # 0..100
    confidence: str                      # "HIGH" | "MEDIUM" | "LOW"
    last_close: float
    as_of_date: Optional[str]

    # Context
    trend: str                           # "bullish" | "bearish" | "sideways"
    zone: str                            # "premium" | "discount" | "equilibrium"
    zone_pct: float                      # 0..100 position within swing range

    # Detected structures
    swing_highs: List[Dict[str, Any]] = field(default_factory=list)
    swing_lows: List[Dict[str, Any]] = field(default_factory=list)
    bos_events: List[Dict[str, Any]] = field(default_factory=list)
    order_blocks: List[Dict[str, Any]] = field(default_factory=list)
    fvg_zones: List[Dict[str, Any]] = field(default_factory=list)
    liquidity_sweeps: List[Dict[str, Any]] = field(default_factory=list)

    # Human-readable rationale
    rationale: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("score", "last_close", "zone_pct"):
            if isinstance(d.get(k), float):
                d[k] = round(d[k], 4)
        return d


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _extract_arrays(bars: List[Dict[str, Any]]) -> Optional[Tuple]:
    """Extract open/high/low/close/volume + dates from a list of bar dicts."""
    try:
        opens  = [_safe(b.get("open")  or b.get("o")) for b in bars]
        highs  = [_safe(b.get("high")  or b.get("h")) for b in bars]
        lows   = [_safe(b.get("low")   or b.get("l")) for b in bars]
        closes = [_safe(b.get("close") or b.get("c")) for b in bars]
        vols   = [_safe(b.get("volume") or b.get("v")) for b in bars]
        dates  = [str(b.get("date") or b.get("t") or "") for b in bars]
        return opens, highs, lows, closes, vols, dates
    except Exception as exc:
        logger.debug("SMC _extract_arrays error: %s", exc)
        return None


# ── 1. Swing High / Low detection ────────────────────────────────────────────

def detect_swings(
    highs: List[float],
    lows: List[float],
    dates: List[str],
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """
    Detect swing highs and swing lows using a pivot-point algorithm.
    A swing high at index i requires:
        all(highs[i-left:i] < highs[i]) and all(highs[i+1:i+right+1] < highs[i])
    """
    n = len(highs)
    swing_highs: List[SwingPoint] = []
    swing_lows:  List[SwingPoint] = []

    for i in range(left, n - right):
        # Swing High
        if all(highs[j] < highs[i] for j in range(i - left, i)) and \
           all(highs[j] < highs[i] for j in range(i + 1, i + right + 1)):
            swing_highs.append(SwingPoint(i, highs[i], "high", dates[i]))

        # Swing Low
        if all(lows[j] > lows[i] for j in range(i - left, i)) and \
           all(lows[j] > lows[i] for j in range(i + 1, i + right + 1)):
            swing_lows.append(SwingPoint(i, lows[i], "low", dates[i]))

    return swing_highs, swing_lows


# ── 2. Break of Structure (BOS) + Change of Character (ChoCH) ────────────────

def detect_bos(
    closes: List[float],
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    dates: List[str],
) -> List[BOS]:
    """
    For each close, check if it breaks a prior swing high (bullish BOS)
    or swing low (bearish BOS).

    The first opposite-direction BOS after a series of same-direction BOS
    events is marked as ChoCH (Change of Character).
    """
    n = len(closes)
    bos_list: List[BOS] = []
    last_direction: Optional[str] = None

    for i in range(1, n):
        c = closes[i]

        # Check for bullish BOS: close above any prior swing high not yet broken
        for sh in swing_highs:
            if sh.index < i and c > sh.price:
                is_choch = last_direction == "bearish"
                b = BOS(
                    index=i,
                    direction="bullish",
                    broken_level=sh.price,
                    close=c,
                    is_choch=is_choch,
                    date=dates[i],
                )
                bos_list.append(b)
                last_direction = "bullish"
                break  # one BOS per candle

        # Check for bearish BOS: close below any prior swing low not yet broken
        else:
            for sl in swing_lows:
                if sl.index < i and c < sl.price:
                    is_choch = last_direction == "bullish"
                    b = BOS(
                        index=i,
                        direction="bearish",
                        broken_level=sl.price,
                        close=c,
                        is_choch=is_choch,
                        date=dates[i],
                    )
                    bos_list.append(b)
                    last_direction = "bearish"
                    break

    # Deduplicate: for each (direction, broken_level) keep the FIRST occurrence
    # (oldest bar index) because ChoCH is only set on the first direction change.
    # Later candles retesting the same broken level would not be ChoCH.
    seen: set = set()
    deduped: List[BOS] = []
    for b in bos_list:
        key = (b.direction, round(b.broken_level, 2))
        if key not in seen:
            seen.add(key)
            deduped.append(b)
    return deduped


# ── 3. Order Blocks ───────────────────────────────────────────────────────────

def detect_order_blocks(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    bos_list: List[BOS],
    dates: List[str],
    lookback: int = OB_LOOKBACK,
) -> List[OrderBlock]:
    """
    For each BOS event, find the last candle of the opposite colour just before
    the BOS started.

    Bullish BOS → last down-candle (close < open) before the BOS bar.
    Bearish BOS → last up-candle (close > open) before the BOS bar.

    Marks the OB as inactive when a later close pierces below ob_low (bullish)
    or above ob_high (bearish).
    """
    obs: List[OrderBlock] = []
    n = len(closes)

    for bos in bos_list[-10:]:  # analyse the last 10 BOS events
        start = max(0, bos.index - lookback)
        found_ob: Optional[OrderBlock] = None

        if bos.direction == "bullish":
            # scan back from the bar before BOS to find the last bearish candle
            for j in range(bos.index - 1, start - 1, -1):
                if closes[j] < opens[j]:  # bearish candle
                    found_ob = OrderBlock(j, highs[j], lows[j], "bullish", True, dates[j])
                    break
        else:
            # bearish BOS → last bullish candle
            for j in range(bos.index - 1, start - 1, -1):
                if closes[j] > opens[j]:  # bullish candle
                    found_ob = OrderBlock(j, highs[j], lows[j], "bearish", True, dates[j])
                    break

        if found_ob is None:
            continue

        # Mark invalidated if later price closed through it
        for k in range(found_ob.index + 1, n):
            if found_ob.kind == "bullish" and closes[k] < found_ob.ob_low * 0.995:
                found_ob.active = False
                break
            if found_ob.kind == "bearish" and closes[k] > found_ob.ob_high * 1.005:
                found_ob.active = False
                break

        obs.append(found_ob)

    # Deduplicate by index
    seen_idx: set = set()
    result: List[OrderBlock] = []
    for ob in obs:
        if ob.index not in seen_idx:
            seen_idx.add(ob.index)
            result.append(ob)
    return result


# ── 4. Fair Value Gaps (FVG) ──────────────────────────────────────────────────

def detect_fvg(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    dates: List[str],
    min_gap_pct: float = FVG_MIN_GAP_PCT,
) -> List[FVG]:
    """
    A Fair Value Gap is formed when there is a gap between:
        Bullish FVG: candle[i].high < candle[i+2].low  (price jumped up, gap left)
        Bearish FVG: candle[i].low  > candle[i+2].high (price fell, gap left)

    We track the last 30 bars only and mark FVGs as filled when price revisits.
    """
    n = len(highs)
    fvgs: List[FVG] = []
    start = max(0, n - 60)

    for i in range(start, n - 2):
        ref_close = closes[i + 1]

        # Bullish FVG: gap between bar[i].high and bar[i+2].low
        gap_low  = highs[i]
        gap_high = lows[i + 2]
        if gap_high > gap_low and ref_close > 0:
            gap_pct = (gap_high - gap_low) / ref_close
            if gap_pct >= min_gap_pct:
                fvgs.append(FVG(i + 1, gap_high, gap_low, "bullish", False, dates[i + 1]))

        # Bearish FVG: gap between bar[i].low and bar[i+2].high
        gap_high2 = lows[i]
        gap_low2  = highs[i + 2]
        if gap_high2 > gap_low2 and ref_close > 0:
            gap_pct = (gap_high2 - gap_low2) / ref_close
            if gap_pct >= min_gap_pct:
                fvgs.append(FVG(i + 1, gap_high2, gap_low2, "bearish", False, dates[i + 1]))

    # Mark filled: a FVG is filled when price trades back into the gap
    for fvg in fvgs:
        for k in range(fvg.index + 2, n):
            if fvg.kind == "bullish" and lows[k] <= fvg.fvg_high:
                fvg.filled = True
                break
            if fvg.kind == "bearish" and highs[k] >= fvg.fvg_low:
                fvg.filled = True
                break

    return fvgs


# ── 5. Liquidity Sweeps ───────────────────────────────────────────────────────

def detect_liquidity_sweeps(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    dates: List[str],
    wick_factor: float = SWEEP_WICK_FACTOR,
) -> List[LiquiditySweep]:
    """
    A liquidity sweep occurs when price wicks beyond a swing point but then
    closes back inside — indicating a stop-hunt by smart money.

    Bullish sweep: wick below swing low, closes above → expect up move.
    Bearish sweep: wick above swing high, closes below → expect down move.
    """
    n = len(closes)
    sweeps: List[LiquiditySweep] = []
    start = max(0, n - 40)

    for i in range(start, n):
        candle_range = highs[i] - lows[i]
        if candle_range <= 0:
            continue

        # Bullish sweep: wick below a prior swing low, close above it
        for sl in swing_lows:
            if sl.index < i and lows[i] < sl.price and closes[i] > sl.price:
                wick = sl.price - lows[i]
                if wick / candle_range >= wick_factor:
                    sweeps.append(LiquiditySweep(i, sl.price, "bullish", dates[i]))
                    break

        # Bearish sweep: wick above a prior swing high, close below it
        for sh in swing_highs:
            if sh.index < i and highs[i] > sh.price and closes[i] < sh.price:
                wick = highs[i] - sh.price
                if wick / candle_range >= wick_factor:
                    sweeps.append(LiquiditySweep(i, sh.price, "bearish", dates[i]))
                    break

    # Deduplicate
    seen: set = set()
    result: List[LiquiditySweep] = []
    for s in sweeps:
        if s.index not in seen:
            seen.add(s.index)
            result.append(s)
    return result


# ── 6. Premium / Discount zones ───────────────────────────────────────────────

def compute_zone(
    closes: List[float],
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> Tuple[str, float]:
    """
    Compare current price to the most recent significant swing range.
    Returns (zone_label, zone_pct):
        zone_pct = 0 → at swing low; 100 → at swing high
        discount    : zone_pct < 42
        equilibrium : 42 ≤ zone_pct ≤ 58
        premium     : zone_pct > 58
    """
    if not swing_highs or not swing_lows or not closes:
        return "unknown", 50.0

    # Use the most recent swing high and swing low
    recent_high = max(swing_highs, key=lambda s: s.index)
    recent_low  = min(swing_lows,  key=lambda s: s.index)
    rng = recent_high.price - recent_low.price
    if rng <= 0:
        return "equilibrium", 50.0

    current = closes[-1]
    pct = (current - recent_low.price) / rng * 100.0
    pct = max(0.0, min(100.0, pct))

    if pct < 42:
        zone = "discount"
    elif pct > 58:
        zone = "premium"
    else:
        zone = "equilibrium"

    return zone, round(pct, 2)


# ── 7. Trend determination ────────────────────────────────────────────────────

def determine_trend(bos_list: List[BOS]) -> str:
    """
    Look at the last 3 BOS events.
    Majority bullish → bullish trend.
    Majority bearish → bearish trend.
    Mixed → sideways.
    """
    if not bos_list:
        return "sideways"
    recent = bos_list[-3:]
    bull = sum(1 for b in recent if b.direction == "bullish")
    bear = sum(1 for b in recent if b.direction == "bearish")
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "sideways"


# ── 8. Signal scoring ─────────────────────────────────────────────────────────

def _compute_signal(
    zone: str,
    trend: str,
    obs: List[OrderBlock],
    fvgs: List[FVG],
    sweeps: List[LiquiditySweep],
    bos_list: List[BOS],
) -> Tuple[str, float, str, List[str]]:
    """
    Combine all SMC signals into a single BUY / SELL / WATCH decision.
    Returns (signal, score_0_100, confidence, rationale_list).
    """
    score = 50.0
    rationale: List[str] = []

    # ── Zone contribution (±20) ──────────────────────────────────────────────
    if zone == "discount":
        score += 20
        rationale.append("Price in discount zone (below 42% of range) — favourable for BUY")
    elif zone == "premium":
        score -= 20
        rationale.append("Price in premium zone (above 58% of range) — favourable for SELL")
    else:
        rationale.append("Price at equilibrium (42–58% of range)")

    # ── Trend BOS (±15) ─────────────────────────────────────────────────────
    if trend == "bullish":
        score += 15
        rationale.append("Bullish Break of Structure trend — higher highs forming")
    elif trend == "bearish":
        score -= 15
        rationale.append("Bearish Break of Structure trend — lower lows forming")

    # ── ChoCH (±12) — strongest reversal signal ──────────────────────────────
    choch_events = [b for b in bos_list if b.is_choch]
    if choch_events:
        last_choch = choch_events[-1]
        if last_choch.direction == "bullish":
            score += 12
            rationale.append(f"Bullish ChoCH detected on {last_choch.date} — trend reversal up")
        else:
            score -= 12
            rationale.append(f"Bearish ChoCH detected on {last_choch.date} — trend reversal down")

    # ── Active Order Block (±10) ──────────────────────────────────────────────
    active_bull_obs = [o for o in obs if o.kind == "bullish" and o.active]
    active_bear_obs = [o for o in obs if o.kind == "bearish" and o.active]
    if active_bull_obs:
        score += 10
        ob = active_bull_obs[-1]
        rationale.append(
            f"Active bullish Order Block: {ob.ob_low:.2f}–{ob.ob_high:.2f} (demand zone)"
        )
    if active_bear_obs:
        score -= 10
        ob = active_bear_obs[-1]
        rationale.append(
            f"Active bearish Order Block: {ob.ob_low:.2f}–{ob.ob_high:.2f} (supply zone)"
        )

    # ── Open FVG (±8) ─────────────────────────────────────────────────────────
    open_bull_fvgs = [f for f in fvgs if f.kind == "bullish" and not f.filled]
    open_bear_fvgs = [f for f in fvgs if f.kind == "bearish" and not f.filled]
    if open_bull_fvgs:
        score += 8
        rationale.append(f"Open bullish FVG ({len(open_bull_fvgs)}) — unmitigated imbalance below")
    if open_bear_fvgs:
        score -= 8
        rationale.append(f"Open bearish FVG ({len(open_bear_fvgs)}) — unmitigated imbalance above")

    # ── Liquidity sweeps (±7) ─────────────────────────────────────────────────
    recent_sweeps = sweeps[-3:] if sweeps else []
    bull_sweeps = [s for s in recent_sweeps if s.direction == "bullish"]
    bear_sweeps = [s for s in recent_sweeps if s.direction == "bearish"]
    if bull_sweeps:
        score += 7
        rationale.append("Recent bullish liquidity sweep (stop hunt below lows) — expect up")
    if bear_sweeps:
        score -= 7
        rationale.append("Recent bearish liquidity sweep (stop hunt above highs) — expect down")

    # ── Clamp and determine action ────────────────────────────────────────────
    score = float(max(0.0, min(100.0, score)))

    if score >= 68:
        signal = "BUY"
        confidence = "HIGH" if score >= 80 else "MEDIUM"
    elif score <= 32:
        signal = "SELL"
        confidence = "HIGH" if score <= 20 else "MEDIUM"
    else:
        signal = "WATCH"
        confidence = "LOW"

    return signal, round(score, 2), confidence, rationale


# ── Main entry point ──────────────────────────────────────────────────────────

def analyse(symbol: str, bars: List[Dict[str, Any]]) -> Optional[SMCResult]:
    """
    Run full SMC analysis on a list of OHLCV bar dicts (oldest → newest).

    The bar dicts must contain keys: open/high/low/close (or o/h/l/c) and
    optionally date/t.

    Returns None when there is insufficient data.
    """
    if len(bars) < MIN_BARS:
        logger.debug("SMC: %s has only %d bars (min %d)", symbol, len(bars), MIN_BARS)
        return None

    extracted = _extract_arrays(bars)
    if extracted is None:
        return None
    opens, highs, lows, closes, vols, dates = extracted

    # 1. Swings
    swing_highs, swing_lows = detect_swings(highs, lows, dates)

    # 2. BOS + ChoCH
    bos_list = detect_bos(closes, swing_highs, swing_lows, dates)

    # 3. Order Blocks
    obs = detect_order_blocks(opens, highs, lows, closes, bos_list, dates)

    # 4. FVG
    fvgs = detect_fvg(highs, lows, closes, dates)

    # 5. Liquidity sweeps
    sweeps = detect_liquidity_sweeps(highs, lows, closes, swing_highs, swing_lows, dates)

    # 6. Zone
    zone, zone_pct = compute_zone(closes, swing_highs, swing_lows)

    # 7. Trend
    trend = determine_trend(bos_list)

    # 8. Signal
    signal, score, confidence, rationale = _compute_signal(
        zone, trend, obs, fvgs, sweeps, bos_list
    )

    # Build compact representations for the API response
    def _sh(s: SwingPoint) -> Dict:
        return {"index": s.index, "price": round(s.price, 2), "date": s.date}

    def _bos(b: BOS) -> Dict:
        return {
            "index": b.index,
            "direction": b.direction,
            "broken_level": round(b.broken_level, 2),
            "close": round(b.close, 2),
            "is_choch": b.is_choch,
            "date": b.date,
        }

    def _ob(o: OrderBlock) -> Dict:
        return {
            "index": o.index,
            "ob_high": round(o.ob_high, 2),
            "ob_low": round(o.ob_low, 2),
            "ob_mid": round(o.ob_mid, 2),
            "kind": o.kind,
            "active": o.active,
            "date": o.date,
        }

    def _fvg(f: FVG) -> Dict:
        return {
            "index": f.index,
            "fvg_high": round(f.fvg_high, 2),
            "fvg_low": round(f.fvg_low, 2),
            "kind": f.kind,
            "filled": f.filled,
            "size_pct": round(f.size_pct, 3),
            "date": f.date,
        }

    def _sw(s: LiquiditySweep) -> Dict:
        return {
            "index": s.index,
            "swept_level": round(s.swept_level, 2),
            "direction": s.direction,
            "date": s.date,
        }

    # Return only the last N items to keep the response slim
    return SMCResult(
        symbol=symbol.upper(),
        signal=signal,
        score=score,
        confidence=confidence,
        last_close=round(closes[-1], 2),
        as_of_date=dates[-1] if dates else None,
        trend=trend,
        zone=zone,
        zone_pct=zone_pct,
        swing_highs=[_sh(s) for s in swing_highs[-10:]],
        swing_lows=[_sh(s)  for s in swing_lows[-10:]],
        bos_events=[_bos(b) for b in bos_list[-10:]],
        order_blocks=[_ob(o) for o in obs],
        fvg_zones=[_fvg(f) for f in fvgs[-20:]],
        liquidity_sweeps=[_sw(s) for s in sweeps[-10:]],
        rationale=rationale,
    )
