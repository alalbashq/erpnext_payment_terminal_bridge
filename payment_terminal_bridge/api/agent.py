"""Authenticated, transactional agent and cashier endpoints.

Socket messages are wakeups, not payment commands. Database rows are authoritative.
Agent attestations never submit invoices or create accounting payments automatically.
"""
import hashlib
import json
import re
import uuid

import frappe
from frappe.utils import cint, now_datetime

from payment_terminal_bridge.policy import FINAL, OPEN, minor_units, validate_report

AGENT = "Payment Terminal Agent"
REQUEST = "Payment Terminal Request"
NUDGE = "payment_terminal_agent_nudge"
UPDATE = "payment_terminal_update"


def _object(value):
    value = frappe.parse_json(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        frappe.throw("Expected a JSON object")
    return value


def _agent(name, own=True):
    doc = frappe.get_doc(AGENT, name)
    if not doc.enabled or (own and doc.agent_user != frappe.session.user):
        frappe.throw("Agent access denied", frappe.PermissionError)
    if own and (frappe.session.user == "Guest" or "Payment Terminal Agent" not in frappe.get_roles()):
        frappe.throw("Dedicated agent role required", frappe.PermissionError)
    return doc


@frappe.whitelist(methods=["POST"])
def resolve_connection():
    if frappe.session.user == "Guest" or "Payment Terminal Agent" not in frappe.get_roles():
        frappe.throw("Dedicated agent role required", frappe.PermissionError)
    agents = frappe.get_all(AGENT, filters={"agent_user": frappe.session.user, "enabled": 1},
                            fields=["name"], limit_page_length=2)
    if len(agents) != 1:
        frappe.throw("Configure exactly one enabled Payment Terminal Agent for this API user", frappe.PermissionError)
    owner = _agent(agents[0].name)
    return {"agent": owner.name, "site": frappe.local.site}


def _lock(doctype, name):
    # Row lock is held until Frappe commits the POST request. No Redis lease can expire mid-payment.
    table = frappe.qb.DocType(doctype)
    frappe.qb.from_(table).select(table.name).where(table.name == name).for_update().run()


def _device(agent, device_id):
    rows = frappe.parse_json(agent.devices_json or "[]")
    if not isinstance(rows, list):
        frappe.throw("Invalid server device registry")
    found = next((x for x in rows if x.get("device_id") == device_id), None)
    if not found or not found.get("enabled"):
        frappe.throw("Device is not authorized in server registry")
    if found.get("provider_id") == "mock" and not agent.test_mode:
        frappe.throw("Mock requires a test agent")
    return found


def _nudge(agent):
    frappe.publish_realtime(NUDGE, {"agent": agent.name}, user=agent.agent_user, after_commit=True)


def _publish(doc):
    frappe.publish_realtime(UPDATE, {
        "request": doc.name, "invoice_type": doc.invoice_type, "invoice_name": doc.invoice_name,
        "status": doc.status, "amount_minor": doc.amount_minor, "currency": doc.currency,
        "terminal_verified": bool(doc.terminal_verified), "evidence_source": "agent_attestation",
        "accounting_posted": False, "provider_reference": doc.provider_reference,
    }, user=doc.cashier, after_commit=True)


def _request_view(doc):
    result = {field: doc.get(field) for field in (
        "name", "operation", "idempotency_key", "device_id", "invoice_type", "invoice_name",
        "company", "amount_minor", "currency", "cashier", "original_request", "payment_mode",
    )}
    if doc.original_request:
        result["original_agent_attempt_id"] = frappe.db.get_value(REQUEST, doc.original_request, "agent_attempt_id")
    return result


@frappe.whitelist(methods=["POST"])
def heartbeat(agent, version, devices):
    doc = _agent(agent)
    devices = frappe.parse_json(devices) if isinstance(devices, str) else devices
    if not isinstance(devices, list) or len(devices) > 100:
        frappe.throw("Invalid heartbeat")
    # Diagnostic only: never replace the admin-approved routing registry from agent input.
    safe = []
    for row in devices:
        if not isinstance(row, dict):
            frappe.throw("Invalid device")
        safe.append({key: str(row.get(key, ""))[:100] for key in ("device_id", "provider_id", "terminal_id")})
    frappe.db.set_value(AGENT, doc.name, {
        "last_seen": now_datetime(), "agent_version": str(version)[:40],
        "reported_devices": json.dumps(safe),
    }, update_modified=False)
    return {"agent": doc.name, "user": frappe.session.user}


@frappe.whitelist(methods=["POST"])
def claim_requests(agent, limit=5):
    owner = _agent(agent)
    _lock(AGENT, owner.name)
    names = frappe.get_all(REQUEST, filters={"agent": owner.name, "status": ["in", list(OPEN)]},
                           fields=["name"], order_by="last_claimed_at asc, creation asc, name asc", limit_page_length=max(1, min(cint(limit), 20)))
    requests, cancels = [], []
    for row in names:
        _lock(REQUEST, row.name)
        doc = frappe.get_doc(REQUEST, row.name)
        if doc.status not in OPEN:
            continue  # A report may have finalized the row while this claim waited for its lock.
        doc.last_claimed_at = now_datetime()
        if doc.cancel_requested:
            if doc.status == "QUEUED":
                # No claim has ever been returned: cancellation is definite and needs no terminal call.
                doc.status = "CANCELLED"
                doc.save(ignore_permissions=True)
                _publish(doc)
                continue
            cancels.append(doc.name)
        if doc.status == "QUEUED":
            doc.status = "REQUEST_SENT"
            _publish(doc)
        doc.save(ignore_permissions=True)
        requests.append(_request_view(doc))
    return {"requests": requests, "cancels": cancels}


@frappe.whitelist(methods=["POST"])
def report_result(agent, payload):
    owner = _agent(agent)
    report = _object(payload)
    if len(json.dumps(report)) > 8192:
        frappe.throw("Report too large")
    _lock(REQUEST, report.get("request"))
    doc = frappe.get_doc(REQUEST, report.get("request"))
    if doc.agent != owner.name:
        frappe.throw("Request belongs to another agent", frappe.PermissionError)
    device = _device(owner, doc.device_id)
    try:
        validated = validate_report(doc.as_dict(), report, device, allow_mock=bool(owner.test_mode))
    except ValueError as exc:
        frappe.throw(str(exc))
    # Persist the allowlisted evidence, never the raw terminal payload. Database updates are atomic.
    doc.status = validated["status"]
    doc.terminal_verified = int(validated["verified"])
    for field in ("agent_attempt_id", "provider_reference", "ecr_reference", "rrn", "stan", "auth_code", "error_code"):
        if field in validated:
            doc.set(field, validated[field])
    doc.result_json = json.dumps(validated, separators=(",", ":"))
    doc.save(ignore_permissions=True)
    _publish(doc)
    return {"accepted": True, "status": doc.status}


def _cashier():
    roles = set(frappe.get_roles())
    if not roles.intersection({"Sales User", "Sales Manager", "System Manager"}) or frappe.session.user == "Guest":
        frappe.throw("Cashier role required", frappe.PermissionError)


def _invoice(doctype, name):
    if doctype not in {"POS Invoice", "Sales Invoice"}:
        frappe.throw("Unsupported invoice type")
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("write")
    if doc.docstatus == 2 or doc.is_return or doc.currency != "SAR":
        frappe.throw("Requires a non-return SAR invoice that is not cancelled")
    return doc


@frappe.whitelist(methods=["POST"])
def create_request(agent, device_id, invoice_type, invoice_name, idempotency_key, operation="sale", original_request=None, payment_mode=None):
    _cashier()
    if invoice_type not in {"POS Invoice", "Sales Invoice"}:
        frappe.throw("Unsupported invoice type")
    if operation not in {"sale", "refund", "reversal"} or not re.fullmatch(r"[A-Za-z0-9:_-]{8,120}", str(idempotency_key)):
        frappe.throw("Invalid operation or idempotency key")
    if operation != "sale" and not set(frappe.get_roles()).intersection({"Sales Manager", "System Manager"}):
        frappe.throw("Supervisor role required", frappe.PermissionError)
    owner = _agent(agent, own=False)
    if owner.company != frappe.db.get_value(invoice_type, invoice_name, "company"):
        frappe.throw("Agent company mismatch")
    _lock(AGENT, owner.name)
    device = _device(owner, device_id)
    registered_mode = device.get("payment_mode")
    if not registered_mode or (payment_mode and payment_mode != registered_mode):
        frappe.throw("Payment mode must match the administrator-approved device registry")
    payment_mode = registered_mode
    invoice = _invoice(invoice_type, invoice_name)
    _lock(invoice_type, invoice_name)
    invoice.reload()
    # Replay compares immutable routing, not today's outstanding balance (which may now be zero).
    existing_name = frappe.db.get_value(REQUEST, {"idempotency_key": idempotency_key}, "name")
    if existing_name:
        existing_doc = frappe.get_doc(REQUEST, existing_name)
        expected = {"agent": agent, "device_id": device_id, "invoice_type": invoice_type,
                    "invoice_name": invoice_name, "operation": operation, "cashier": frappe.session.user,
                    "original_request": original_request or "", "payment_mode": payment_mode}
        if any((existing_doc.get(key) or "") != value for key, value in expected.items()):
            frappe.throw("Idempotency conflict")
        return {"request": existing_name}
    # User may create only in their permitted company; no customer-supplied amount is accepted.
    amount = None
    original = None
    if operation != "sale":
        original = frappe.get_doc(REQUEST, original_request)
        if original.agent != agent or original.device_id != device_id or original.invoice_type != invoice_type or original.invoice_name != invoice_name:
            frappe.throw("Original payment routing mismatch")
        if original.operation != "sale" or original.status != "APPROVED" or not original.terminal_verified:
            frappe.throw("Approved original required")
        amount = original.amount_minor  # Full adjustments only; partial refunds require a separate accounting design.
    else:
        amount = minor_units(invoice.outstanding_amount if invoice.docstatus == 1 else invoice.rounded_total or invoice.grand_total)
    identity = [agent, device_id, invoice_type, invoice_name, operation, original_request or "", amount, frappe.session.user]
    fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    existing = frappe.db.get_value(REQUEST, {"idempotency_key": idempotency_key}, ["name", "fingerprint"], as_dict=True)
    if existing:
        if existing.fingerprint != fingerprint:
            frappe.throw("Idempotency conflict")
        return {"request": existing.name}
    if operation == "sale":
        conflict = {"invoice_type": invoice_type, "invoice_name": invoice_name, "operation": "sale",
                    "status": ["not in", ["DECLINED", "CANCELLED", "FAILED"]]}
    else:
        conflict = {"original_request": original.name, "status": ["not in", ["DECLINED", "CANCELLED", "FAILED"]]}
    if frappe.db.exists(REQUEST, conflict):
        frappe.throw("An approved or unresolved payment/adjustment already exists")
    doc = frappe.get_doc({"doctype": REQUEST, "name": "PTR-" + uuid.uuid4().hex,
        "agent": agent, "device_id": device_id, "operation": operation, "original_request": original_request,
        "invoice_type": invoice_type, "invoice_name": invoice_name, "company": invoice.company,
        "cashier": frappe.session.user, "currency": "SAR", "amount_minor": amount, "payment_mode": payment_mode,
        "idempotency_key": idempotency_key, "fingerprint": fingerprint, "status": "QUEUED"})
    doc.insert(ignore_permissions=True)
    _nudge(owner)
    _publish(doc)
    return {"request": doc.name}


@frappe.whitelist(methods=["POST"])
def cancel_request(name):
    _cashier()
    _lock(REQUEST, name)
    doc = frappe.get_doc(REQUEST, name)
    _invoice(doc.invoice_type, doc.invoice_name)
    if doc.cashier != frappe.session.user and not set(frappe.get_roles()).intersection({"Sales Manager", "System Manager"}):
        frappe.throw("Only the cashier or supervisor may cancel", frappe.PermissionError)
    if doc.status not in OPEN:
        frappe.throw("Request already final")
    doc.cancel_requested = 1
    doc.save(ignore_permissions=True)
    _nudge(_agent(doc.agent, own=False))
    return {"request": doc.name, "cancel_requested": True}


@frappe.whitelist()
def get_status(name):
    _cashier()
    doc = frappe.get_doc(REQUEST, name)
    _invoice(doc.invoice_type, doc.invoice_name)
    return {"request": doc.name, "status": doc.status, "terminal_verified": bool(doc.terminal_verified),
            "provider_reference": doc.provider_reference, "accounting_posted": False}