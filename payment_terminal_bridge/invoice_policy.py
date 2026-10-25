"""Pure invoice identity and full-payment checks, shared by hooks and tests."""
import hashlib
import json
from decimal import Decimal

from payment_terminal_bridge.policy import minor_units


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), "f")
    return value


def invoice_snapshot(invoice):
    # Exclude volatile status/modified timestamps and bridge-owned display fields.
    fields = ("doctype", "name", "company", "customer", "currency", "conversion_rate",
              "is_pos", "is_return", "grand_total", "rounded_total", "base_grand_total",
              "discount_amount", "additional_discount_percentage", "apply_discount_on",
              "write_off_amount", "redeem_loyalty_points", "loyalty_amount", "pos_profile",
              "custom_terminal_payment_mode", "debit_to", "cost_center", "project")
    data = {field: invoice.get(field) for field in fields}
    for table in ("items", "taxes", "payments", "advances"):
        # Preserve all business fields; strip framework bookkeeping and terminal reference.
        rows = invoice.get(table) or []
        if table == "payments":
            rows = [row for row in rows if Decimal(str(row.get("amount") or 0)) != 0]
        data[table] = [{key: value for key, value in dict(row).items() if key not in {
            "name", "parent", "parenttype", "parentfield", "creation", "modified", "modified_by",
            "owner", "docstatus", "doctype", "reference_no", "__islocal", "__unsaved",
        } and not key.startswith("_")} for row in rows]
    return hashlib.sha256(json.dumps(_canonical(data), sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def full_payment(invoice, mode):
    if invoice.get("currency") != "SAR" or invoice.get("is_return"):
        raise ValueError("الدفع الآلي يدعم فاتورة بيع غير مرتجعة وبعملة SAR فقط")
    amount = minor_units(invoice.get("rounded_total") or invoice.get("grand_total"))
    if invoice.get("advances") or any(Decimal(str(invoice.get(key) or 0)) != 0 for key in (
        "write_off_amount", "loyalty_amount", "redeem_loyalty_points",
    )):
        raise ValueError("السلف والولاء والشطب تحتاج تسوية منفصلة؛ لم يُرسل طلب دفع")
    positive = [row for row in invoice.get("payments") or [] if Decimal(str(row.get("amount") or 0)) != 0]
    is_pos = invoice.get("doctype") == "POS Invoice" or invoice.get("is_pos")
    if is_pos or positive:
        if len(positive) != 1 or positive[0].get("mode_of_payment") != mode or minor_units(positive[0].get("amount")) != amount:
            raise ValueError("يلزم دفع كامل الفاتورة بطريقة شبكة واحدة؛ الدفع الجزئي أو المختلط غير مدعوم بعد")
    return amount