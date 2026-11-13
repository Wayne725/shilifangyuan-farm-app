"""Index encrypted membership phone numbers and link roster-only counter sales."""

from alembic import op
import sqlalchemy as sa

from app.config import get_settings
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.phone_lookup import phone_lookup_digest

revision = "0068_counter_member_phone"
down_revision = "0067_meal_event_archive"
branch_labels = None
depends_on = None

PHONE_COLUMNS = (
    ("users", "meal_contact_phone_encrypted", "meal_contact_phone_lookup", "meal-contact"),
    ("member_profiles", "phone_encrypted", "phone_lookup", "member-profile"),
    ("member_roster_entries", "phone_encrypted", "phone_lookup", "member-roster"),
)
IDENTITY_CHECK = (
    "(customer_kind = 'account' AND user_id IS NOT NULL AND verified_counter_membership_id IS NULL{account_roster}) OR "
    "(customer_kind = 'walk_in' AND user_id IS NULL AND sales_channel = 'regular' AND payment_method = 'cash_on_pickup' AND "
    "{exclusive}(({counter_id} IS NULL AND membership_type_snapshot = 'nonmember' AND NOT member_tax_exempt_snapshot) OR "
    "({counter_id} IS NOT NULL AND membership_type_snapshot IN ('member', 'trainee') AND "
    "member_tax_exempt_snapshot = (membership_type_snapshot = 'member' AND invoice_buyer_type = 'personal'))))"
)
ROSTER_FK = "fk_orders_counter_roster"
ROSTER_INDEX = "ix_orders_verified_counter_roster_entry_id"


def _backfill(table_name: str, phone_name: str, lookup_name: str, context: str) -> None:
    identity = "user_id" if table_name == "member_profiles" else "id"
    table = sa.table(table_name, *(sa.column(name) for name in {"id", identity, phone_name, lookup_name}))
    conn = op.get_bind()
    query = sa.select(table.c.id, table.c[identity], table.c[phone_name]).where(
        table.c[phone_name].is_not(None), table.c[phone_name] != "",
    ).order_by(table.c.id).limit(500)
    cursor = None
    cipher = None
    while True:
        rows = conn.execute(query if cursor is None else query.where(table.c.id > cursor)).all()
        if not rows:
            return
        cipher = cipher or pii_cipher_from_settings(get_settings())
        conn.execute(
            table.update().where(table.c.id == sa.bindparam("row_id")).values({lookup_name: sa.bindparam("digest")}),
            [
                {
                    "row_id": row_id,
                    "digest": phone_lookup_digest(
                        cipher.decrypt_text(ciphertext, associated_data=f"{context}:{owner_id}"), cipher,
                    ),
                }
                for row_id, owner_id, ciphertext in rows
            ],
        )
        cursor = rows[-1][0]


def _backfill_verified_roster_phones() -> None:
    conn = op.get_bind()
    candidates = sa.text("""
        SELECT u.id, r.id AS roster_id, r.phone_encrypted
        FROM users u
        JOIN member_roster_entries r ON r.claimed_user_id = u.id
        JOIN memberships m ON m.user_id = u.id AND m.member_number = r.member_number
        LEFT JOIN member_profiles p ON p.user_id = u.id
        WHERE u.meal_contact_phone_encrypted IS NULL AND p.id IS NULL
          AND r.phone_lookup IS NOT NULL AND u.id > :cursor
        ORDER BY u.id LIMIT 500
    """)
    update_phone = sa.text("""
        UPDATE users SET meal_contact_phone_encrypted = :ciphertext, meal_contact_phone_lookup = :digest
        WHERE id = :row_id
    """)
    cursor = ""
    cipher = None
    while True:
        rows = conn.execute(candidates, {"cursor": cursor}).all()
        if not rows:
            return
        cipher = cipher or pii_cipher_from_settings(get_settings())
        updates = []
        for user_id, roster_id, ciphertext in rows:
            phone = cipher.decrypt_text(ciphertext, associated_data=f"member-roster:{roster_id}")
            updates.append({
                "row_id": user_id,
                "ciphertext": cipher.encrypt_text(phone, associated_data=f"meal-contact:{user_id}"),
                "digest": phone_lookup_digest(phone, cipher),
            })
        conn.execute(update_phone, updates)
        cursor = rows[-1][0]


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    for table, phone, lookup, context in PHONE_COLUMNS:
        op.add_column(table, sa.Column(lookup, sa.String(64), nullable=True))
        _backfill(table, phone, lookup, context)
        op.create_index(f"ix_{table}_{lookup}", table, [lookup])
    _backfill_verified_roster_phones()
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("verified_counter_roster_entry_id", sa.String(36), nullable=True))
        batch.create_foreign_key(ROSTER_FK, "member_roster_entries", ["verified_counter_roster_entry_id"], ["id"], ondelete="RESTRICT")
        batch.create_index(ROSTER_INDEX, ["verified_counter_roster_entry_id"])
        batch.drop_constraint("customer_identity", type_="check")
        batch.create_check_constraint("customer_identity", IDENTITY_CHECK.format(
            account_roster=" AND verified_counter_roster_entry_id IS NULL",
            exclusive="(verified_counter_membership_id IS NULL OR verified_counter_roster_entry_id IS NULL) AND ",
            counter_id="COALESCE(verified_counter_membership_id, verified_counter_roster_entry_id)",
        ))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE")
    elif op.get_context().dialect.name == "sqlite":
        op.execute("UPDATE orders SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM orders WHERE verified_counter_roster_entry_id IS NOT NULL)")):
        raise RuntimeError("已有名冊社員現場交易，請保留社員身分並向前修復")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("customer_identity", type_="check")
        batch.drop_index(ROSTER_INDEX)
        batch.drop_constraint(ROSTER_FK, type_="foreignkey")
        batch.drop_column("verified_counter_roster_entry_id")
        batch.create_check_constraint("customer_identity", IDENTITY_CHECK.format(
            account_roster="", exclusive="", counter_id="verified_counter_membership_id",
        ))
    for table, _, lookup, _ in reversed(PHONE_COLUMNS):
        op.drop_index(f"ix_{table}_{lookup}", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column(lookup)
