from datetime import date, datetime, time, timedelta
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import require_admin
from ..csv_export import csv_response
from ..database import get_session
from ..domain import aware
from ..meal_booking import TAIPEI
from ..models import AdminAudit, MealCancellationRequest, Order, Refund, RefundStatus, SalesChannel, User

meal_cancellation_reports_router = APIRouter(prefix="/v1/admin", tags=["meal-cancellations"])


def cancellation_filters(query, starts_on, ends_on, search):
    if starts_on and ends_on and starts_on > ends_on:
        raise HTTPException(status_code=422, detail="結束日期不可早於開始日期")
    if starts_on:
        query = query.where(MealCancellationRequest.requested_at >= datetime.combine(starts_on, time.min, TAIPEI))
    if ends_on:
        query = query.where(MealCancellationRequest.requested_at < datetime.combine(ends_on + timedelta(days=1), time.min, TAIPEI))
    if search.strip():
        from sqlalchemy import or_
        query = query.where(or_(Order.order_number.icontains(search.strip(), autoescape=True), Order.contact_email.icontains(search.strip(), autoescape=True)))
    return query


async def cancellation_report(session, starts_on, ends_on, search):
    query = select(MealCancellationRequest).join(Order).where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
    requests = list(await session.scalars(cancellation_filters(query, starts_on, ends_on, search).options(
        selectinload(MealCancellationRequest.order).selectinload(Order.refunds), selectinload(MealCancellationRequest.requested_by)
    ).order_by(MealCancellationRequest.requested_at.desc(), MealCancellationRequest.id)))
    summary = {"requests": len(requests), "orders": len({row.order_id for row in requests}), "pending": 0, "approved": 0, "rejected": 0,
               "pending_amount": 0, "approved_amount": 0, "refund_pending": 0, "refunded": 0, "refund_failed": 0}
    approved_orders, rows = set(), []
    for request in requests:
        order = request.order
        cancellation_refunds = [refund for refund in order.refunds if refund.purpose == "order_cancellation"]
        summary[request.status] += 1
        if request.status == "pending":
            summary["pending_amount"] += order.amount_total
        if request.status == "approved" and order.id not in approved_orders:
            approved_orders.add(order.id)
            summary["approved_amount"] += order.amount_total
            for refund in cancellation_refunds:
                summary[{RefundStatus.PENDING: "refund_pending", RefundStatus.COMPLETED: "refunded", RefundStatus.FAILED: "refund_failed"}[refund.status]] += refund.amount
        rows.append({"id": request.id, "order_number": order.order_number, "name": request.requested_by.display_name,
            "email": order.contact_email, "amount": order.amount_total, "status": request.status, "reason": request.reason,
            "requested_at": request.requested_at, "review_reason": request.review_reason, "reviewed_at": request.reviewed_at,
            "payment_status": order.payment_status.value,
            "refund_pending": sum(row.amount for row in cancellation_refunds if row.status == RefundStatus.PENDING),
            "refunded": sum(row.amount for row in cancellation_refunds if row.status == RefundStatus.COMPLETED)})
    return {"summary": summary, "items": rows}


@meal_cancellation_reports_router.get("/meal-cancellation-summary")
async def read_cancellation_summary(starts_on: date | None = None, ends_on: date | None = None, search: str = Query(default="", max_length=100),
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return (await cancellation_report(session, starts_on, ends_on, search))["summary"]


@meal_cancellation_reports_router.get("/meal-cancellations.csv")
async def export_cancellations(starts_on: date | None = None, ends_on: date | None = None, search: str = Query(default="", max_length=100),
    review_status: Literal["pending", "approved", "rejected", "all"] = "all",
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    data = await cancellation_report(session, starts_on, ends_on, search)
    rows = [row for row in data["items"] if review_status == "all" or row["status"] == review_status]
    session.add(AdminAudit(actor_id=admin.id, action="meal.cancellations_exported", aggregate_type="meal_cancellations", aggregate_id=str(uuid4()), data={"count": len(rows)}))
    await session.commit()
    local = lambda value: aware(value).astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M") if value else ""
    statuses = {"pending": "待審核", "approved": "已核准", "rejected": "未核准"}
    return csv_response("meal-cancellations.csv", ["訂單編號", "姓名", "Email", "訂單金額", "審核狀態", "申請原因", "申請時間", "審核理由", "審核時間", "付款狀態", "退款處理中金額", "已完成退款金額"],
        [[row["order_number"], row["name"], row["email"], row["amount"], statuses[row["status"]], row["reason"], local(row["requested_at"]), row["review_reason"], local(row["reviewed_at"]), row["payment_status"], row["refund_pending"], row["refunded"]] for row in rows])
