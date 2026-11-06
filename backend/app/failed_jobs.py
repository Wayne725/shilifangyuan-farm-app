from __future__ import annotations

from sqlalchemy import case, func, or_

from .models import Order, OutboxEvent


JOB_TITLES = {
    "auth.member_lookup_requested": "社員資格查詢結果",
    "invoice.issue_requested": "電子發票開立",
    "invoice.issue": "電子發票開立",
    "issue_invoice": "電子發票開立",
    "invoice.adjustment_required": "電子發票調整",
    "refund.requested": "退款處理",
    "sandbox_refund": "退款處理",
    "auth.email_verification_requested": "Email 驗證信",
    "auth.password_reset_requested": "密碼重設信",
    "order.cancelled": "訂單取消通知",
    "proposal.review_requested": "提案審核通知",
}
EMAIL_TITLES = {
    "payment_succeeded": "付款成功通知",
    "refund_completed": "退款完成通知",
    "invoice_issued": "發票通知信",
    "email_verification": "Email 驗證信",
    "password_reset": "密碼重設信",
}
JOB_KINDS = {
    "invoice": {"invoice.issue_requested", "invoice.issue", "issue_invoice", "invoice.adjustment_required"},
    "refund": {"refund.requested", "sandbox_refund"},
    "email": {"send_email", "auth.email_verification_requested", "auth.password_reset_requested", "auth.member_lookup_requested"},
}
ERROR_RULES = (
    (("timeout", "timed out", "逾時"), "外部服務回應逾時，尚未確認處理結果。"),
    (("credential", "api key", "apikey", "http 401", "http 403", "status 401", "status 403", "授權", "設定", "簽章"), "外部服務的授權或設定檢查未通過。"),
    (("connection", "connecterror", "network", "dns", "連線"), "目前無法連線到外部服務。"),
    (("http 500", "http 502", "http 503", "http 504", "status 500", "status 502", "status 503", "status 504", "service unavailable"), "外部服務暫時無法完成處理。"),
)
UNKNOWN_ERROR = "目前紀錄未提供可辨識的失敗原因，需進一步核對。"


def job_error_summary(event: OutboxEvent) -> str:
    error = (event.last_error or "").lower()
    return next((summary for words, summary in ERROR_RULES if any(word in error for word in words)), UNKNOWN_ERROR)


def job_title_expression():
    email_title = case(*[(OutboxEvent.payload["event_type"].as_string() == key, title) for key, title in EMAIL_TITLES.items()], else_="平台通知信")
    return case((OutboxEvent.event_type == "send_email", email_title), *[(OutboxEvent.event_type == key, title) for key, title in JOB_TITLES.items()], else_="系統背景工作")


def job_error_expression():
    error = func.lower(func.coalesce(OutboxEvent.last_error, ""))
    return case(*[(or_(*[error.contains(word, autoescape=True) for word in words]), summary) for words, summary in ERROR_RULES], else_=UNKNOWN_ERROR)


def job_order_id_expression():
    return func.coalesce(
        OutboxEvent.payload["order_id"].as_string(),
        OutboxEvent.payload["data"]["order_id"].as_string(),
        case((OutboxEvent.aggregate_type == "order", OutboxEvent.aggregate_id)),
    )


def job_details(event: OutboxEvent, order: Order | None) -> dict:
    kind = next((key for key, types in JOB_KINDS.items() if event.event_type in types), "other")
    title = EMAIL_TITLES.get(event.payload.get("event_type"), "平台通知信") if event.event_type == "send_email" else JOB_TITLES.get(event.event_type, "系統背景工作")
    description = {
        "invoice": "這筆工作的發票開立或調整結果尚未完成確認。",
        "refund": "這筆工作的退款結果尚未完成確認。",
        "email": "這封通知信的寄送結果尚未完成確認。",
        "other": "這筆背景工作未完成，需由維護人員核對處理紀錄。",
    }[kind]
    next_action = "提供問題編號給網站維護人員核對。"
    if order:
        next_action = {"invoice": "開啟訂單，核對目前的發票狀態。", "refund": "開啟訂單，核對目前的付款與退款狀態。", "email": "開啟訂單核對狀態，再判斷是否重送通知。"}.get(kind, "開啟訂單，核對目前處理狀態。")
    return {"title": title, "kind": kind, "description": description, "next_action": next_action,
            "order_id": order.id if order else None, "order_number": order.order_number if order else None,
            "created_at": event.created_at}
