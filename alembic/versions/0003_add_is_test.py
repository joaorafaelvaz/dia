"""is_test flag em alerts e forecasts (test harness)

Revision ID: 0003_add_is_test
Revises: 0002_ai_usage
Create Date: 2026-04-25

Adiciona Boolean is_test nas duas tabelas pra suportar o menu de inserção
manual de alertas/forecasts sintéticos. Default False — registros existentes
ficam marcados como reais. Indexed pra os filtros do dashboard e do
context_builder serem eficientes.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_is_test"
down_revision: Union[str, None] = "0002_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "is_test",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_alerts_is_test", "alerts", ["is_test"])

    op.add_column(
        "forecasts",
        sa.Column(
            "is_test",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_forecasts_is_test", "forecasts", ["is_test"])


def downgrade() -> None:
    op.drop_index("ix_forecasts_is_test", table_name="forecasts")
    op.drop_column("forecasts", "is_test")
    op.drop_index("ix_alerts_is_test", table_name="alerts")
    op.drop_column("alerts", "is_test")
