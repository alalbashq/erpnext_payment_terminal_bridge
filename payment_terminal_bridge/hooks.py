app_name = "payment_terminal_bridge"
app_title = "Payment Terminal Bridge"
app_publisher = "Payment Terminal Bridge contributors"
app_description = "Durable requests for Windows payment terminals"
app_email = ""
app_license = "MIT"
required_apps = ["erpnext"]
before_install = "payment_terminal_bridge.install.ensure_roles"
after_migrate = "payment_terminal_bridge.install.ensure_roles"
after_install = "payment_terminal_bridge.install.ensure_roles"

doctype_js = {
    "Sales Invoice": "public/js/invoice_terminal.js",
    "POS Invoice": "public/js/invoice_terminal.js",
}
app_include_js = ["/assets/payment_terminal_bridge/js/terminal_client.js"]

doc_events = {
    doctype: {
        "validate": "payment_terminal_bridge.invoice_flow.validate_invoice",
        "on_update": "payment_terminal_bridge.invoice_flow.on_update",
        "before_submit": "payment_terminal_bridge.invoice_flow.before_submit",
        "on_submit": "payment_terminal_bridge.invoice_flow.on_submit",
        "before_cancel": "payment_terminal_bridge.invoice_flow.before_cancel",
        "on_trash": "payment_terminal_bridge.invoice_flow.before_cancel",
    }
    for doctype in ("Sales Invoice", "POS Invoice")
}