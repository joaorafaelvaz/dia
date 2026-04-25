"""clients — entidade Client + FK em dams (substitui owner_group string)

Revision ID: 0004_add_clients
Revises: 0003_add_is_test
Create Date: 2026-04-25

Estratégia em 6 passos pra zero perda de dado em prod:
  1. Cria tabela clients vazia
  2. INSERT distinct(owner_group) → vira linha em clients
  3. Adiciona dams.client_id NULLABLE + FK
  4. UPDATE dams SET client_id = clients.id (JOIN por nome)
  5. Promove client_id pra NOT NULL
  6. DROP dams.owner_group

Downgrade reverte na ordem oposta (re-cria owner_group, popula via JOIN, dropa
client_id, dropa clients).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_clients"
down_revision: Union[str, None] = "0003_add_is_test"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabela clients
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        sa.Column("contact_name", sa.String(length=150), nullable=True),
        sa.Column("contact_email", sa.String(length=200), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("cnpj", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_clients_name", "clients", ["name"], unique=True)

    # 2. Backfill: cada owner_group distinto vira um Client
    op.execute(
        """
        INSERT INTO clients (name, is_active, created_at, updated_at)
        SELECT DISTINCT owner_group, TRUE, NOW(), NOW()
        FROM dams
        WHERE owner_group IS NOT NULL
        """
    )

    # 3. Adiciona client_id em dams (NULLABLE temporariamente pro backfill)
    op.add_column(
        "dams",
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
    )
    op.create_index("ix_dams_client_id", "dams", ["client_id"])

    # 4. Backfill via JOIN — Postgres syntax
    op.execute(
        """
        UPDATE dams
        SET client_id = clients.id
        FROM clients
        WHERE clients.name = dams.owner_group
        """
    )

    # 5. Promove pra NOT NULL agora que todos têm valor
    op.alter_column("dams", "client_id", nullable=False)

    # 6. Dropa o índice + coluna owner_group
    op.drop_index("ix_dams_owner_group", table_name="dams")
    op.drop_column("dams", "owner_group")


def downgrade() -> None:
    # Re-introduz owner_group como NULLABLE pra poder popular via JOIN antes
    # de promover.
    op.add_column(
        "dams",
        sa.Column("owner_group", sa.String(length=100), nullable=True),
    )
    op.execute(
        """
        UPDATE dams
        SET owner_group = clients.name
        FROM clients
        WHERE clients.id = dams.client_id
        """
    )
    op.alter_column("dams", "owner_group", nullable=False)
    op.create_index("ix_dams_owner_group", "dams", ["owner_group"])

    op.drop_index("ix_dams_client_id", table_name="dams")
    op.drop_column("dams", "client_id")

    op.drop_index("ix_clients_name", table_name="clients")
    op.drop_table("clients")
