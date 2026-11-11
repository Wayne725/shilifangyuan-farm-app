from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.feie import FeieError, configured_region
from .models import PrintJob
from .order_summary import OrderSummaryFilters, build_print_summary, print_summary_filters
from .printing import printer_fingerprint, receipt_time, wrap_text


MAX_SUMMARY_PAGES = 50
SUMMARY_FORMAT_VERSION = 3


def normalized_print_filters(filters: OrderSummaryFilters) -> dict:
    return print_summary_filters(filters)


def summary_fingerprint(report: dict, settings: Settings, *, format_version: int = SUMMARY_FORMAT_VERSION) -> str:
    if format_version < 3:
        report = {**report, "filters": {key: value for key, value in report["filters"].items() if key != "include_unpaid_business"},
                  "products": [{key: value for key, value in product.items()
                                if key not in {"meal_booking_type", "payment_status"}} for product in report["products"]]}
        for product in report["products"]:
            identity = json.loads(product["key"])
            product["key"] = json.dumps(identity[:6], ensure_ascii=False, separators=(",", ":"))
    payload = {"report": report, "printer": printer_fingerprint(settings),
               "region": configured_region(settings), "width": settings.printer_line_width,
               "mode": "dry_run" if settings.printer_dry_run else "live"}
    if format_version >= 2:
        payload["format_version"] = format_version
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lines(value: object, width: int, tag: str | None = None) -> list[str]:
    return [f"<{tag}>{line}</{tag}>" if tag else line
            for line in wrap_text(value, width // 2 if tag in {"CB", "B", "W"} else width)]


def _variant_label(options: list[str]) -> str:
    staples, extras = [], []
    for option in options:
        group, separator, name = option.partition("：")
        if separator and "主食" in group:
            staples.append(f"主食：{name}")
        else:
            extras.append(name if separator else option)
    return "－".join([*staples, *extras]) or "無選項"


def format_summary_pages(report: dict, generated_at: str, request_id: str, *, width: int = 32,
                         dry_run: bool = False) -> list[str]:
    filters, totals = report["filters"], report["summary"]
    category = {"meal": "便當訂單", "general": "一般訂單", "all": "全部訂單"}.get(filters["order_category"], "訂單")
    date_basis = "取餐日期" if filters.get("date_basis") == "pickup" else "下單日期"

    def header(page: int, total: int) -> list[str]:
        result = [*_lines("十里方圓 合作社", width, "C"), *_lines("訂單彙總", width, "CB")]
        if dry_run:
            result += _lines("＊＊ 模擬列印 ＊＊", width, "BOLD")
        payment_label = "已付款＋會餐未付款" if filters.get("include_unpaid_business") else "已付款"
        result += [*_lines(category, width, "BOLD"), *_lines(payment_label, width, "BOLD"),
                   *_lines(f"{date_basis}（台灣）", width),
                   *_lines(f"{filters['starts_on']} 至 {filters['ends_on']}", width)]
        if filters.get("sales_channel") in {"regular", "group"}:
            result += _lines("日常選購" if filters["sales_channel"] == "regular" else "共同購買", width)
        for key, label in [("search", "品項查詢"), ("category", "品類")]:
            if filters.get(key):
                result += _lines(f"{label}：{filters[key]}", width)
        result += [*_lines(f"統計：{generated_at}", width),
                   *_lines(f"第 {page}/{total} 張", width, "C"),
                   *_lines(f"全單 {totals['order_count']} 筆訂單", width),
                   *_lines(f"{totals['product_count']} 種品項・總數量 {totals['quantity']}", width), "-" * width]
        if filters.get("include_unpaid_business"):
            result += _lines(f"已付款 {totals.get('paid_order_count', 0)} 筆／會餐未付款 {totals.get('pending_business_order_count', 0)} 筆", width)
        return result

    footer_label = "備餐彙總含會餐未付款" if filters.get("include_unpaid_business") else "依統計時間之已付款訂單彙總"
    footer = ["-" * width, *_lines(footer_label, width, "C"), "", "", ""]
    chunks: list[list[str]] = []
    current: list[str] = []

    def encode(rows: list[str], page: int = MAX_SUMMARY_PAGES, total: int = MAX_SUMMARY_PAGES) -> str:
        return "<BR>".join([*header(page, total), *rows, *footer])

    def fits(rows: list[str]) -> bool:
        return len(encode(rows).encode("utf-8")) <= 5000

    def finish() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = []
        if len(chunks) >= MAX_SUMMARY_PAGES:
            raise FeieError("summary_too_many_pages")

    def section(title: str, details: list[str]) -> None:
        nonlocal current
        heading = _lines(title, width, "BOLD")
        rows = [*heading, *details, "-" * width]
        if current and not fits([*current, *rows]) and fits(rows):
            finish()
        if fits([*current, *rows]):
            current += rows
            return
        for row in rows:
            if not fits([*current, row]):
                finish()
                current = [*_lines(f"{title}（續）", width, "BOLD")]
            if not fits([*current, row]):
                raise FeieError("receipt_too_long")
            current.append(row)

    for product in report["products"]:
        details: list[str] = []
        for variant in product["variants"]:
            options = _variant_label(variant["options"])
            details += _lines(f"  {variant['quantity']} {product['unit']} × {options}", width)
        label = product['name']
        if product.get('meal_booking_type') == 'business':
            label = f"會餐｜{'未付款' if product.get('payment_status') == 'pending' else '已付款'}｜{label}"
        section(f"{product['quantity']} {product['unit']} × {label}", details)
    for note in report.get("notes", []):
        details = []
        for item in note["items"]:
            details += _lines(f"  {item['quantity']} {item['unit']} × {item['name']}", width)
        details += _lines(note["note"], width)
        section(f"整單備註：{note['order_number']}", details)
    if current:
        chunks.append(current)
    if len(chunks) > MAX_SUMMARY_PAGES:
        raise FeieError("summary_too_many_pages")
    pages = [encode(rows, page, len(chunks)) for page, rows in enumerate(chunks, 1)]
    if any(len(page.encode("utf-8")) > 5000 for page in pages):
        raise FeieError("receipt_too_long")
    return pages


async def summary_job_is_current(session: AsyncSession, job: PrintJob, settings: Settings) -> bool:
    format_version = job.snapshot.get("format_version", 1)
    if format_version not in (1, 2, SUMMARY_FORMAT_VERSION):
        return False
    try:
        filters = OrderSummaryFilters.model_validate(job.snapshot.get("filters"))
    except ValidationError:
        return False
    try:
        report = await build_print_summary(session, filters)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        return False
    return (report["source_versions"] == job.snapshot.get("source_versions")
            and summary_fingerprint(report, settings, format_version=format_version) == job.snapshot.get("fingerprint"))


async def read_summary_jobs(session: AsyncSession, request_id: str) -> list[PrintJob]:
    return list(await session.scalars(select(PrintJob).where(
        PrintJob.kind == "summary", PrintJob.event_key.startswith(f"summary:{request_id}:", autoescape=True),
    ).order_by(PrintJob.created_at, PrintJob.event_key)))


def summary_batch_read(jobs: list[PrintJob]) -> dict:
    first = jobs[0]
    fingerprint = first.snapshot.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        fingerprint = None
    filters = first.snapshot.get("filters")
    try:
        OrderSummaryFilters.model_validate(filters)
    except ValidationError:
        filters = None
    summary = first.snapshot.get("summary")
    if not isinstance(summary, dict) or any(
        type(value) is not int or value < 0
        for value in (
            summary.get("order_count"), summary.get("quantity"), summary.get("product_count"),
            summary.get("paid_order_count", 0), summary.get("pending_business_order_count", 0),
        )
    ):
        summary = None
    return {"request_id": first.snapshot["request_id"], "mode": first.mode,
            "fingerprint": fingerprint, "created_at": first.created_at, "filters": filters,
            "summary": summary,
            "pages": [{"id": job.id, "page": job.snapshot["page"], "status": job.status,
                       "last_error": job.last_error} for job in sorted(jobs, key=lambda job: job.snapshot["page"])]}


def summary_generated_at() -> str:
    return receipt_time(datetime.now(timezone.utc))
