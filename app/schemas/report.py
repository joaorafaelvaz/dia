"""Report schemas."""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportBase(BaseModel):
    report_type: Literal["briefing", "client"]
    title: str
    scope: Literal["gerdau", "kinross", "all", "custom"]
    dam_ids: list[int] = Field(default_factory=list)
    period_start: date
    period_end: date


class ReportCreate(ReportBase):
    pass


class ReportGenerateRequest(BaseModel):
    report_type: Literal["briefing", "client"]
    scope: Literal["gerdau", "kinross", "all", "custom"]
    dam_ids: list[int] | None = None
    period_days: int = Field(default=30, ge=1, le=365)
    # Default False: cron de briefing/cliente NUNCA passa True. Manual pode
    # incluir pra exercitar context_builder com dado sintético do test harness.
    include_test: bool = False


class ReportRead(ReportBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    error_message: str | None
    content_html: str
    content_markdown: str
    events_summary: dict[str, Any]
    generated_by: str
    generated_at: datetime
