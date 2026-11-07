from copy import deepcopy
from types import SimpleNamespace
import unittest

from app.invoice_tax_summary import invoice_tax_summary


def fanyu(*, personal=False, exempt=0, tax_type="1"):
    sales, tax = (120, 0) if personal else (1000, 50)
    if tax_type == "3":
        sales, tax, exempt = 0, 0, 120
    lines = [{"taxType": code, "description": "private-line"} for code in (["1", "3"] if tax_type == "9" else [tax_type])]
    return SimpleNamespace(provider="fanyu", status="issued", invoice_number="AB12345678",
        buyer_type="personal" if personal else "company", sales_amount=sales, tax_amount=tax, total_amount=sales + tax + exempt,
        provider_request={"process_type": "C" if personal else "B", "taxType": tax_type,
            "salesAmount": str(sales), "freetaxSalesamount": str(exempt), "zerotaxSalesamount": "0",
            "taxAmount": str(tax), "totalAmount": str(sales + tax + exempt), "Details": lines,
            "buyerID": "private-tax-id", "buyerName": "private-buyer", "notifyEmail": "private@example.com"})


def ecpay(tax_type="1"):
    return SimpleNamespace(provider="ecpay", status="issued", invoice_number="AB12345678", buyer_type="company",
        sales_amount=120, tax_amount=0, total_amount=120, provider_request={"TaxType": tax_type, "SalesAmount": 120, "vat": "1",
        "Items": [{"ItemTaxType": code} for code in (["1", "3"] if tax_type == "9" else [tax_type])], "CustomerEmail": "private@example.com"})


class InvoiceTaxSummaryTests(unittest.TestCase):
    def assert_unknown(self, invoice, status="issued"):
        self.assertEqual(invoice_tax_summary(invoice), {"status": status, "tax_type": "unknown",
            "amount_semantics": "unknown", "tax_amount": None})

    def test_no_invoice_and_original_company_tax(self):
        self.assertIsNone(invoice_tax_summary(None))
        self.assertEqual(invoice_tax_summary(fanyu()), {"status": "issued", "tax_type": "taxable",
            "amount_semantics": "separately_stated", "tax_amount": 50})

    def test_fanyu_personal_taxable_and_mixed_are_not_zero_tax_or_exempt(self):
        for code in ["1", "9"]:
            invoice = fanyu(personal=True, tax_type=code, exempt=80 if code == "9" else 0)
            self.assertEqual(invoice_tax_summary(invoice), {"status": "issued", "tax_type": "mixed" if code == "9" else "taxable",
                "amount_semantics": "included_not_separately_stated", "tax_amount": None})

    def test_mixed_fanyu_sales_excludes_exempt_and_no_recalculation(self):
        invoice = fanyu(tax_type="9", exempt=80)
        invoice.sales_amount, invoice.tax_amount, invoice.total_amount = 200, 10, 290
        invoice.provider_request.update(salesAmount="200", taxAmount="10", totalAmount="290")
        self.assertEqual(invoice_tax_summary(invoice)["tax_amount"], 10)
        invoice.sales_amount = 280
        self.assert_unknown(invoice)

    def test_zero_priced_line_still_preserves_mixed_classification(self):
        invoice = fanyu(personal=True, tax_type="9", exempt=0)
        self.assertEqual(invoice_tax_summary(invoice)["tax_type"], "mixed")

    def test_exempt_requires_original_tax_type_and_consistent_amounts(self):
        for invoice in [fanyu(personal=True, tax_type="3"), fanyu(tax_type="3"), ecpay("3")]:
            self.assertEqual(invoice_tax_summary(invoice), {"status": "issued", "tax_type": "tax_exempt",
                "amount_semantics": "separately_stated", "tax_amount": 0})

    def test_ecpay_taxable_and_mixed_saved_zero_are_included(self):
        for code in ["1", "9"]:
            summary = invoice_tax_summary(ecpay(code))
            self.assertEqual(summary["amount_semantics"], "included_not_separately_stated")
            self.assertIsNone(summary["tax_amount"])
            self.assertNotEqual(summary["tax_type"], "tax_exempt")

    def test_pending_failed_external_and_void_states_are_not_relabelled(self):
        for status in ["pending", "failed", "external", "not_eligible"]:
            invoice = fanyu(); invoice.status = status
            self.assert_unknown(invoice, status)
        for status in ["void_pending", "voided"]:
            invoice = fanyu(); invoice.status = status
            self.assertEqual(invoice_tax_summary(invoice)["status"], status)
            self.assertEqual(invoice_tax_summary(invoice)["tax_amount"], 50)

    def test_missing_or_malformed_source_is_unknown(self):
        mutations = [
            lambda i: setattr(i, "invoice_number", ""), lambda i: setattr(i, "provider", "unknown"),
            lambda i: setattr(i, "provider_request", None), lambda i: setattr(i, "provider_request", []),
            lambda i: setattr(i, "total_amount", 1051), lambda i: setattr(i, "sales_amount", True),
            lambda i: setattr(i, "tax_amount", -1), lambda i: setattr(i, "tax_amount", "50"),
            lambda i: i.provider_request.pop("taxAmount"), lambda i: i.provider_request.pop("Details"),
            lambda i: i.provider_request.update(totalAmount="999"), lambda i: i.provider_request.update(process_type="C"),
            lambda i: i.provider_request.update(process_type=[]), lambda i: setattr(i, "buyer_type", {}),
            lambda i: i.provider_request.update(taxType="3"), lambda i: i.provider_request.update(taxType=[]),
            lambda i: i.provider_request.update(Details=[]), lambda i: i.provider_request.update(Details=[{"taxType": []}]),
            lambda i: i.provider_request.update(Details=[{"taxType": "3"}]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                invoice = fanyu(); mutate(invoice); self.assert_unknown(invoice)
        for value in [True, -1, 50.0, "-1", "５０", "1e2", "50.0", "", None, [], {}, "9" * 20]:
            with self.subTest(amount=repr(value)):
                invoice = fanyu(); invoice.provider_request["taxAmount"] = value; self.assert_unknown(invoice)
        for value in ["private-status", {}, [], None, True]:
            invoice = fanyu(); invoice.status = value; self.assert_unknown(invoice, "unknown")

    def test_ecpay_snapshot_mismatch_and_missing_metadata_are_unknown(self):
        for field, value in [("TaxType", "2"), ("SalesAmount", True), ("SalesAmount", 121), ("vat", 1), ("vat", "0"), ("Items", [])]:
            invoice = ecpay(); invoice.provider_request[field] = value; self.assert_unknown(invoice)
        invoice = ecpay(); invoice.tax_amount = 1; self.assert_unknown(invoice)
        invoice = ecpay(); invoice.provider_request.pop("vat"); self.assert_unknown(invoice)

    def test_only_closed_safe_fields_and_original_snapshot_unchanged(self):
        for invoice in [fanyu(), fanyu(personal=True), fanyu(tax_type="9", exempt=80), ecpay()]:
            original = deepcopy(invoice.__dict__)
            summary = invoice_tax_summary(invoice)
            self.assertEqual(set(summary), {"status", "tax_type", "amount_semantics", "tax_amount"})
            self.assertNotIn("private", str(summary))
            self.assertEqual(invoice.__dict__, original)


if __name__ == "__main__":
    unittest.main()
