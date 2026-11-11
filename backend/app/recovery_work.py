import re
from uuid import UUID

from sqlalchemy import JSON, and_, case, cast, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Invoice, OutboxEvent, PrintJob, Refund, RefundStatus
from .recovery_quarantine import active_hold, cutover_preparing


INVOICE_ISSUE_EVENTS = frozenset({"invoice.issue", "invoice.issue_requested", "issue_invoice"})
REFUND_EVENTS = frozenset({"refund.requested", "sandbox_refund"})
SUMMARY_ALL_TARGET = ("print_lineage", "summary:all")


def outbox_is_held():
    event_type, aggregate = OutboxEvent.event_type, OutboxEvent.aggregate_id
    payload = OutboxEvent.payload
    refund_id = func.nullif(payload["refund_id"].as_string(), "")
    legacy_order = OutboxEvent.aggregate_type == "order"
    linked_refund = exists(
        select(Refund.id).where(
            or_(
                Refund.id == refund_id,
                and_(refund_id.is_(None), ~legacy_order, Refund.id == aggregate),
                and_(refund_id.is_(None), legacy_order, Refund.order_id == aggregate,
                     Refund.status == RefundStatus.PENDING, Refund.purpose == "order_cancellation"),
            ),
            or_(
                active_hold("order", Refund.order_id),
                active_hold("payment_attempt", Refund.payment_attempt_id),
                active_hold("activity_charge", Refund.activity_charge_id),
                active_hold("membership_charge", Refund.membership_charge_id),
            ),
        ).correlate(OutboxEvent)
    )
    linked_invoice = exists(
        select(Invoice.id).where(
            Invoice.id == func.coalesce(payload["invoice_id"].as_string(), aggregate),
            active_hold("order", Invoice.order_id),
        ).correlate(OutboxEvent)
    )
    order_in_payload = active_hold("order", payload["order_id"].as_string())
    return or_(
        cutover_preparing(),
        active_hold("outbox", OutboxEvent.id),
        and_(
            event_type.in_(INVOICE_ISSUE_EVENTS),
            or_(order_in_payload, active_hold("order", aggregate)),
        ),
        and_(event_type == "invoice.adjustment_required", or_(order_in_payload, linked_invoice)),
        and_(
            event_type.in_(REFUND_EVENTS),
            or_(
                order_in_payload,
                and_(legacy_order, active_hold("order", aggregate)),
                active_hold("activity_charge", payload["activity_charge_id"].as_string()),
                active_hold("membership_charge", payload["membership_charge_id"].as_string()),
                linked_refund,
            ),
        ),
    )


async def outbox_work_held(session: AsyncSession, event_id: str) -> bool:
    return bool(await session.scalar(select(OutboxEvent.id).where(
        OutboxEvent.id == event_id, outbox_is_held(),
    )))


def order_print_targets(order_id: str) -> tuple[tuple[str, str], ...]:
    return ("order", order_id), ("print_lineage", f"order:{order_id}")


def _summary_source_ids(snapshot: dict) -> tuple[str, ...]:
    sources = snapshot.get("source_versions")
    if not isinstance(sources, list):
        return ()
    identifiers = []
    for source in sources:
        identifier = source.get("id") if isinstance(source, dict) else None
        if not isinstance(identifier, str):
            return ()
        try:
            if str(UUID(identifier)) != identifier:
                return ()
        except ValueError:
            return ()
        identifiers.append(identifier)
    return tuple(identifiers)


def summary_print_targets(snapshot: dict) -> tuple[tuple[str, str], ...]:
    if not isinstance(snapshot, dict):
        return (SUMMARY_ALL_TARGET,)
    sources = _summary_source_ids(snapshot)
    # Without complete source IDs a regenerated summary cannot be matched to the original.
    targets = tuple(("print_lineage", f"summary-order:{identifier}") for identifier in sources) or (SUMMARY_ALL_TARGET,)
    fingerprint = snapshot.get("fingerprint")
    if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        targets += (("print_lineage", f"summary:{fingerprint}"),)
    return targets


def print_job_targets(job: PrintJob) -> tuple[tuple[str, str], ...]:
    targets = [("print_job", job.id)]
    if job.reprint_of_id:
        targets.append(("print_job", job.reprint_of_id))
    if job.order_id:
        targets.extend(order_print_targets(job.order_id))
    elif job.kind == "summary":
        targets.extend(summary_print_targets(job.snapshot))
    return tuple(targets)


def print_is_held(session: AsyncSession):
    source_versions = PrintJob.snapshot["source_versions"]
    if session.get_bind().dialect.name == "postgresql":
        array = case((func.json_typeof(source_versions) == "array", source_versions), else_=cast(literal("[]"), JSON))
        source = func.json_array_elements(array).table_valued("value")
        source_id = cast(source.c.value, JSON)["id"].as_string()
    else:
        source = func.json_each(source_versions).table_valued("value", "type")
        source_id = func.json_extract(case((source.c.type == "object", source.c.value)), "$.id")
    linked_summary_order = exists(
        select(1).select_from(source).where(
            active_hold("print_lineage", literal("summary-order:") + source_id),
        ).correlate(PrintJob)
    )
    return or_(
        cutover_preparing(),
        active_hold("print_job", PrintJob.id),
        active_hold("print_job", PrintJob.reprint_of_id),
        active_hold("order", PrintJob.order_id),
        active_hold("print_lineage", literal("order:") + PrintJob.order_id),
        and_(
            PrintJob.kind == "summary",
            or_(
                active_hold(*SUMMARY_ALL_TARGET),
                active_hold("print_lineage", literal("summary:") + PrintJob.snapshot["fingerprint"].as_string()),
                linked_summary_order,
            ),
        ),
    )
