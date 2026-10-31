from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReconciliationPhaseRead(BaseModel):
    status: Literal["running", "succeeded", "failed", "cancelled"]
    duration_ms: int | None = Field(default=None, ge=0)
    error_type: str | None = None
    error_code: str | None = None


class ReconciliationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: Literal["job", "internal", "request", "periodic"]
    status: Literal["running", "succeeded", "partial_failure", "cancelled"]
    started_at: datetime
    progress_at: datetime
    finished_at: datetime | None
    current_phase: str | None
    deferred_reason: str | None
    phases: dict[str, ReconciliationPhaseRead]
    counts: dict[str, int]

    @field_validator("started_at", "progress_at", "finished_at", mode="after")
    @classmethod
    def utc_timestamp(cls, value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ReconciliationBacklogRead(BaseModel):
    kind: str
    count: int = Field(ge=0)
    oldest_at: datetime | None

    @field_validator("oldest_at", mode="after")
    @classmethod
    def utc_timestamp(cls, value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ReconciliationRetentionRead(BaseModel):
    enabled: bool
    retention_days: int
    batch_size: int
    max_batches: int
    interval_seconds: int
    status: Literal["never_run", "running", "succeeded", "failed"]
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_success_at: datetime | None
    deleted_count: int = Field(ge=0)
    error_type: str | None
    oldest_eligible_at: datetime | None

    @field_validator("last_started_at", "last_finished_at", "last_success_at", "oldest_eligible_at", mode="after")
    @classmethod
    def utc_timestamp(cls, value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ReconciliationStatusRead(BaseModel):
    current: ReconciliationRunRead | None
    last_success: ReconciliationRunRead | None
    long_running: bool
    stale_after_seconds: int
    backlog: list[ReconciliationBacklogRead]
    cleanup: ReconciliationRetentionRead
