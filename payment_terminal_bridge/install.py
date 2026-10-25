"""Idempotent provisioning; assigning the role to users is an administrator action."""
import frappe


def ensure_roles():
    if not frappe.db.exists("Role", "Payment Terminal Agent"):
        frappe.get_doc({"doctype": "Role", "role_name": "Payment Terminal Agent", "desk_access": 0}).insert(ignore_permissions=True)