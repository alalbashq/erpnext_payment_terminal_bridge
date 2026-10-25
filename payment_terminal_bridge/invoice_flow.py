"""Invoice orchestration. Realtime wakes clients; durable rows authorize every action.

Only explicit user submission posts accounting. Agent reports never submit documents.
"""
import uuid

import frappe
from frappe.utils import nowdate

from payment_terminal_bridge.invoice_policy import full_payment, invoice_snapshot

REQUEST = "Payment Terminal Request"
RETRYABLE = {"DECLINED", "CANCELLED", "FAILED"}


def _latest(invoice):
    rows = frappe.get_all(REQUEST, filters={"invoice_type": invoice.doctype,
        "invoice_name": invoice.name, "operation": "sale"}, fields=["name"],
        order_by="creation desc, name desc", limit_page_length=1)
    return frappe.get_doc(REQUEST, rows[0].name) if rows else None


def _selected_mode(invoice):
    selected = invoice.get("custom_terminal_payment_mode")
    if selected:
        return selected
    positive = [row for row in invoice.get("payments") or [] if row.get("amount") and row.amount > 0]
    return positive[0].mode_of_payment if len(positive) == 1 else None


def _route(invoice, mode, required=False):
    rows = frappe.get_all("Payment Terminal Route", filters={"enabled": 1,
        "cashier": frappe.session.user, "company": invoice.company, "payment_mode": mode},
        fields=["name"], limit_page_length=2) if mode else []
    if len(rows) != 1:
        if required:
            frappe.throw("لا يوجد مسار دفع مفعّل ووحيد لهذا المستخدم والشركة وطريقة الدفع")
        return None
    return frappe.get_doc("Payment Terminal Route", rows[0].name)


def _display(invoice, request):
    values = {
        "custom_terminal_request": request.name if request else None,
        "custom_terminal_status": request.status if request else None,
        "custom_terminal_reference": request.provider_reference if request else None,
    }
    for key, value in values.items():
        invoice.set(key, value)
    return values


def validate_invoice(doc, method=None):
    # Lock before inspecting requests so a concurrent queue/save cannot mutate a charged draft.
    from payment_terminal_bridge.api.agent import _lock
    if not doc.is_new():
        _lock(doc.doctype, doc.name)
    request = _latest(doc) if not doc.is_new() else None
    _display(doc, request)  # Do not trust client-supplied readonly status fields.
    if request and request.status not in RETRYABLE:
        if not request.invoice_snapshot:
            frappe.throw("طلب قديم بلا بصمة فاتورة؛ يلزم مراجعته وتسويته قبل تعديل الفاتورة")
        if invoice_snapshot(doc.as_dict()) != request.invoice_snapshot:
            frappe.throw("لا يمكن تغيير بيانات فاتورة لها دفعة مقبولة أو قيد التنفيذ. راجع طلب الدفع الأصلي")


def on_update(doc, method=None):
    if doc.docstatus != 0 or doc.flags.get("terminal_queue_in_progress"):
        return
    mode = _selected_mode(doc)
    route = _route(doc, mode)
    # Automatic dispatch is opt-in per cashier/mode, never a global save side effect.
    if not route or not route.auto_send:
        return
    request = _latest(doc)
    if request:
        return  # Failed/declined requests require an explicit retry, not another Save.
    _queue(doc, route)


def check_accounting_setup(invoice, mode):
    # Check setup before any physical charge, not after the customer has paid.
    if invoice.doctype == "Sales Invoice" and not invoice.get("is_pos"):
        if not all(frappe.has_permission("Payment Entry", ptype=permission) for permission in ("create", "submit")):
            frappe.throw("يلزم صلاحية إنشاء واعتماد Payment Entry لتسوية الفاتورة بعد الدفع", frappe.PermissionError)
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
    account = get_bank_cash_account(mode, invoice.company)
    if not account or not account.get("account"):
        frappe.throw("حدد حساب البنك لطريقة الدفع في الشركة قبل إرسال طلب الجهاز")
    if frappe.db.get_value("Company", invoice.company, "default_currency") != "SAR":
        frappe.throw("التسوية الحالية تتطلب شركة بعملة SAR؛ تعدد العملات يحتاج اعتمادًا منفصلًا")
    if frappe.db.get_value("Account", account["account"], "account_currency") != "SAR":
        frappe.throw("حساب بنك طريقة الدفع يجب أن يكون بعملة SAR")


def _queue(invoice, route):
    from payment_terminal_bridge.api.agent import create_request
    if invoice.docstatus != 0:
        frappe.throw("هذا المسار مخصص لمسودة تنتظر الدفع ثم اعتماد المستخدم")
    try:
        full_payment(invoice.as_dict(), route.payment_mode)
    except ValueError as exc:
        frappe.throw(str(exc))
    result = create_request(agent=route.agent, device_id=route.device_id,
        invoice_type=invoice.doctype, invoice_name=invoice.name,
        payment_mode=route.payment_mode, idempotency_key="invoice:" + uuid.uuid4().hex)
    return result


@frappe.whitelist(methods=["POST"])
def request_payment(invoice_type, invoice_name, retry=0):
    """Shared Desk/POS/mobile endpoint. The caller never supplies a charge amount/device."""
    from payment_terminal_bridge.api.agent import _cashier, _invoice, _lock
    from frappe.utils import cint
    _cashier()
    invoice = _invoice(invoice_type, invoice_name)
    _lock(invoice_type, invoice_name)
    invoice.reload()
    route = _route(invoice, _selected_mode(invoice), required=True)
    current = _latest(invoice)
    if current and (current.status not in RETRYABLE or not cint(retry)):
        return {"request": current.name, "status": current.status}
    return _queue(invoice, route)


def record_result(request):
    if request.operation != "sale":
        return
    invoice = frappe.get_doc(request.invoice_type, request.invoice_name)
    values = _display(invoice, request)
    frappe.db.set_value(invoice.doctype, invoice.name, values, update_modified=False)


def _approved(invoice):
    from payment_terminal_bridge.api.agent import _lock
    _lock(invoice.doctype, invoice.name)
    request = _latest(invoice)
    if not request:
        return None
    _lock(REQUEST, request.name)
    request.reload()
    if request.status != "APPROVED" or not request.terminal_verified:
        frappe.throw("لم يتم تأكيد الدفع على الجهاز؛ لا يمكن اعتماد هذه الفاتورة")
    if not request.invoice_snapshot or invoice_snapshot(invoice.as_dict()) != request.invoice_snapshot:
        frappe.throw("بيانات الفاتورة تغيّرت أو طلب الدفع قديم؛ يلزم مراجعة الدفعة قبل الاعتماد")
    owner = frappe.get_doc("Payment Terminal Agent", request.agent)
    devices = frappe.parse_json(owner.devices_json or "[]")
    device = next((row for row in devices if row.get("device_id") == request.device_id), None)
    if not device or device.get("provider_id") == "mock" or owner.test_mode:
        frappe.throw("لا يجوز ترحيل دفعة محاكاة أو وكيل اختبار محاسبيًا")
    if frappe.db.exists(REQUEST, {"original_request": request.name,
            "status": ["not in", list(RETRYABLE)]}):
        frappe.throw("توجد عملية استرداد أو عكس لهذه الدفعة؛ يلزم التسوية قبل الاعتماد")
    try:
        amount = full_payment(invoice.as_dict(), request.payment_mode)
    except ValueError as exc:
        frappe.throw(str(exc))
    if amount != request.amount_minor:
        frappe.throw("مبلغ الفاتورة لا يطابق الدفعة المؤكدة")
    return request


def before_submit(doc, method=None):
    request = _approved(doc)
    if not request:
        if doc.get("custom_terminal_payment_mode") or _route(doc, _selected_mode(doc)):
            frappe.throw("أرسل الفاتورة إلى جهاز الدفع وانتظر نجاح الدفع قبل الاعتماد")
        return
    if doc.doctype == "POS Invoice" or doc.get("is_pos"):
        for row in doc.payments:
            if row.mode_of_payment == request.payment_mode:
                row.reference_no = request.provider_reference


def on_submit(doc, method=None):
    request = _approved(doc)
    if not request:
        return
    # POS/Sales Invoice with is_pos posts its own payments through ERPNext's controller.
    if doc.doctype == "Sales Invoice" and not doc.get("is_pos"):
        if request.accounting_payment:
            frappe.throw("تم ربط هذه الدفعة بقيد دفع سابق؛ راجع التسوية")
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
        account = get_bank_cash_account(request.payment_mode, doc.company)
        if not account or not account.get("account"):
            frappe.throw("حدد حساب البنك لطريقة الدفع في الشركة قبل الاعتماد")
        payment = get_payment_entry(doc.doctype, doc.name, bank_account=account["account"])
        payment.mode_of_payment = request.payment_mode
        payment.reference_no = request.provider_reference
        payment.reference_date = nowdate()
        payment.remarks = "Terminal request: " + request.name
        from payment_terminal_bridge.policy import minor_units
        if (minor_units(payment.paid_amount) != request.amount_minor or
                minor_units(payment.received_amount) != request.amount_minor or
                sum(minor_units(row.allocated_amount) for row in payment.references) != request.amount_minor):
            frappe.throw("مبلغ سند الدفع لا يطابق مبلغ الجهاز؛ راجع الخصومات والسلف والتسوية")
        # Normal permission checks, GL validation and transaction rollback all apply.
        payment.insert()
        payment.submit()
        request.accounting_payment = payment.name
    request.invoice_submitted = 1
    request.save(ignore_permissions=True)
    from payment_terminal_bridge.api.agent import _publish
    _publish(request)


def before_cancel(doc, method=None):
    from payment_terminal_bridge.api.agent import _lock
    _lock(doc.doctype, doc.name)
    request = _latest(doc)
    if request and request.status not in RETRYABLE:
        frappe.throw("الفاتورة مرتبطة بدفع مقبول أو غير محسوم. سوِّ الاسترداد/العكس محاسبيًا قبل الإلغاء أو الحذف")


@frappe.whitelist()
def invoice_status(invoice_type, invoice_name):
    from payment_terminal_bridge.api.agent import _cashier, _invoice
    _cashier()
    invoice = _invoice(invoice_type, invoice_name)
    request = _latest(invoice)
    route = _route(invoice, _selected_mode(invoice))
    owner = frappe.get_doc("Payment Terminal Agent", request.agent) if request else None
    return {
        "request": request.name if request else None,
        "status": request.status if request else None,
        "terminal_verified": bool(request and request.terminal_verified),
        "provider_reference": request.provider_reference if request else None,
        "can_submit": bool(request and request.status == "APPROVED" and request.terminal_verified and
                           request.invoice_snapshot == invoice_snapshot(invoice.as_dict()) and
                           not owner.test_mode and invoice.docstatus == 0),
        "invoice_submitted": bool(request and request.invoice_submitted),
        "accounting_payment": request.accounting_payment if request else None,
        "routed": bool(route), "auto_send": bool(route and route.auto_send),
    }