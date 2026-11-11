from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .. import membership_admission as admission
from ..auth import get_current_user, require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..membership_admission_fee import (
    AdmissionFeeConflict, AdmissionFeeSettingsRead, AdmissionFeeSettingsUpdate,
    read_admission_fee_settings, update_admission_fee_settings,
)
from ..membership_admission_schemas import (
    AdmissionEducationConfirm, AdmissionListRead, AdmissionMeRead, AdmissionQuizSubmit,
    AdmissionReviewInput, AdmissionRoundRead, AdmissionRoundStart, AdmissionRoundUpdate,
    AdmissionVersionInput, AdmissionWithdraw,
)
from ..models import User


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


membership_admission_router = APIRouter(tags=["membership-admission"], dependencies=[Depends(private_response)])


async def _write(session, operation):
    try:
        result = await operation
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


@membership_admission_router.get("/v1/admin/membership/admission-fee", response_model=AdmissionFeeSettingsRead)
async def admission_fee_settings(_admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await read_admission_fee_settings(session)


@membership_admission_router.put("/v1/admin/membership/admission-fee", response_model=AdmissionFeeSettingsRead)
async def change_admission_fee_settings(body: AdmissionFeeSettingsUpdate, admin: User = Depends(require_admin),
                                        session: AsyncSession = Depends(get_session)):
    try:
        return await _write(session, update_admission_fee_settings(session, body, actor_id=admin.id))
    except AdmissionFeeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc


@membership_admission_router.get("/v1/membership/admission", response_model=AdmissionMeRead)
async def admission_me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                       settings: Settings = Depends(get_settings)):
    return await admission.read_me(session, user, settings)


@membership_admission_router.get("/v1/membership/admission/rounds/{round_id}", response_model=AdmissionRoundRead)
async def admission_round(round_id: str, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await admission.read_one(session, round_id, user, settings)


@membership_admission_router.post("/v1/membership/admission/rounds", response_model=AdmissionRoundRead)
async def start_round(body: AdmissionRoundStart, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.start_round(session, user, body, settings))


@membership_admission_router.put("/v1/membership/admission/rounds/{round_id}", response_model=AdmissionRoundRead)
async def update_round(round_id: str, body: AdmissionRoundUpdate, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.update_round(session, round_id, user, body, settings))


@membership_admission_router.post("/v1/membership/admission/rounds/{round_id}/education", response_model=AdmissionRoundRead)
async def confirm_education(round_id: str, body: AdmissionEducationConfirm, user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.confirm_education(session, round_id, user, body, settings))


@membership_admission_router.post("/v1/membership/admission/rounds/{round_id}/quiz-attempts", response_model=AdmissionRoundRead)
async def submit_quiz(round_id: str, body: AdmissionQuizSubmit, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.submit_quiz(session, round_id, user, body, settings))


@membership_admission_router.post("/v1/membership/admission/rounds/{round_id}/submit", response_model=AdmissionRoundRead)
async def submit_round(round_id: str, body: AdmissionVersionInput, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.submit_round(session, round_id, user, body, settings))


@membership_admission_router.post("/v1/membership/admission/rounds/{round_id}/withdraw", response_model=AdmissionRoundRead)
async def withdraw_round(round_id: str, body: AdmissionWithdraw, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.withdraw_round(session, round_id, user, body, settings))


@membership_admission_router.get("/v1/admin/membership/admissions", response_model=AdmissionListRead)
async def list_admissions(
    status: Literal["pending", "all", "draft", "submitted", "needs_supplement", "approved", "rejected", "withdrawn"] = "pending",
    q: str = Query(default="", max_length=120), limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
):
    return await admission.list_rounds(session, settings, status=status, q=q, limit=limit, offset=offset)


@membership_admission_router.post("/v1/admin/membership/admissions/{round_id}/review", response_model=AdmissionRoundRead)
async def review_admission(round_id: str, body: AdmissionReviewInput, admin: User = Depends(require_admin),
                           session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return await _write(session, admission.review_round(session, round_id, admin, body, settings))
