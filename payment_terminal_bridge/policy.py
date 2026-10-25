"""Pure validation: no network, Frappe or cardholder data."""
from decimal import Decimal, InvalidOperation

FINAL = {"APPROVED", "DECLINED", "CANCELLED", "FAILED", "REFUNDED", "REVERSED"}
OPEN = {"QUEUED", "REQUEST_SENT", "REFUND_REQUESTED", "REVERSAL_REQUESTED", "PROCESSING", "UNKNOWN", "MANUAL_REVIEW"}
SUCCESS = {"sale": "APPROVED", "refund": "REFUNDED", "reversal": "REVERSED"}
REPORT_FIELDS = {
    "request", "agent_attempt_id", "status", "verified", "terminal_id", "provider_id",
    "ecr_reference", "provider_reference", "rrn", "stan", "auth_code", "error_code",
    "amount_minor", "reported_at",
}


def minor_units(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError("Amount must use at most two decimal places")
        result = int(amount)
    except (InvalidOperation, TypeError, OverflowError):
        raise ValueError("Invalid monetary amount") from None
    if not 0 < result <= 100_000_000:
        raise ValueError("Amount outside supported range")
    return result


def validate_report(request, report, device, allow_mock=False):
    """Reports are authenticated agent attestations, NOT independent bank verification.

    The app records terminal_verified only. It deliberately never marks an invoice paid.
    """
    if not isinstance(report, dict) or set(report) - REPORT_FIELDS:
        raise ValueError("Unsupported report fields (raw/card payloads are forbidden)")
    status = report.get("status")
    if status not in OPEN | FINAL or status == "QUEUED":
        raise ValueError("Unsupported report status")
    for key, value in report.items():
        if key not in {"amount_minor", "verified"} and value is not None:
            if not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid report value")
    if type(report.get("verified")) is not bool:
        raise ValueError("verified must be a boolean")
    attempt = report.get("agent_attempt_id")
    if request.get("agent_attempt_id") and attempt != request["agent_attempt_id"]:
        raise ValueError("Attempt identity changed")
    if report.get("amount_minor") is not None and (
        type(report["amount_minor"]) is not int or report["amount_minor"] != request["amount_minor"]
    ):
        raise ValueError("Amount mismatch")
    if attempt:
        if report.get("provider_id") != device["provider_id"] or report.get("terminal_id") != device["terminal_id"]:
            raise ValueError("Terminal identity mismatch")
    if request.get("status") in FINAL:
        if status != request["status"] or attempt != request.get("agent_attempt_id"):
            raise ValueError("Final outcome cannot be overwritten")
    if request.get("status") == "UNKNOWN" and status in {"REQUEST_SENT", "PROCESSING", "FAILED"}:
        raise ValueError("Uncertain payment cannot become not-sent or be resent")
    if request.get("status") in {"UNKNOWN", "MANUAL_REVIEW"} and status in {"REFUND_REQUESTED", "REVERSAL_REQUESTED"}:
        raise ValueError("Uncertain adjustment cannot be restarted")
    if request.get("status") == "MANUAL_REVIEW" and status in {"REQUEST_SENT", "PROCESSING", "FAILED"}:
        raise ValueError("Manual review cannot become not-sent or be resent")
    if status == "REFUND_REQUESTED" and request["operation"] != "refund":
        raise ValueError("Outcome does not match operation")
    if status == "REVERSAL_REQUESTED" and request["operation"] != "reversal":
        raise ValueError("Outcome does not match operation")
    if status in {"APPROVED", "REFUNDED", "REVERSED"}:
        if status != SUCCESS[request["operation"]]:
            raise ValueError("Outcome does not match operation")
        if not attempt or not report["verified"] or not report.get("provider_reference") or not report.get("ecr_reference"):
            raise ValueError("Missing terminal evidence")
        if report.get("amount_minor") != request["amount_minor"]:
            raise ValueError("Missing amount")
        if device["provider_id"] == "mock" and not allow_mock:
            raise ValueError("Mock outcomes require a test agent")
    if status in {"DECLINED", "CANCELLED"} and (not attempt or not report["verified"]):
        raise ValueError("Unverified no-money outcome is not safe to retry")
    return {key: report[key] for key in REPORT_FIELDS if key in report}