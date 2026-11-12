import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..domain import aware
from ..meal_finance import MealSalesFilters, meal_sales_details, meal_sales_report
from ..meal_schedules import TAIPEI
from ..models import User


meal_finance_router = APIRouter(prefix="/v1/admin/finance", tags=["meal-finance"])


@meal_finance_router.get("/meal-sales")
async def read_meal_sales(
    filters: Annotated[MealSalesFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await meal_sales_report(session, filters)


def _csv_text(value):
    text = "" if value is None else str(value)
    return f"'{text}" if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")) else text


def _local_time(value):
    return aware(value).astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M") if value else ""


@meal_finance_router.get("/meal-sales.csv")
async def export_meal_sales(
    filters: Annotated[MealSalesFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["訂單編號", "下單時間", "付款時間", "買家", "聯絡信箱", "會員身分", "取餐時間", "品項與加選", "總數量",
                     "訂單金額", "稅額", "期間收款", "期間完成退款", "期間淨收款", "付款狀態", "取餐狀態", "退單審核", "發票狀態", "發票號碼", "退款紀錄", "銷售類型", "目前訂單版本", "實收交易紀錄", "訂單分類"])
    page_filters = filters.model_copy(update={"limit": 100, "offset": 0})
    while True:
        page = await meal_sales_details(session, page_filters)
        for order in page["items"]:
            items = "；".join(f"{item['name']} ×{item['quantity']} {item['unit']} / NT${item['subtotal']}"
                             + ("（" + "、".join(option["name"] for option in item["options"]) + "）" if item["options"] else "") for item in order["items"])
            refunds = "；".join(f"NT${refund['amount']} {refund['status']} {_local_time(refund['completed_at'])} {refund['reason']}" for refund in order["refunds"])
            receipts = "；".join(f"第{receipt['order_revision']}版 {receipt['merchant_trade_no']} NT${receipt['amount']} {receipt['status']} {_local_time(receipt['received_at']) if receipt['received_at'] else '收款日期待核對'}"
                                 + (" 舊版付款" if receipt['obsolete_revision'] else "") for receipt in order['receipts'])
            writer.writerow([_csv_text(value) for value in [order["order_number"], _local_time(order["created_at"]), _local_time(order["paid_at"]),
                order["customer_name"], order["customer_email"], order["membership_type"], _local_time(order["pickup_at"]), items,
                sum(item["quantity"] for item in order["items"]), order["order_amount"], order["tax_amount"], order["period_received"],
                order["period_refunded"], order["period_net"], order["payment_status"], order["fulfillment_status"], order["cancellation_status"],
                order["invoice_status"], order["invoice_number"], refunds,
                {"meal_preorder": "便當", "regular": "日常選品", "group": "共同購買"}[order["sales_channel"]], order["revision"], receipts,
                order["financial_order_category_label"]]])
        if page_filters.offset + len(page["items"]) >= page["total"]:
            break
        page_filters = page_filters.model_copy(update={"offset": page_filters.offset + 100})
    filename = f"meal-sales-{filters.starts_on}-{filters.ends_on}.csv"
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
