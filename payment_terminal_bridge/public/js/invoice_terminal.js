for (const doctype of ["Sales Invoice", "POS Invoice"]) {
  frappe.ui.form.on(doctype, {
    refresh(frm) {
      if (frm._terminal_unwatch) frm._terminal_unwatch();
      frm._terminal_unwatch = null;
      if (frm.is_new() || frm.doc.docstatus === 2) return;
      const labels = {
        QUEUED: "بانتظار تطبيق الدفع", REQUEST_SENT: "أُرسل إلى جهاز الدفع",
        PROCESSING: "الدفع جارٍ — لا تُعد العملية", UNKNOWN: "النتيجة غير محسومة — استعلم عن العملية الأصلية",
        MANUAL_REVIEW: "تحتاج مراجعة — لا تُعد الخصم", APPROVED: "تم الدفع على الجهاز — بانتظار اعتماد الفاتورة",
        DECLINED: "رُفض الدفع", CANCELLED: "أُلغي الطلب", FAILED: "تعذر بدء الدفع",
      };
      const invoiceName = frm.doc.name;
      frm._terminal_unwatch = payment_terminal_bridge.watch(doctype, invoiceName, (state) => {
        if (frm.doc.name !== invoiceName || cur_frm !== frm) {
          if (frm._terminal_unwatch) frm._terminal_unwatch();
          return;
        }
        frm.remove_custom_button("إرسال إلى جهاز الدفع");
        frm.remove_custom_button("إعادة محاولة الدفع");
        frm.remove_custom_button("اعتماد الفاتورة بعد الدفع");
        if (state.status) frm.dashboard.set_headline_alert(
          state.invoice_submitted ? "تم اعتماد الفاتورة بعد الدفع" : labels[state.status] || "راجع حالة طلب الدفع"
        );
        if (frm.doc.docstatus !== 0) return;
        if (state.routed && (!state.request || ["DECLINED", "CANCELLED", "FAILED"].includes(state.status))) {
          frm.add_custom_button(state.request ? "إعادة محاولة الدفع" : "إرسال إلى جهاز الدفع", () => {
            if (frm.is_dirty()) {
              frappe.msgprint("احفظ بيانات الفاتورة أولًا ثم أرسل طلب الدفع");
              return;
            }
            frappe.confirm("سيُرسل مبلغ الفاتورة إلى جهاز الشبكة المرتبط بمستخدمك. هل تريد المتابعة؟", async () => {
              await payment_terminal_bridge.request(doctype, invoiceName, !!state.request);
              await frm.reload_doc();
            });
          });
        }
        if (state.can_submit && frappe.model.can_submit(doctype)) {
          frm.add_custom_button("اعتماد الفاتورة بعد الدفع", () => {
            if (frm.is_dirty()) {
              frappe.msgprint("أعد تحميل الفاتورة؛ لا تغيّر بيانات فاتورة تم دفعها");
              return;
            }
            frappe.confirm("تم الدفع على الجهاز. هل تريد اعتماد الفاتورة وترحيلها الآن؟", () => frm.save("Submit"));
          });
        }
      });
    },
  });
}