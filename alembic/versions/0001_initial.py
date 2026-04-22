"""initial schema — dams, climate_events, forecasts, reports, alerts

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("owner_group", sa.String(length=100), nullable=False),
        sa.Column("dam_type", sa.String(length=50), nullable=False),
        sa.Column("municipality", sa.String(length=150), nullable=False),
        sa.Column("state", sa.String(length=3), nullable=False),
        sa.Column("country", sa.String(length=3), nullable=False, server_default="BR"),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("anm_classification", sa.String(length=5), nullable=True),
        sa.Column("cri", sa.String(length=20), nullable=True),
        sa.Column("dpa", sa.String(length=20), nullable=True),
        sa.Column("capacity_m3", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dams_name", "dams", ["name"])
    op.create_index("ix_dams_owner_group", "dams", ["owner_group"])
    op.create_index("ix_dams_is_active", "dams", ["is_active"])

    op.create_table(
        "climate_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dam_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("severity_label", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="weather"),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("precipitation_mm", sa.Float(), nullable=True),
        sa.Column("affected_area_km2", sa.Float(), nullable=True),
        sa.Column("casualties", sa.Integer(), nullable=True),
        sa.Column("evacuated", sa.Integer(), nullable=True),
        sa.Column("economic_damage_brl", sa.Float(), nullable=True),
        sa.Column("ai_analysis", sa.Text(), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dam_id"], ["dams.id"],
            name="fk_climate_events_dam_id_dams", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_climate_events_dam_id", "climate_events", ["dam_id"])
    op.create_index("ix_climate_events_event_type", "climate_events", ["event_type"])
    op.create_index("ix_climate_events_severity", "climate_events", ["severity"])
    op.create_index("ix_climate_events_source_type", "climate_events", ["source_type"])
    op.create_index("ix_climate_events_event_date", "climate_events", ["event_date"])

    op.create_table(
        "forecasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dam_id", sa.Integer(), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="open_meteo"),
        sa.Column("max_precipitation_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_precipitation_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_temperature_c", sa.Float(), nullable=True),
        sa.Column("min_temperature_c", sa.Float(), nullable=True),
        sa.Column("wind_speed_kmh", sa.Float(), nullable=True),
        sa.Column("weather_code", sa.Integer(), nullable=True),
        sa.Column("weather_description", sa.String(length=200), nullable=True),
        sa.Column("risk_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("risk_label", sa.String(length=30), nullable=False, server_default="Baixo"),
        sa.Column("alert_threshold_exceeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ai_assessment", sa.Text(), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dam_id"], ["dams.id"],
            name="fk_forecasts_dam_id_dams", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "dam_id", "forecast_date", "source",
            name="uq_forecasts_dam_date_source",
        ),
    )
    op.create_index("ix_forecasts_dam_id", "forecasts", ["dam_id"])
    op.create_index("ix_forecasts_forecast_date", "forecasts", ["forecast_date"])
    op.create_index("ix_forecasts_risk_level", "forecasts", ["risk_level"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("dam_ids", sa.JSON(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("events_summary", sa.JSON(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="generating"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.String(length=20), nullable=False, server_default="auto"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_reports_report_type", "reports", ["report_type"])
    op.create_index("ix_reports_generated_at", "reports", ["generated_at"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dam_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=100), nullable=True),
        sa.Column("forecast_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_whatsapp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notified_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["dam_id"], ["dams.id"],
            name="fk_alerts_dam_id_dams", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_alerts_dam_id", "alerts", ["dam_id"])
    op.create_index("ix_alerts_alert_type", "alerts", ["alert_type"])
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_is_active", "alerts", ["is_active"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("reports")
    op.drop_table("forecasts")
    op.drop_table("climate_events")
    op.drop_table("dams")
