from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_explanations import AdminExplanation
from .membership_policy import MEMBERSHIP_ADMISSION_FEE, MEMBERSHIP_SHARE_UNIT_AMOUNT
from .models import AdminAudit, SystemSetting


ADMISSION_FEE_KEY = "membership_admission_fee"
MAX_ADMISSION_FEE_AMOUNT = 100_000_000


class AdmissionFeeConflict(ValueError):
    pass


class AdmissionFeeSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    admission_fee_amount: int = Field(ge=0, le=MAX_ADMISSION_FEE_AMOUNT, strict=True)
    expected_version: int = Field(ge=0, strict=True)
    reason: AdminExplanation = Field(default="", max_length=1000)


class AdmissionFeeSettingsRead(BaseModel):
    admission_fee_amount: int = Field(ge=0, le=MAX_ADMISSION_FEE_AMOUNT, strict=True)
    version: int = Field(ge=0, strict=True)
    share_unit_amount: int = MEMBERSHIP_SHARE_UNIT_AMOUNT
    maximum_admission_fee_amount: int = MAX_ADMISSION_FEE_AMOUNT


def _settings(row: SystemSetting | None) -> AdmissionFeeSettingsRead:
    if row is None:
        return AdmissionFeeSettingsRead(admission_fee_amount=MEMBERSHIP_ADMISSION_FEE, version=0)
    return AdmissionFeeSettingsRead(
        admission_fee_amount=row.value["admission_fee_amount"],
        version=row.value["version"],
    )


async def read_admission_fee_settings(session: AsyncSession) -> AdmissionFeeSettingsRead:
    return _settings(await session.get(SystemSetting, ADMISSION_FEE_KEY, populate_existing=True))


async def update_admission_fee_settings(
    session: AsyncSession, body: AdmissionFeeSettingsUpdate, *, actor_id: str,
) -> AdmissionFeeSettingsRead:
    row = await session.scalar(select(SystemSetting)
        .where(SystemSetting.key == ADMISSION_FEE_KEY)
        .with_for_update()
        .execution_options(populate_existing=True))
    previous = _settings(row)
    conflict = "入社費設定已更新，請重新讀取後再儲存"
    if body.expected_version != previous.version:
        raise AdmissionFeeConflict(conflict)
    if body.admission_fee_amount == previous.admission_fee_amount:
        return previous

    values = {"admission_fee_amount": body.admission_fee_amount, "version": previous.version + 1}
    updated_at = datetime.now(timezone.utc)
    if row is None:
        try:
            async with session.begin_nested():
                session.add(SystemSetting(
                    key=ADMISSION_FEE_KEY, value=values, updated_by_id=actor_id, updated_at=updated_at,
                ))
                await session.flush()
        except IntegrityError:
            if await session.get(SystemSetting, ADMISSION_FEE_KEY) is None:
                raise
            raise AdmissionFeeConflict(conflict) from None
    else:
        row.value = values
        row.updated_at = updated_at
        row.updated_by_id = actor_id

    current = AdmissionFeeSettingsRead(**values)
    session.add(AdminAudit(
        actor_id=actor_id, action="membership.admission_fee_settings_updated",
        aggregate_type="system_setting", aggregate_id=ADMISSION_FEE_KEY, reason=body.reason,
        data={"previous": {"admission_fee_amount": previous.admission_fee_amount, "version": previous.version},
            "current": values},
    ))
    await session.flush()
    return current
