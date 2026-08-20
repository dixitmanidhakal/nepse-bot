"""add_capital_and_timeframe_columns

Revision ID: e91b5fd23ca8
Revises: da6f06da0867
Create Date: 2026-06-02 12:00:00.000000

Adds the money-management columns that the paper-trading bot system needs:

paper_trades:
  - capital_allocated   FLOAT  (NPR amount placed in this trade)
  - shares_qty          INT    (number of shares bought)
  - pnl_nrs             FLOAT  (P&L in Nepalese Rupees)
  - timeframe           VARCHAR(10) daily/weekly/monthly

bot_learning_states:
  - capital_nrs         FLOAT  (total assigned capital, default 10L)
  - capital_deployed    FLOAT  (NPR currently in open trades)
  - total_pnl_nrs       FLOAT  (cumulative realised P&L)
  - peak_capital_nrs    FLOAT  (high-water mark for drawdown calc)
  - max_drawdown_pct    FLOAT  (worst peak-to-trough drawdown %)

Uses IF NOT EXISTS so it is safe to run on deployments that already applied
these columns via manual ALTER TABLE.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91b5fd23ca8"
down_revision: Union[str, None] = "da6f06da0867"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── paper_trades ──────────────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE paper_trades "
        "ADD COLUMN IF NOT EXISTS capital_allocated FLOAT"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades "
        "ADD COLUMN IF NOT EXISTS shares_qty INTEGER"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades "
        "ADD COLUMN IF NOT EXISTS pnl_nrs FLOAT"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades "
        "ADD COLUMN IF NOT EXISTS timeframe VARCHAR(10) DEFAULT 'daily'"
    ))

    # ── bot_learning_states ───────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states "
        "ADD COLUMN IF NOT EXISTS capital_nrs FLOAT DEFAULT 1000000"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states "
        "ADD COLUMN IF NOT EXISTS capital_deployed FLOAT DEFAULT 0"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states "
        "ADD COLUMN IF NOT EXISTS total_pnl_nrs FLOAT DEFAULT 0"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states "
        "ADD COLUMN IF NOT EXISTS peak_capital_nrs FLOAT DEFAULT 1000000"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states "
        "ADD COLUMN IF NOT EXISTS max_drawdown_pct FLOAT DEFAULT 0"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # ── bot_learning_states ───────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states DROP COLUMN IF EXISTS max_drawdown_pct"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states DROP COLUMN IF EXISTS peak_capital_nrs"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states DROP COLUMN IF EXISTS total_pnl_nrs"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states DROP COLUMN IF EXISTS capital_deployed"
    ))
    conn.execute(sa.text(
        "ALTER TABLE bot_learning_states DROP COLUMN IF EXISTS capital_nrs"
    ))

    # ── paper_trades ──────────────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE paper_trades DROP COLUMN IF EXISTS timeframe"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades DROP COLUMN IF EXISTS pnl_nrs"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades DROP COLUMN IF EXISTS shares_qty"
    ))
    conn.execute(sa.text(
        "ALTER TABLE paper_trades DROP COLUMN IF EXISTS capital_allocated"
    ))
