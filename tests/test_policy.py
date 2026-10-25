import json
from pathlib import Path
import unittest

from payment_terminal_bridge.policy import FINAL, OPEN, minor_units, validate_report


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.request = {"operation": "sale", "status": "REQUEST_SENT", "amount_minor": 1234}
        self.device = {"provider_id": "nami", "terminal_id": "T1"}
        self.report = {"request": "PTR-1", "agent_attempt_id": "attempt-1", "status": "APPROVED",
                       "verified": True, "provider_id": "nami", "terminal_id": "T1",
                       "amount_minor": 1234, "provider_reference": "ref", "ecr_reference": "ecr"}

    def test_exact_minor_units(self):
        self.assertEqual(minor_units("12.34"), 1234)
        for value in ("NaN", "Infinity", "0", "-1", "1.001", "1000000.01", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                minor_units(value)

    def test_verified_approval(self):
        self.assertEqual(validate_report(self.request, self.report, self.device), self.report)

    def test_required_evidence_and_forbidden_raw_data(self):
        for change in ({"verified": False}, {"verified": 1}, {"amount_minor": 1235},
                       {"provider_reference": None}, {"terminal_id": "T2"}, {"pan": "forbidden"},
                       {"status": "REFUNDED"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_report(self.request, self.report | change, self.device)

    def test_final_outcome_and_attempt_cannot_change(self):
        request = self.request | {"status": "APPROVED", "agent_attempt_id": "attempt-1"}
        self.assertEqual(validate_report(request, self.report, self.device), self.report)
        for change in ({"status": "FAILED"}, {"agent_attempt_id": "attempt-2"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_report(request, self.report | change, self.device)

    def test_uncertainty_cannot_be_retried_as_unsent(self):
        for previous in ("UNKNOWN", "MANUAL_REVIEW"):
            for status in ("FAILED", "REQUEST_SENT", "PROCESSING", "REFUND_REQUESTED", "REVERSAL_REQUESTED"):
                with self.subTest(previous=previous, status=status), self.assertRaises(ValueError):
                    validate_report(self.request | {"status": previous}, self.report | {"status": status}, self.device)

    def test_unverified_decline_and_cancel_are_not_retryable(self):
        for status in ("DECLINED", "CANCELLED"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_report(self.request, self.report | {"status": status, "verified": False}, self.device)

    def test_mock_requires_explicit_test_mode(self):
        report = self.report | {"provider_id": "mock"}
        device = self.device | {"provider_id": "mock"}
        with self.assertRaises(ValueError):
            validate_report(self.request, report, device)
        self.assertEqual(validate_report(self.request, report, device, allow_mock=True), report)

    def test_adjustment_states_match_operation(self):
        for operation, status, final in (("refund", "REFUND_REQUESTED", "REFUNDED"), ("reversal", "REVERSAL_REQUESTED", "REVERSED")):
            request = self.request | {"operation": operation}
            for outcome in (status, final):
                report = self.report | {"status": outcome}
                self.assertEqual(validate_report(request, report, self.device), report)

    def test_schema_covers_policy_and_unique_keys(self):
        root = Path(__file__).resolve().parents[1] / "payment_terminal_bridge" / "payment_terminal_bridge" / "doctype"
        for name in ("payment_terminal_agent", "payment_terminal_request"):
            schema = json.loads((root / name / (name + ".json")).read_text())
            fields = {field["fieldname"]: field for field in schema["fields"]}
            self.assertEqual(set(schema["field_order"]), set(fields))
            self.assertFalse(any(p.get("delete") for p in schema["permissions"]))
            if name == "payment_terminal_request":
                self.assertEqual(set(fields["status"]["options"].splitlines()), OPEN | FINAL)
                self.assertEqual(fields["idempotency_key"]["unique"], 1)
                self.assertFalse(any(p.get("write") or p.get("create") for p in schema["permissions"]))


if __name__ == "__main__":
    unittest.main()