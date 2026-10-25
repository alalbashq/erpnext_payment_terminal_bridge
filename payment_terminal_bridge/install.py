"""Idempotent provisioning; assigning the role to users is an administrator action."""
import frappe


def ensure_roles():
    if not frappe.db.exists("Role", "Payment Terminal Agent"):
        frappe.get_doc({"doctype": "Role", "role_name": "Payment Terminal Agent", "desk_access": 0}).insert(ignore_permissions=True)
    # before_install can run before ERPNext metadata is available.
    if not frappe.db.exists("DocType", "Sales Invoice"):
        return
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    fields = [
        {"fieldname": "custom_terminal_section", "label": "الدفع على جهاز الشبكة", "fieldtype": "Section Break", "insert_after": "payments"},
        {"fieldname": "custom_terminal_payment_mode", "label": "طريقة دفع جهاز الشبكة", "fieldtype": "Link", "options": "Mode of Payment", "no_copy": 1},
        {"fieldname": "custom_terminal_request", "label": "طلب جهاز الشبكة", "fieldtype": "Link", "options": "Payment Terminal Request", "read_only": 1, "no_copy": 1, "allow_on_submit": 1},
        {"fieldname": "custom_terminal_status", "label": "حالة الدفع على الجهاز", "fieldtype": "Data", "read_only": 1, "no_copy": 1, "allow_on_submit": 1},
        {"fieldname": "custom_terminal_reference", "label": "مرجع الدفع على الجهاز", "fieldtype": "Data", "read_only": 1, "no_copy": 1, "allow_on_submit": 1},
    ]
    create_custom_fields({doctype: fields for doctype in ("Sales Invoice", "POS Invoice")
                          if frappe.db.exists("DocType", doctype)})