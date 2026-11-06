import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.pii_crypto import encrypt_auth_outbox_credential
from ..member_lookup import MEMBER_LOOKUP_EVENT, MEMBER_LOOKUP_MESSAGE, MemberLookupRequest
from ..models import OutboxEvent
from ..rate_limit import REGISTER_RULE, client_key, enforce


member_lookup_router = APIRouter(prefix="/v1/auth", tags=["auth"])


@member_lookup_router.post("/member-lookup", status_code=202)
async def request_member_lookup(
    body: MemberLookupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    recipient_key = hashlib.sha256(str(body.email).encode()).hexdigest()
    enforce(client_key(request, "member-lookup", recipient_key), REGISTER_RULE)
    request_id = str(uuid4())
    session.add(OutboxEvent(
        event_type=MEMBER_LOOKUP_EVENT,
        aggregate_type="member_lookup",
        aggregate_id=request_id,
        payload={"credential_encrypted": encrypt_auth_outbox_credential(
            settings, MEMBER_LOOKUP_EVENT, request_id,
            json.dumps(body.model_dump(mode="json"), ensure_ascii=False),
        )},
    ))
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"message": MEMBER_LOOKUP_MESSAGE}
