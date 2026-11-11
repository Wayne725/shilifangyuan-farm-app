from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .admin_explanations import AdminExplanation
from .domain import aware
from .models import AdminAudit


class CatalogVisibilityUpdate(BaseModel):
    is_public: bool = Field(strict=True)
    expected_updated_at: datetime
    reason: AdminExplanation = Field(default='', max_length=1000)

    @field_validator('expected_updated_at')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('資料版本時間必須包含時區')
        return value


def update_catalog_visibility(session, record, body, admin, aggregate_type):
    if aware(record.updated_at) != aware(body.expected_updated_at):
        raise HTTPException(409, '資料已更新，請重新讀取後再操作')
    if record.is_public == body.is_public:
        return
    previous = record.is_public
    record.is_public = body.is_public
    record.updated_at = max(datetime.now(timezone.utc), aware(record.updated_at) + timedelta(microseconds=1))
    session.add(AdminAudit(actor_id=admin.id, action=f'{aggregate_type}.visibility_changed',
        aggregate_type=aggregate_type, aggregate_id=record.id, reason=body.reason,
        data={'previous_is_public': previous, 'is_public': body.is_public}))
