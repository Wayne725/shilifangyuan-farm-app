"""Read saved invoice tax semantics without recalculating or exposing provider data."""
from __future__ import annotations

import re


_STATUSES = {"external", "not_eligible", "pending", "issued", "failed", "void_pending", "voided"}
_TAX_TYPES = {"1": "taxable", "3": "tax_exempt", "9": "mixed"}


def _value(value):
    return getattr(value, "value", value)


def _amount(value):
    if type(value) is int and 0 <= value <= 2**63 - 1:
        return value
    if type(value) is str and re.fullmatch(r"[0-9]{1,19}", value):
        number = int(value)
        return number if number <= 2**63 - 1 else None
    return None


def _tax_type(request, field, lines_field, line_tax_field):
    code = request.get(field)
    if type(code) is not str or code not in _TAX_TYPES:
        return None
    lines = request.get(lines_field)
    if not isinstance(lines, list) or not lines:
        return None
    types = set()
    for line in lines:
        if not isinstance(line, dict) or type(line.get(line_tax_field)) is not str or line[line_tax_field] not in {"1", "3"}:
            return None
        types.add(line[line_tax_field])
    return code if types == ({"1", "3"} if code == "9" else {code}) else None


def invoice_tax_summary(invoice):
    if invoice is None:
        return None
    status = _value(getattr(invoice, "status", None))
    status = status if type(status) is str and status in _STATUSES else "unknown"
    unknown = {"status": status, "tax_type": "unknown", "amount_semantics": "unknown", "tax_amount": None}
    if status not in {"issued", "void_pending", "voided"}:
        return unknown
    number = getattr(invoice, "invoice_number", None)
    request = getattr(invoice, "provider_request", None)
    if type(number) is not str or not number.strip() or not isinstance(request, dict):
        return unknown
    saved = [getattr(invoice, name, None) for name in ("sales_amount", "tax_amount", "total_amount")]
    if any(type(value) is not int or _amount(value) is None for value in saved):
        return unknown
    sales, tax, total = saved
    provider = getattr(invoice, "provider", None)
    if provider == "fanyu":
        code = _tax_type(request, "taxType", "Details", "taxType")
        fields = ("salesAmount", "freetaxSalesamount", "zerotaxSalesamount", "taxAmount", "totalAmount")
        values = [_amount(request.get(name)) for name in fields]
        if code is None or any(value is None for value in values):
            return unknown
        taxable_sales, exempt, zero, raw_tax, raw_total = values
        buyer = _value(getattr(invoice, "buyer_type", None))
        process = request.get("process_type")
        if type(process) is not str or type(buyer) is not str or (process, buyer) not in {("B", "company"), ("C", "personal")}:
            return unknown
        # Fanyu's stored sales_amount contains taxable sales only, excluding exempt sales.
        if (sales != taxable_sales or tax != raw_tax or total != raw_total or zero != 0
                or taxable_sales + exempt + zero + raw_tax != total
                or (code == "1" and exempt != 0)
                or (code == "3" and (taxable_sales != 0 or tax != 0))
                or (process == "C" and tax != 0)):
            return unknown
        semantics = "included_not_separately_stated" if process == "C" and code != "3" else "separately_stated"
    elif provider == "ecpay":
        code = _tax_type(request, "TaxType", "Items", "ItemTaxType")
        if (code is None or request.get("vat") != "1" or _amount(request.get("SalesAmount")) != total
                or sales != total or tax != 0):
            return unknown
        semantics = "separately_stated" if code == "3" else "included_not_separately_stated"
    else:
        return unknown
    return {"status": status, "tax_type": _TAX_TYPES[code], "amount_semantics": semantics,
            "tax_amount": tax if semantics == "separately_stated" else None}
