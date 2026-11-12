from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_explanations import AdminExplanation
from .models import AdminAudit, Membership, MembershipStatus, PointAccount, PointSourceType, PointTransaction, User
from .point_accounts import point_account


class MemberPointAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    member_number: str = Field(min_length=1, max_length=100, strict=True)
    amount: int = Field(ge=-2_147_483_648, le=2_147_483_647, strict=True)
    expected_balance: int = Field(ge=-(2**53 - 1), le=2**53 - 1, strict=True)
    reason: AdminExplanation = Field(default="", max_length=240)

    @field_validator("amount")
    @classmethod
    def nonzero_amount(cls, value: int) -> int:
        if value == 0:
            raise ValueError("調整點數不可為 0")
        return value


class MemberPointAdjustmentReceipt(BaseModel):
    id: str
    user_id: str
    member_number: str
    amount: int
    balance_before: int
    balance_after: int


def _transaction_id(request_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, f"shilifangyuan:member-point-adjustment:{request_id}"))


def _reject(code: str, message: str) -> None:
    raise HTTPException(409, message, headers={"X-Error-Code": code, "Cache-Control": "no-store"})


async def _lookup(session: AsyncSession, request_id: UUID):
    result = await session.execute(select(PointTransaction, AdminAudit, PointAccount.user_id)
        .join(PointAccount, PointAccount.id == PointTransaction.account_id)
        .outerjoin(AdminAudit, and_(AdminAudit.aggregate_id == PointTransaction.id,
            AdminAudit.aggregate_type == "point_transaction", AdminAudit.action == "points.adjusted"))
        .where(PointTransaction.id == _transaction_id(request_id)))
    return result.one_or_none()


def _receipt(row, *, user_id: str, actor_id: str, request_id: UUID) -> MemberPointAdjustmentReceipt | None:
    transaction, audit, owner_id = row
    if (owner_id != user_id or transaction.source_type != PointSourceType.ADMIN_ADJUSTMENT
        or transaction.created_by_id != actor_id or transaction.reference_id != transaction.id
        or audit is None or audit.actor_id != actor_id or audit.data.get("request_id") != str(request_id)):
        return None
    return MemberPointAdjustmentReceipt(
        id=transaction.id, user_id=owner_id, member_number=audit.data["member_number"], amount=transaction.amount,
        balance_before=audit.data["balance_before"], balance_after=audit.data["balance_after"],
    )


async def read_member_point_adjustment(
    session: AsyncSession, user_id: str, actor_id: str, request_id: UUID,
) -> MemberPointAdjustmentReceipt:
    row = await _lookup(session, request_id)
    result = _receipt(row, user_id=user_id, actor_id=actor_id, request_id=request_id) if row else None
    if result is None:
        raise HTTPException(404, "尚未找到這筆點數調整", headers={"Cache-Control": "no-store"})
    return result


def _replay(row, user_id: str, actor_id: str, body: MemberPointAdjustment) -> MemberPointAdjustmentReceipt:
    receipt = _receipt(row, user_id=user_id, actor_id=actor_id, request_id=body.request_id)
    if (receipt is None or receipt.member_number != body.member_number or receipt.amount != body.amount
        or receipt.balance_before != body.expected_balance or row[0].note != body.reason):
        _reject("point_request_conflict", "此調整識別碼已用於其他內容")
    return receipt


async def adjust_member_points(
    session: AsyncSession, user_id: str, actor_id: str, body: MemberPointAdjustment,
) -> MemberPointAdjustmentReceipt:
    existing = await _lookup(session, body.request_id)
    if existing is not None:
        return _replay(existing, user_id, actor_id, body)
    user_active = await session.scalar(select(User.is_active).where(User.id == user_id).with_for_update(key_share=True))
    membership = await session.scalar(select(Membership).where(Membership.user_id == user_id)
        .with_for_update(key_share=True).execution_options(populate_existing=True))
    if not user_active or membership is None or membership.status != MembershipStatus.ACTIVE or membership.member_number != body.member_number:
        _reject("point_target_changed", "社員資格或編號已變更，請更新帳冊後重新確認")
    account = await point_account(session, user_id)
    # The stronger lock also serializes ledger inserts that take only the foreign-key lock.
    await session.scalar(select(PointAccount.id).where(PointAccount.id == account.id).with_for_update())
    existing = await _lookup(session, body.request_id)
    if existing is not None:
        return _replay(existing, user_id, actor_id, body)
    balance = int(await session.scalar(select(func.coalesce(func.sum(PointTransaction.amount), 0))
        .where(PointTransaction.account_id == account.id)))
    if balance != body.expected_balance:
        _reject("point_balance_changed", "點數餘額已變更，請重新預覽")
    transaction_id = _transaction_id(body.request_id)
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    written = await session.scalar(insert(PointTransaction).values(
        id=transaction_id, account_id=account.id, source_type=PointSourceType.ADMIN_ADJUSTMENT,
        reference_id=transaction_id, amount=body.amount, note=body.reason, created_by_id=actor_id,
    ).on_conflict_do_nothing(index_elements=[PointTransaction.id]).returning(PointTransaction.id))
    if written is None:
        existing = await _lookup(session, body.request_id)
        if existing is not None:
            return _replay(existing, user_id, actor_id, body)
        _reject("point_request_conflict", "此調整識別碼已用於其他內容")
    receipt = MemberPointAdjustmentReceipt(id=transaction_id, user_id=user_id, member_number=body.member_number,
        amount=body.amount, balance_before=balance, balance_after=balance + body.amount)
    session.add(AdminAudit(actor_id=actor_id, action="points.adjusted", aggregate_type="point_transaction",
        aggregate_id=transaction_id, reason=body.reason,
        data={**receipt.model_dump(exclude={"id"}), "request_id": str(body.request_id)}))
    return receipt
