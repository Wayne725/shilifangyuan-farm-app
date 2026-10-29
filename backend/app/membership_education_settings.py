from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .membership_policy import MembershipRetryPolicy
from .models import AdminAudit, SystemSetting


MEMBERSHIP_EDUCATION_KEY = "membership_education"


class MembershipEducationSettingsConflict(ValueError):
    pass


class MembershipEducationPolicyValues(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    retry_limit: int = Field(ge=0, le=100, strict=True)
    retry_interval_minutes: int = Field(ge=0, le=525_600, strict=True)


class MembershipEducationSettingsUpdate(MembershipEducationPolicyValues):
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)


class MembershipEducationSettingsRead(MembershipEducationPolicyValues):
    configured: bool
    version: int = Field(ge=0, strict=True)
    updated_at: datetime | None = None
    updated_by_id: str | None = None

    def retry_policy(self) -> MembershipRetryPolicy | None:
        if not self.configured:
            return None
        return MembershipRetryPolicy(
            retry_limit=self.retry_limit,
            retry_interval_minutes=self.retry_interval_minutes,
        )


def _read_setting(row: SystemSetting | None) -> MembershipEducationSettingsRead:
    if row is None:
        return MembershipEducationSettingsRead(
            configured=False, version=0, retry_limit=0, retry_interval_minutes=0,
        )
    updated_at = row.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return MembershipEducationSettingsRead(
        **row.value, configured=True,
        updated_at=updated_at, updated_by_id=row.updated_by_id,
    )


async def read_membership_education_settings(
    session: AsyncSession,
) -> MembershipEducationSettingsRead:
    row = await session.scalar(select(SystemSetting).where(
        SystemSetting.key == MEMBERSHIP_EDUCATION_KEY,
    ))
    return _read_setting(row)


async def update_membership_education_settings(
    session: AsyncSession,
    body: MembershipEducationSettingsUpdate,
    *,
    actor_id: str,
) -> MembershipEducationSettingsRead:
    row = await session.scalar(
        select(SystemSetting)
        .where(SystemSetting.key == MEMBERSHIP_EDUCATION_KEY)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    previous = _read_setting(row)
    if body.expected_version != previous.version:
        raise MembershipEducationSettingsConflict("設定已被更新，請重新載入後再儲存")

    values = body.model_dump(exclude={"expected_version", "reason"})
    updated_at = datetime.now(timezone.utc)
    if row is None:
        row = SystemSetting(
            key=MEMBERSHIP_EDUCATION_KEY,
            value={**values, "version": 1},
            updated_by_id=actor_id, updated_at=updated_at,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            # A concurrent first save cannot be locked before the setting exists.
            existing = await session.scalar(select(SystemSetting).where(
                SystemSetting.key == MEMBERSHIP_EDUCATION_KEY,
            ))
            if existing is None:
                raise
            raise MembershipEducationSettingsConflict("設定已被更新，請重新載入後再儲存") from None
    else:
        row.value = {**values, "version": previous.version + 1}
        row.updated_by_id = actor_id
        row.updated_at = updated_at

    current = _read_setting(row)
    session.add(AdminAudit(
        actor_id=actor_id, action="membership.education_settings_updated",
        aggregate_type="system_setting", aggregate_id=MEMBERSHIP_EDUCATION_KEY,
        reason=body.reason,
        data={"previous": previous.model_dump(mode="json"), "current": current.model_dump(mode="json")},
    ))
    await session.flush()
    return current
