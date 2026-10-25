"""Hook orchestration tests with a fake Frappe boundary, NOT bench integration tests."""
import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


class InvoiceFlowTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = Mock(side_effect=ValueError("blocked"))
        self.frappe.session = types.SimpleNamespace(user="cashier@example.com")
        self.utils = types.ModuleType("frappe.utils")
        self.utils.nowdate = lambda: "2026-10-25"
        self.utils.cint = int
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": self.utils})
        self.modules.start()
        self.flow = importlib.import_module("payment_terminal_bridge.invoice_flow")
        self.previous_frappe = self.flow.frappe
        self.flow.frappe = self.frappe
        self.doc = Mock(docstatus=0, doctype="Sales Invoice", name="INV-1")
        self.doc.flags = {}

    def tearDown(self):
        self.flow.frappe = self.previous_frappe
        self.modules.stop()

    def test_auto_send_only_when_opted_in_and_no_previous_attempt(self):
        for auto_send, existing, count in ((False, None, 0), (True, None, 1),
                                            (True, Mock(status="FAILED"), 0),
                                            (True, Mock(status="APPROVED"), 0)):
            with self.subTest(auto_send=auto_send, existing=existing):
                route = types.SimpleNamespace(auto_send=auto_send)
                with patch.object(self.flow, "_selected_mode", return_value="Card"), \
                     patch.object(self.flow, "_route", return_value=route), \
                     patch.object(self.flow, "_latest", return_value=existing), \
                     patch.object(self.flow, "_queue") as queue:
                    self.flow.on_update(self.doc)
                    self.assertEqual(queue.call_count, count)

    def test_submitted_invoice_is_never_automatically_charged(self):
        self.doc.docstatus = 1
        with patch.object(self.flow, "_queue") as queue:
            self.flow.on_update(self.doc)
            queue.assert_not_called()

    def test_submit_without_approval_is_blocked_for_routed_invoice(self):
        with patch.object(self.flow, "_approved", return_value=None), \
             self.assertRaises(ValueError):
            self.flow.before_submit(self.doc)

    def test_non_terminal_invoice_keeps_normal_submit_flow(self):
        self.doc.get.return_value = None
        with patch.object(self.flow, "_approved", return_value=None), \
             patch.object(self.flow, "_route", return_value=None):
            self.flow.before_submit(self.doc)
        self.frappe.throw.assert_not_called()

    def test_agent_result_updates_display_not_accounting(self):
        self.frappe.get_doc = Mock(return_value=self.doc)
        self.frappe.db = Mock()
        request = types.SimpleNamespace(operation="sale", invoice_type="Sales Invoice",
            invoice_name="INV-1", name="PTR-1", status="APPROVED", provider_reference="REF-1")
        self.flow.record_result(request)
        self.frappe.db.set_value.assert_called_once()
        self.doc.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()