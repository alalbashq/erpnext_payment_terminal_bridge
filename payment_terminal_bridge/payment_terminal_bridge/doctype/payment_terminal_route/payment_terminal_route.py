import hashlib
import json

import frappe
from frappe.model.document import Document


class PaymentTerminalRoute(Document):
    def validate(self):
        self.route_key = hashlib.sha256(json.dumps(
            [self.cashier, self.company, self.payment_mode], separators=(",", ":")
        ).encode()).hexdigest()
        if self.cashier in {"Guest", "Administrator"}:
            frappe.throw("اختر مستخدم كاشير مخصصًا وليس Administrator أو Guest")
        owner = frappe.get_doc("Payment Terminal Agent", self.agent)
        if owner.company != self.company:
            frappe.throw("شركة الوكيل لا تطابق شركة مسار الدفع")
        devices = frappe.parse_json(owner.devices_json or "[]")
        device = next((row for row in devices if row.get("device_id") == self.device_id), None)
        if not device or device.get("payment_mode") != self.payment_mode:
            frappe.throw("الجهاز وطريقة الدفع يجب أن يطابقا قائمة الأجهزة المعتمدة للوكيل")
        mode = frappe.get_doc("Mode of Payment", self.payment_mode)
        if not mode.enabled or mode.type != "Bank":
            frappe.throw("مسار الجهاز يتطلب طريقة دفع بنكية مفعّلة")
        if self.enabled and (not owner.enabled or not device.get("enabled") or
                             not frappe.db.get_value("User", self.cashier, "enabled")):
            frappe.throw("فعّل مستخدم الكاشير والوكيل والجهاز قبل تفعيل المسار")