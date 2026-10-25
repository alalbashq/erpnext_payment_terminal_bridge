from frappe.model.document import Document


class PaymentTerminalRequest(Document):
    """No direct create/write permission: mutations go through authenticated endpoints."""