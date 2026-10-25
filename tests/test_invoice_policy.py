import copy
import unittest

from payment_terminal_bridge.invoice_policy import full_payment, invoice_snapshot


class InvoicePolicyTests(unittest.TestCase):
    def setUp(self):
        self.invoice = {"doctype": "Sales Invoice", "name": "INV-1", "company": "Company",
            "customer": "Customer", "currency": "SAR", "grand_total": 150,
            "rounded_total": 150, "items": [{"item_code": "ITEM", "qty": 1, "rate": 150}],
            "payments": [], "taxes": [], "advances": []}

    def test_regular_invoice_and_pos_full_payment(self):
        self.assertEqual(full_payment(self.invoice, "Card"), 15000)
        self.invoice.update(doctype="POS Invoice", payments=[{"mode_of_payment": "Card", "amount": 150}])
        self.assertEqual(full_payment(self.invoice, "Card"), 15000)

    def test_split_partial_wrong_mode_and_excess_are_rejected(self):
        for rows in ([{"mode_of_payment": "Card", "amount": 100}],
                     [{"mode_of_payment": "Cash", "amount": 150}],
                     [{"mode_of_payment": "Card", "amount": 151}],
                     [{"mode_of_payment": "Card", "amount": 100}, {"mode_of_payment": "Cash", "amount": 50}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                full_payment(self.invoice | {"payments": rows}, "Card")

    def test_unsupported_accounting_cases_fail_closed(self):
        for change in ({"currency": "USD"}, {"is_return": 1}, {"write_off_amount": 1},
                       {"advances": [{"allocated_amount": 5}]}, {"loyalty_amount": 1},
                       {"doctype": "POS Invoice"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                full_payment(self.invoice | change, "Card")

    def test_snapshot_ignores_bookkeeping_but_locks_business_fields(self):
        original = invoice_snapshot(self.invoice)
        same = copy.deepcopy(self.invoice)
        same.update(modified="later", docstatus=1, custom_terminal_status="APPROVED")
        same["items"][0].update(name="child", parent="INV-1", docstatus=1)
        same["grand_total"] = 150.0
        self.assertEqual(original, invoice_snapshot(same))
        for key, value in (("customer", "Other"), ("company", "Other"), ("grand_total", 160)):
            self.assertNotEqual(original, invoice_snapshot(self.invoice | {key: value}))
        same["items"][0]["qty"] = 2
        self.assertNotEqual(original, invoice_snapshot(same))


if __name__ == "__main__":
    unittest.main()