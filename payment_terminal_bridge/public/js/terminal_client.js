/* Shared Desk/POS client. Mobile clients use the same authenticated REST methods.
 * A socket event is only an invalidation hint: always read authoritative state.
 */
frappe.provide("payment_terminal_bridge");
payment_terminal_bridge.request = async function (doctype, name, retry = false) {
  const result = await frappe.call({
    method: "payment_terminal_bridge.invoice_flow.request_payment",
    args: { invoice_type: doctype, invoice_name: name, retry: retry ? 1 : 0 },
  });
  return result.message;
};
payment_terminal_bridge.watch = function (doctype, name, callback) {
  let active = true;
  let loading = false;
  const refresh = async () => {
    if (!active || loading) return;
    loading = true;
    try {
      const result = await frappe.call({
        method: "payment_terminal_bridge.invoice_flow.invoice_status",
        args: { invoice_type: doctype, invoice_name: name },
      });
      if (active) callback(result.message);
    } catch (error) {
      // Keep the watcher alive after disconnect; never infer a payment outcome.
      console.warn("Terminal status unavailable; waiting for reconnection");
    } finally { loading = false; }
  };
  const handler = (event) => {
    if (event.invoice_type === doctype && event.invoice_name === name) void refresh();
  };
  frappe.realtime.on("payment_terminal_update", handler);
  const timer = setInterval(() => { void refresh(); }, 15000);
  void refresh();
  return () => {
    active = false;
    clearInterval(timer);
    frappe.realtime.off("payment_terminal_update", handler);
  };
};