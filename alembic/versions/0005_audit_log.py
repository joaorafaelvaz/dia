"""audit_log — registra mutações de Client/Dam/Alert/Test-harness.

Revision ID: 0005_audit_log
Revises: 0004_add_clients
Create Date: 2026-04-25

Mínimo viável de audit: quem fez O QUÊ em qual entidade, quando. Detalhes
opcionais em JSON (ex: campos antes/depois). Indexes em (entity_type,
entity_id) e created_at pra suportar busca por barragem ou janela de tempo.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_audit_log"
down_revision: Union[str, None] = "0004_add_clients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        # "client.create" | "client.update" | "client.delete"
        # "dam.create" | "dam.update" | "dam.delete" | "dam.deactivate"
        # "alert.acknowledge" | "test_harness.alert_create" | etc.
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_user", "audit_log", ["user"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_user", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_table("audit_log")
