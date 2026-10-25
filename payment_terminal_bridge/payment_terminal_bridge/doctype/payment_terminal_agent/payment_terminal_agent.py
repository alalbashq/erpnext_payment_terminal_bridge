import json
import re

import frappe
from frappe.model.document import Document


class PaymentTerminalAgent(Document):
    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,80}", self.name or ""):
            frappe.throw("Agent name must be 3-80 ASCII letters, digits, dots, underscores or hyphens")
        if self.agent_user in {"Guest", "Administrator"}:
            frappe.throw("Use a dedicated non-administrator agent user")
        if self.enabled and (not frappe.db.get_value("User", self.agent_user, "enabled") or
                             "Payment Terminal Agent" not in frappe.get_roles(self.agent_user)):
            frappe.throw("Enabled agent requires an enabled user with the Payment Terminal Agent role")
        try:
            devices = json.loads(self.devices_json or "[]")
        except (ValueError, TypeError):
            frappe.throw("Invalid devices JSON")
        if not isinstance(devices, list) or len(devices) > 100:
            frappe.throw("Devices must be a list of at most 100 entries")
        seen = set()
        for device in devices:
            if not isinstance(device, dict) or set(device) - {"device_id", "provider_id", "terminal_id", "enabled", "payment_mode"}:
                frappe.throw("Invalid device registry fields")
            for field in ("device_id", "provider_id", "terminal_id", "payment_mode"):
                value = device.get(field)
                if not isinstance(value, str) or not value or len(value) > 80 or any(ord(c) < 32 for c in value):
                    frappe.throw("Invalid device identity")
            if type(device.get("enabled")) is not bool or device["device_id"] in seen:
                frappe.throw("Device IDs must be unique and enabled must be boolean")
            if device["provider_id"] == "mock" and not self.test_mode:
                frappe.throw("Mock devices require test mode")
            seen.add(device["device_id"])