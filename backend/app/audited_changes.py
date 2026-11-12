from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit


def audit_changes(
    session: AsyncSession, actor_id: str, aggregate_type: str, aggregate_id: str,
    action: str, before: dict, after: dict,
) -> None:
    fields = sorted(key for key in before if before[key] != after[key])
    if fields:
        session.add(AdminAudit(
            actor_id=actor_id, action=action, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            data={"changed_fields": fields, "before": jsonable_encoder({key: before[key] for key in fields}),
                  "after": jsonable_encoder({key: after[key] for key in fields})},
        ))
