"""
Populate the Stock.sma_20 / rsi_14 / macd / ... indicator-cache columns
for every active stock.

The bot's /stocks/screen/* endpoints filter on these cached columns, so
without this the screener pages will always return 0 rows even though the
OHLCV data is fully loaded.

Run after ingesting OHLCV:
    ./venv/bin/python scripts/populate_indicator_cache.py
"""

from __future__ import annotations

import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.models.stock import Stock
from app.indicators.calculator import IndicatorCalculator


def _nested(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _current(d, *keys):
    """Pull res['indicators'][...][...]['current'] safely."""
    node = _nested(d, *keys)
    if isinstance(node, dict):
        v = node.get("current")
        if v is None:
            return None
        try:
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                return None
            return fv
        except (TypeError, ValueError):
            return None
    return None


def populate_one(stock: Stock, calc: IndicatorCalculator) -> bool:
    res = calc.calculate_all(stock.symbol, days=500)
    if not res or not res.get("success"):
        return False

    ind = res.get("indicators") or {}

    stock.ltp = res.get("current_price", stock.ltp)

    stock.sma_20 = _current(ind, "moving_averages", "sma", "sma_20")
    stock.sma_50 = _current(ind, "moving_averages", "sma", "sma_50")
    stock.sma_200 = _current(ind, "moving_averages", "sma", "sma_200")
    stock.ema_9 = (
        _current(ind, "moving_averages", "ema", "ema_9")
        or _current(ind, "moving_averages", "ema", "ema_10")
    )
    stock.ema_21 = (
        _current(ind, "moving_averages", "ema", "ema_21")
        or _current(ind, "moving_averages", "ema", "ema_20")
    )

    stock.rsi_14 = _current(ind, "momentum", "rsi")
    macd_signals = _nested(ind, "momentum", "macd", "signals")
    if isinstance(macd_signals, dict):
        def _safe(v):
            try:
                fv = float(v)
                if math.isnan(fv) or math.isinf(fv):
                    return None
                return fv
            except (TypeError, ValueError):
                return None
        stock.macd = _safe(macd_signals.get("current_macd"))
        stock.macd_signal = _safe(macd_signals.get("current_signal"))
        stock.macd_histogram = _safe(macd_signals.get("current_histogram"))

    stock.atr_14 = _current(ind, "volatility", "atr")
    bb_vals = _nested(ind, "volatility", "bollinger_bands", "values")
    if isinstance(bb_vals, dict):
        def _safe2(v):
            try:
                fv = float(v)
                if math.isnan(fv) or math.isinf(fv):
                    return None
                return fv
            except (TypeError, ValueError):
                return None
        stock.bollinger_upper = _safe2(bb_vals.get("upper_current"))
        stock.bollinger_middle = _safe2(bb_vals.get("middle_current"))
        stock.bollinger_lower = _safe2(bb_vals.get("lower_current"))

    # Compute avg_volume_10d / 30d directly from the OHLCV series the calculator
    # already has cached on the request. If not cached, derive via a fresh query.
    df = calc.get_ohlcv_data(stock.symbol, days=60)
    if df is not None and len(df) >= 10:
        tail10 = df["volume"].tail(10)
        tail30 = df["volume"].tail(30)
        avg10 = float(tail10.mean()) if len(tail10) else None
        avg30 = float(tail30.mean()) if len(tail30) else None
        if avg10 and avg10 > 0:
            stock.avg_volume_10d = avg10
        if avg30 and avg30 > 0:
            stock.avg_volume_30d = avg30

    stock.passes_volume_filter = (
        stock.volume is not None
        and stock.avg_volume_30d is not None
        and stock.avg_volume_30d > 0
        and (stock.volume / stock.avg_volume_30d) >= 1.2
    )
    stock.passes_volatility_filter = (
        stock.atr_14 is not None and stock.ltp and stock.ltp > 0
        and (stock.atr_14 / stock.ltp) < 0.08
    )
    stock.passes_beta_filter = True
    stock.indicators_updated_at = datetime.utcnow()
    return True


def main():
    db = SessionLocal()
    calc = IndicatorCalculator(db)

    stocks = db.query(Stock).filter(Stock.is_active.is_(True)).all()
    print(f"Populating indicator cache for {len(stocks)} stocks...")

    started = time.time()
    ok = 0
    skipped = 0
    for i, s in enumerate(stocks, 1):
        try:
            if populate_one(s, calc):
                ok += 1
            else:
                skipped += 1
        except Exception as exc:
            skipped += 1
            print(f"  [skip] {s.symbol}: {exc}")

        if i % 25 == 0:
            db.commit()
            print(f"  {i}/{len(stocks)}  ok={ok} skipped={skipped} "
                  f"({time.time() - started:.1f}s)")

    db.commit()

    # Quick sanity stats
    from app.models.stock import Stock as S
    with_sma = db.query(S).filter(S.sma_20.isnot(None)).count()
    with_rsi = db.query(S).filter(S.rsi_14.isnot(None)).count()
    print(f"Post-run: stocks with sma_20={with_sma}, rsi_14={with_rsi}")
    db.close()
    print(f"Done. ok={ok} skipped={skipped} in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
