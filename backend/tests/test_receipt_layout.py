import re
import unicodedata

import pytest

from app.integrations.feie import FeieError
from app.printing import format_receipt, receipt_preview


def sample_receipt(**extra):
    return {
        "kind": "order", "revision": 1,
        "order_number": "M261101TEST0001", "pickup_code": "123456",
        "customer_name": "測試顧客", "customer_phone": "0900000000",
        "customer_email": "sample@example.com",
        "ordered_at": "2026-11-01 18:20", "pickup_at": "2026-11-02 11:30",
        "print_requested_at": "2026-11-01 18:20:30",
        "location": "合作社門市", "needs_utensils": False,
        "amount_total": 240, "payment_method": "線上付款", "payment_status": "已付款",
        "items": [{"name": "香烤雞腿便當", "quantity": 2, "unit_price": 120, "subtotal": 240,
                   "options": ["主食：紫米飯"]}],
        **extra,
    }


def test_receipt_has_readable_sections_and_phone_first():
    content = format_receipt(sample_receipt())
    plain = receipt_preview(content)
    assert "<CB>便當訂餐單</CB>" in content
    assert "<CB>取餐碼 123456</CB>" in content
    assert "<L><BOLD>顧客：測試顧客</BOLD></L>" in content
    assert "電話：0900000000" in plain and "sample@example.com" not in plain
    assert plain.index("訂購：2026-11-01 18:20") < plain.index("取餐：2026-11-02 11:30")
    assert plain.index("取餐點：合作社門市") < plain.index("2 × 香烤雞腿便當")
    assert "餐具：不需要" in plain and "主食：紫米飯" in plain
    assert "共 2 份" in plain
    assert "付款方式：線上付款" in plain and "付款狀態：已付款" in plain
    assert "<C><L><BOLD>總金額：NT$240</BOLD></L></C>" in content
    assert "送印時間：2026-11-01 18:20:30" in plain
    assert "<" not in plain


@pytest.mark.parametrize("kind", ["order", "amendment", "cancellation"])
def test_receipt_wraps_real_paper_width_and_does_not_execute_customer_tags(kind):
    snapshot = sample_receipt(
        kind=kind, customer_phone=None, customer_email="long.test.customer.address@example.com",
        customer_name="超長姓名" * 8 + "<CB>", note="少飯<BR>不要辣\x1b",
        cancellation_reason="顧客取消<CUT>", reprint=True,
        quantity_changes=[{"name": "香烤雞腿便當", "before": 1, "after": 2}],
    )
    content = format_receipt(snapshot)
    assert "＜CB＞" in content and "＜BR＞" in content
    assert "\x1b" not in content and "<CUT>" not in content
    assert "long.test.customer.address@example.com" in receipt_preview(content).replace("\n", "")
    assert "電話：" not in receipt_preview(content)
    for line in content.split("<BR>"):
        plain = re.sub(r"</?\w+>", "", line)
        cells = sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1 for char in plain)
        assert cells <= (16 if "<CB>" in line else 32)
    assert content.endswith("<BR><BR><BR>")


def test_pending_and_legacy_receipts_do_not_fabricate_contact_or_times():
    snapshot = sample_receipt()
    for key in ["customer_phone", "customer_email", "ordered_at", "print_requested_at", "pickup_code"]:
        snapshot.pop(key)
    plain = receipt_preview(format_receipt(snapshot))
    assert "電話：" not in plain and "Email：" not in plain and "訂購：" not in plain
    assert "取餐：2026-11-02 11:30" in plain
    assert "送印時間：送印時產生" in plain


def test_tags_count_towards_provider_byte_limit():
    with pytest.raises(FeieError, match="receipt_too_long"):
        format_receipt(sample_receipt(note="繁體中文字" * 400))


def test_adjusted_total_keeps_item_amounts_and_final_order_total_distinct():
    plain = receipt_preview(format_receipt(sample_receipt(amount_total=210)))
    assert "小計 $240" in plain
    assert "餐點小計：NT$240" in plain and "金額調整：-NT$30" in plain
    assert "總金額：NT$210" in plain
