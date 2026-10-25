"""Preserve existing commitments while moving group decisions after settlement."""
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "0037_group_review_window"
down_revision = "0036_meeting_categories"
branch_labels = None
depends_on = None

AUDIT_ACTION = "campaign.review_window_migrated"
ACTIVE_DECISIONS = {"recruiting", "pending_confirmation"}
TERMINATION_ACTIONS = {"campaign.cancelled": "cancelled", "campaign.rejected": "rejected"}
TERMINATION_EVENTS = {"group.cancelled": "cancelled", "group.rejected": "rejected"}
CAMPAIGN_COLUMNS = (
    "confirmed_paid_quantity", "confirmed_min_paid_quantity", "confirmed_threshold_version",
    "confirmation_exception_reason", "legacy_confirmed_intake",
    "review_checked_at",
)
PAYMENT_COLUMNS = ("unpaid_verified_at", "settlement_unknown_since")


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _snapshot(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items() if key in {
                "decision_status", "intake_status", "confirmation_deadline", "deadline",
                "estimated_pickup_start", "estimated_pickup_end", "final_pickup_at",
                "paid_quantity", "reserved_quantity", "threshold_version", "confirmed_at",
                "legacy_confirmed_intake",
            }}


def _termination_evidence(bind, campaigns, audits, outbox):
    candidates = {row["id"] for row in campaigns if row["decision_status"] in ACTIVE_DECISIONS}
    evidence = {key: [] for key in candidates}
    ambiguous = set()
    for table, discriminator, mapping, payload_column in (
        (audits, "action", TERMINATION_ACTIONS, "data"),
        (outbox, "event_type", TERMINATION_EVENTS, "payload"),
    ):
        records = bind.execute(sa.select(table).where(table.c[discriminator].in_(mapping))).mappings()
        for record in records:
            payload = record[payload_column] or {}
            payload_id = payload.get("campaign_id") if isinstance(payload, dict) else None
            affected = {record["aggregate_id"], payload_id} & candidates
            if not affected:
                continue
            if (record["aggregate_type"] != "group_campaign"
                    or (payload_id is not None and payload_id != record["aggregate_id"])):
                ambiguous.update(affected)
                continue
            evidence[record["aggregate_id"]].append({
                "source": table.name, "id": record["id"],
                "decision": mapping[record[discriminator]], "created_at": _aware(record["created_at"]),
            })
    latest = {}
    for campaign_id, records in evidence.items():
        if not records:
            continue
        latest_at = max(record["created_at"] for record in records)
        matching = [record for record in records if record["created_at"] == latest_at]
        if len({record["decision"] for record in matching}) != 1:
            ambiguous.add(campaign_id)
        else:
            latest[campaign_id] = matching
    if ambiguous:
        raise RuntimeError("共同購買取消／拒絕證據不一致，請先人工核對：" + ", ".join(sorted(ambiguous)))
    return latest


def _backfill_campaigns(bind, now):
    metadata = sa.MetaData()
    campaigns = sa.Table("group_campaigns", metadata, autoload_with=bind)
    audits = sa.Table("admin_audits", metadata, autoload_with=bind)
    outbox = sa.Table("outbox_events", metadata, autoload_with=bind)
    rows = list(bind.execute(sa.select(campaigns).order_by(campaigns.c.id).with_for_update()).mappings())
    completed = set(bind.execute(sa.select(audits.c.aggregate_id).where(
        audits.c.action == AUDIT_ACTION, audits.c.aggregate_type == "group_campaign")).scalars())
    termination = _termination_evidence(bind, rows, audits, outbox)
    for row in rows:
        if row["id"] in completed:
            continue
        original = _snapshot(row)
        issues = []
        evidence = termination.get(row["id"], [])
        if row["decision_status"] == "confirmed":
            updates = {"legacy_confirmed_intake": True}
            reason = "保留既有已確認共同購買的接單承諾；不回填未知的歷史確認數量"
        elif row["decision_status"] not in ACTIVE_DECISIONS:
            continue
        elif evidence:
            updates = {"decision_status": evidence[0]["decision"], "intake_status": "closed"}
            reason = "依既有整團決策證據修復狀態；未新增退款或通知"
            issues.append("restored_existing_termination_decision")
        else:
            deadline = _aware(row["deadline"])
            review_at = (deadline + timedelta(hours=72) if deadline > now else
                         max(_aware(row["confirmation_deadline"]) if row["confirmation_deadline"] else now,
                             now + timedelta(hours=72)))
            intake = ("full" if row["paid_quantity"] + row["reserved_quantity"] >= row["supply_cap"] else "open")
            updates = {"decision_status": "recruiting", "intake_status": intake if deadline > now else "settling",
                       "confirmation_deadline": review_at}
            reason = "轉入截止後付款核對及管理員審核；保留原付款截止與取貨承諾"
            if _aware(row["estimated_pickup_start"]) <= now or _aware(row["estimated_pickup_end"]) <= now:
                issues.append("pickup_time_has_passed")
            if _aware(row["estimated_pickup_start"]) <= review_at:
                issues.append("pickup_conflicts_with_review_deadline")
            if row["final_pickup_at"] is not None and _aware(row["final_pickup_at"]) <= review_at:
                issues.append("final_pickup_conflicts_with_review_deadline")
        bind.execute(campaigns.update().where(campaigns.c.id == row["id"]).values(**updates))
        after = dict(row)
        after.update(updates)
        bind.execute(audits.insert().values(
            id=str(uuid5(NAMESPACE_URL, f"{revision}:{row['id']}")), actor_id=row["created_by_id"],
            action=AUDIT_ACTION, aggregate_type="group_campaign", aggregate_id=row["id"],
            reason=reason, created_at=now, data={
                "migration": revision, "before": original, "after": _snapshot(after),
                "requires_manual_review": bool(issues), "issues": issues,
                "termination_evidence": [{**item, "created_at": item["created_at"].isoformat()} for item in evidence],
            },
        ))


def upgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    campaigns = sa.Table("group_campaigns", metadata, autoload_with=bind)
    audits = sa.Table("admin_audits", metadata, autoload_with=bind)
    outbox = sa.Table("outbox_events", metadata, autoload_with=bind)
    existing = list(bind.execute(sa.select(campaigns).with_for_update()).mappings())
    _termination_evidence(bind, existing, audits, outbox)
    for name in CAMPAIGN_COLUMNS[:3]:
        op.add_column("group_campaigns", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("group_campaigns", sa.Column("confirmation_exception_reason", sa.Text(), nullable=True))
    op.add_column("group_campaigns", sa.Column("legacy_confirmed_intake", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("group_campaigns", sa.Column("review_checked_at", sa.DateTime(timezone=True), nullable=True))
    for name in PAYMENT_COLUMNS:
        op.add_column("payment_attempts", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    _backfill_campaigns(op.get_bind(), datetime.now(timezone.utc))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM group_campaigns")):
        raise RuntimeError("已有共同購買資料，降版可能遺失審核決策或恢復自動退款，無法安全降版；請以升版前備份規劃還原")
    if bind.scalar(sa.text("SELECT COUNT(*) FROM payment_attempts WHERE unpaid_verified_at IS NOT NULL OR settlement_unknown_since IS NOT NULL")):
        raise RuntimeError("已有付款核對紀錄，無法安全降版")
    with op.batch_alter_table("payment_attempts") as batch:
        for name in reversed(PAYMENT_COLUMNS):
            batch.drop_column(name)
    with op.batch_alter_table("group_campaigns") as batch:
        for name in reversed(CAMPAIGN_COLUMNS):
            batch.drop_column(name)
