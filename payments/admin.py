# payments/admin.py
from decimal import Decimal
from datetime import date, datetime
from calendar import monthrange

from django.contrib import admin, messages
from django.db.models import Sum, Count, Q
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django import forms

from .models import (
    PaymentMethod,
    Transaction,
    Discount,
    Invoice,
)

# -----------------------------
# دعم حقول/موديلات اختيارية لو موجودة
# -----------------------------
HAS_PERCENTAGE_FEE = hasattr(PaymentMethod, "percentage_fee")

OperationalCost = None
FinanceSettings = None
try:
    from .models import OperationalCost as _OperationalCost  # type: ignore
    OperationalCost = _OperationalCost
except Exception:
    pass

try:
    from .models import FinanceSettings as _FinanceSettings  # type: ignore
    FinanceSettings = _FinanceSettings
except Exception:
    pass


def _listing_table(headers, rows_html):
    """
    يبني جدول HTML قابل لإعادة الاستخدام.
    يتحمّل:
      - rows_html كـ list[str]
      - rows_html كـ str
      - rows_html كـ callable يرجّع str أو list[str]
    ويتفادى TypeError لما يمرّر بالخطأ “ميثود” بدل ناتجها.
    """
    # لو انمررت دالة (callable) استدعها أولاً
    if callable(rows_html):
        rows_html = rows_html()

    # جهّز جسم الجدول
    if isinstance(rows_html, str):
        body = rows_html
    else:
        try:
            body = "".join(rows_html)
        except TypeError:
            # آخر علاج: حوّله نص مباشرة (لمنع كراش ولو كان إدخال خاطئ)
            body = str(rows_html)

    # رؤوس الجدول
    head = "".join(
        f"<th style='padding:10px 12px;text-align:right;border-bottom:1px solid #1f2937;'>{h}</th>"
        for h in headers
    )

    # صف افتراضي لو ما فيه بيانات
    if not body:
        body = (
            f"<tr><td colspan='{len(headers)}' "
            f"style='padding:12px;color:#94a3b8;'>لا توجد بيانات</td></tr>"
        )

    # الغلاف
    return f"""
    <div class="module" style="margin:12px 0;border-radius:12px;overflow:hidden;">
      <table class="listing" style="width:100%;border-collapse:collapse;background:#0b1220;">
        <thead style="background:#0f172a;color:#cbd5e1;">{head}</thead>
        <tbody style="color:#e2e8f0;">{body}</tbody>
      </table>
    </div>
    """

# -----------------------------
# افتراضات مرنة وقابلة للتعديل من صفحة الإحصائيات
# -----------------------------
DEFAULT_PLATFORM_PER_TX_SAR = Decimal("1.00")  # 1 ريال لكل عملية
DEFAULT_USD_TO_SAR = Decimal("3.75")
DEFAULT_MONTHLY_SAR = Decimal("90.00")
DEFAULT_MONTHLY_USD = Decimal("7.00")

def _usd_to_sar_rate() -> Decimal:
    if FinanceSettings:
        try:
            obj = FinanceSettings.objects.first()
            if obj and obj.usd_to_sar and Decimal(obj.usd_to_sar) > 0:
                return Decimal(obj.usd_to_sar)
        except Exception:
            pass
    return DEFAULT_USD_TO_SAR

def _guess_percent_for_method(pm: PaymentMethod) -> Decimal:
    """لو ما عندك percentage_fee بالموديل، نقدّر النسبة بشكل منطقي."""
    if HAS_PERCENTAGE_FEE:
        try:
            if pm.percentage_fee is not None:
                return Decimal(pm.percentage_fee)
        except Exception:
            pass
    name = (pm.name or pm.name_ar or "").lower()
    if "mada" in name or "مدى" in name:
        return Decimal("1.0")
    if "visa" in name or "ماستر" in name or "master" in name or "فيزا" in name:
        return Decimal("2.7")
    return Decimal("0.0")

def _to_sar(amount: Decimal, currency: str, usd_rate: Decimal) -> Decimal:
    currency = (currency or "SAR").upper()
    if currency == "USD":
        return (amount or Decimal("0")) * usd_rate
    return Decimal(amount or Decimal("0"))

# -----------------------------
# نموذج التحكم (فلاتر + تكاليف ديناميكية للصفحة)
# يدعم: شهري SAR + شهري USD + ريال لكل عملية + 5 مبالغ مقطوعة بعملة مستقلة
# -----------------------------
CURRENCY_CHOICES = (("SAR", "SAR"), ("USD", "USD"))

class FinanceControlForm(forms.Form):
    # الفترة
    date_from = forms.DateField(label="من", required=False)
    date_to = forms.DateField(label="إلى", required=False)

    # التكاليف الشهرية
    monthly_sar = forms.DecimalField(label="تكلفة شهرية (SAR)", required=False, min_value=0)
    monthly_usd = forms.DecimalField(label="تكلفة شهرية (USD)", required=False, min_value=0)
    usd_to_sar = forms.DecimalField(label="سعر الصرف USD→SAR", required=False, min_value=0)

    # تكلفة لكل عملية (ثابتة)
    per_tx_platform_sar = forms.DecimalField(label="تكلفة المنصة لكل عملية (SAR)", required=False, min_value=0)

    # مبالغ مقطوعة (حتى 5)
    one_time_1_name = forms.CharField(label="مقطوع 1 - المذكرة", required=False)
    one_time_1_amount = forms.DecimalField(label="مقطوع 1 - المبلغ", required=False, min_value=0)
    one_time_1_currency = forms.ChoiceField(label="مقطوع 1 - العملة", choices=CURRENCY_CHOICES, required=False)

    one_time_2_name = forms.CharField(label="مقطوع 2 - المذكرة", required=False)
    one_time_2_amount = forms.DecimalField(label="مقطوع 2 - المبلغ", required=False, min_value=0)
    one_time_2_currency = forms.ChoiceField(label="مقطوع 2 - العملة", choices=CURRENCY_CHOICES, required=False)

    one_time_3_name = forms.CharField(label="مقطوع 3 - المذكرة", required=False)
    one_time_3_amount = forms.DecimalField(label="مقطوع 3 - المبلغ", required=False, min_value=0)
    one_time_3_currency = forms.ChoiceField(label="مقطوع 3 - العملة", choices=CURRENCY_CHOICES, required=False)

    one_time_4_name = forms.CharField(label="مقطوع 4 - المذكرة", required=False)
    one_time_4_amount = forms.DecimalField(label="مقطوع 4 - المبلغ", required=False, min_value=0)
    one_time_4_currency = forms.ChoiceField(label="مقطوع 4 - العملة", choices=CURRENCY_CHOICES, required=False)

    one_time_5_name = forms.CharField(label="مقطوع 5 - المذكرة", required=False)
    one_time_5_amount = forms.DecimalField(label="مقطوع 5 - المبلغ", required=False, min_value=0)
    one_time_5_currency = forms.ChoiceField(label="مقطوع 5 - العملة", choices=CURRENCY_CHOICES, required=False)

    def initial_with_defaults(self):
        today = timezone.localdate()
        start_month = date(today.year, today.month, 1)
        return {
            "date_from": start_month,
            "date_to": today,
            "monthly_sar": DEFAULT_MONTHLY_SAR,
            "monthly_usd": DEFAULT_MONTHLY_USD,
            "usd_to_sar": _usd_to_sar_rate(),
            "per_tx_platform_sar": DEFAULT_PLATFORM_PER_TX_SAR,
            "one_time_1_currency": "SAR",
            "one_time_2_currency": "SAR",
            "one_time_3_currency": "SAR",
            "one_time_4_currency": "SAR",
            "one_time_5_currency": "SAR",
        }

    def parse_one_time_items(self) -> list[dict]:
        items = []
        for i in range(1, 6):
            name = self.cleaned_data.get(f"one_time_{i}_name") or ""
            amount = self.cleaned_data.get(f"one_time_{i}_amount")
            currency = self.cleaned_data.get(f"one_time_{i}_currency") or "SAR"
            if amount and amount > 0:
                items.append({"name": name.strip() or f"مقطوع {i}", "amount": Decimal(amount), "currency": currency})
        return items

# -----------------------------
# حساب الإحصائيات المالية
# - يدعم المبالغ الشهرية + المقطوعة + تكلفة لكل عملية
# - رسوم البوابة = نسبة حسب الطريقة + processing_fee لكل عملية (من PaymentMethod)
# -----------------------------
def _months_overlap_count(d1: date, d2: date) -> int:
    """عدد الأشهر (سنة/شهر مميزة) ضمن الفترة."""
    y1, m1 = d1.year, d1.month
    y2, m2 = d2.year, d2.month
    return (y2 - y1) * 12 + (m2 - m1) + 1

def compute_financials(txns, *, usd_rate: Decimal, monthly_sar: Decimal, monthly_usd: Decimal,
                       per_tx_platform_sar: Decimal, one_time_items: list[dict],
                       date_from: date, date_to: date):
    # إجمالي الدخل (بالريال)
    gross_sar = Decimal("0.0")
    gateway_fees_sar = Decimal("0.0")
    per_tx_platform_total = Decimal("0.0")

    for t in txns:
        amt_sar = _to_sar(t.amount, t.currency, usd_rate)
        gross_sar += amt_sar

        # رسوم البوابة لطريقة الدفع
        pm = t.payment_method
        perc = _guess_percent_for_method(pm) if pm else Decimal("0")
        perc_fee = (amt_sar * perc) / Decimal("100.0")
        flat_fee = Decimal(pm.processing_fee) if (pm and pm.processing_fee) else Decimal("0.0")
        gateway_fees_sar += (perc_fee + flat_fee)

        # تكلفة المنصة لكل عملية
        per_tx_platform_total += per_tx_platform_sar

    # تكاليف شهرية (نحسبها بعدد الأشهر المغطّاة في الفلتر)
    monthly_total_one_month = (monthly_sar or 0) + _to_sar((monthly_usd or 0), "USD", usd_rate)
    months_count = _months_overlap_count(date_from, date_to)
    monthly_applied = monthly_total_one_month * months_count

    # مبالغ مقطوعة (تُضاف كما هي)
    one_time_total = Decimal("0.0")
    one_time_breakdown = []
    for item in one_time_items:
        item_sar = _to_sar(item["amount"], item["currency"], usd_rate)
        one_time_total += item_sar
        one_time_breakdown.append({"name": item["name"], "amount_sar": item_sar})

    total_costs = gateway_fees_sar + per_tx_platform_total + monthly_applied + one_time_total
    net_sar = gross_sar - total_costs

    return {
        "gross_sar": gross_sar,
        "gateway_fees_sar": gateway_fees_sar,
        "per_tx_platform_total": per_tx_platform_total,
        "monthly_applied_sar": monthly_applied,
        "one_time_total_sar": one_time_total,
        "one_time_breakdown": one_time_breakdown,
        "net_sar": net_sar,
        "months_count": months_count,
    }

# -----------------------------
# PaymentMethod Admin
# -----------------------------
@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "name", "is_active", "processing_fee", "percent_display", "used_count")
    list_filter = ("is_active",)
    search_fields = ("name", "name_ar")

    def used_count(self, obj):
        return Transaction.objects.filter(payment_method=obj).count()
    used_count.short_description = "عدد الاستخدام"

    def percent_display(self, obj):
        v = _guess_percent_for_method(obj)
        return f"{v}%"
    percent_display.short_description = "نسبة العمولة"

# -----------------------------
# Transaction Admin + صفحة الإحصائيات
# -----------------------------
@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("short_id", "user", "package_ref", "amount_currency", "method_badge", "status_badge", "created_at", "completed_at")
    list_filter = ("status", "currency", "payment_method", "created_at")
    search_fields = ("id", "user__username", "package__package_number", "gateway_transaction_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def short_id(self, obj):
        return str(obj.id)[:8]
    short_id.short_description = "ID"

    def package_ref(self, obj):
        try:
            return f"{obj.package.get_game_type_display()} / حزمة {obj.package.package_number}"
        except Exception:
            return "—"
    package_ref.short_description = "الحزمة"

    def amount_currency(self, obj):
        return f"{obj.amount} {obj.currency}"
    amount_currency.short_description = "المبلغ"

    def method_badge(self, obj):
        if not obj.payment_method:
            return "—"
        return format_html(
            '<span style="background:#111827;color:#93c5fd;border:1px solid #1f2937;padding:2px 8px;border-radius:999px;">{}</span>',
            obj.payment_method.name_ar or obj.payment_method.name
        )
    method_badge.short_description = "طريقة الدفع"

    def status_badge(self, obj):
        colors = {
            "completed": ("#dcfce7", "#166534"),
            "pending": ("#fef9c3", "#92400e"),
            "processing": ("#e0f2fe", "#075985"),
            "failed": ("#fee2e2", "#7f1d1d"),
            "cancelled": ("#e5e7eb", "#374151"),
            "refunded": ("#f3e8ff", "#6b21a8"),
        }
        bg, fg = colors.get(obj.status, ("#e5e7eb", "#111827"))
        return format_html('<span style="background:{};color:{};padding:2px 8px;border-radius:12px;">{}</span>', bg, fg, obj.get_status_display())
    status_badge.short_description = "الحالة"

    # رابط الإحصائيات
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("finance-dashboard/", self.admin_site.admin_view(self.finance_dashboard), name="payments_finance_dashboard"),
        ]
        return my + urls

    def finance_dashboard(self, request):
        ctx = {**self.admin_site.each_context(request)}
        form = FinanceControlForm(request.GET or None)
        if not form.is_valid():
            # حط افتراضات أولية
            form = FinanceControlForm(initial=FinanceControlForm().initial_with_defaults())
        cleaned = form.cleaned_data if form.is_bound and form.is_valid() else form.initial

        # قرّاءه القيم
        d_from = cleaned.get("date_from") or FinanceControlForm().initial_with_defaults()["date_from"]
        d_to = cleaned.get("date_to") or FinanceControlForm().initial_with_defaults()["date_to"]
        usd_rate = Decimal(cleaned.get("usd_to_sar") or _usd_to_sar_rate())
        monthly_sar = Decimal(cleaned.get("monthly_sar") or DEFAULT_MONTHLY_SAR)
        monthly_usd = Decimal(cleaned.get("monthly_usd") or DEFAULT_MONTHLY_USD)
        per_tx_platform = Decimal(cleaned.get("per_tx_platform_sar") or DEFAULT_PLATFORM_PER_TX_SAR)
        one_time_items = form.parse_one_time_items() if form.is_bound else []

        # المعاملات المكتملة ضمن الفترة
        qs = Transaction.objects.filter(
            status="completed",
            completed_at__date__gte=d_from,
            completed_at__date__lte=d_to
        ).select_related("payment_method")

        tx_count = qs.count()
        buyers = (qs.values("user__username")
                    .annotate(total=Sum("amount"))
                    .order_by("-total")[:10])
        by_method = (qs.values("payment_method__name_ar", "payment_method__name")
                       .annotate(total=Sum("amount"), cnt=Count("id"))
                       .order_by("-total"))

        # الحسابات
        fin = compute_financials(
            qs,
            usd_rate=usd_rate,
            monthly_sar=monthly_sar,
            monthly_usd=monthly_usd,
            per_tx_platform_sar=per_tx_platform,
            one_time_items=one_time_items,
            date_from=d_from,
            date_to=d_to
        )

        # عرض أنيق متناسق مع ثيم الأدمن
        def _sar(v: Decimal) -> str:
            return f"{v.quantize(Decimal('0.01'))} ﷼"

        one_time_rows = "".join(
            f"<tr><td>{i['name']}</td><td style='text-align:right'>{_sar(i['amount_sar'])}</td></tr>"
            for i in fin["one_time_breakdown"]
        ) or "<tr><td colspan='2' style='text-align:center;color:#6b7280'>لا مبالغ مقطوعة</td></tr>"

        method_rows = "".join(
            f"<tr><td>{(m['payment_method__name_ar'] or m['payment_method__name'] or '—')}</td>"
            f"<td style='text-align:center'>{m['cnt']}</td>"
            f"<td style='text-align:right'>{_sar(_to_sar(Decimal(m['total'] or 0), 'SAR', usd_rate))}</td></tr>"
            for m in by_method
        ) or "<tr><td colspan='3' style='text-align:center;color:#6b7280'>لا بيانات</td></tr>"

        buyers_rows = "".join(
            f"<tr><td>{b['user__username']}</td>"
            f"<td style='text-align:right'>{_sar(_to_sar(Decimal(b['total'] or 0), 'SAR', usd_rate))}</td></tr>"
            for b in buyers
        ) or "<tr><td colspan='2' style='text-align:center;color:#6b7280'>لا بيانات</td></tr>"

        ctx.update({
            "title": "لوحة مالية — المدفوعات",
            "form": form,
            "d_from": d_from,
            "d_to": d_to,
            "usd_rate": usd_rate,
            "cards": [
                ("إجمالي الدخل", _sar(fin["gross_sar"])),
                ("رسوم البوابات", _sar(fin["gateway_fees_sar"])),
                ("تكلفة المنصة (لكل عملية)", _sar(fin["per_tx_platform_total"])),
                (f"التكاليف الشهرية × {fin['months_count']}", _sar(fin["monthly_applied_sar"])),
                ("مبالغ مقطوعة", _sar(fin["one_time_total_sar"])),
                ("صافي الربح", _sar(fin["net_sar"])),
            ],
            "tx_count": tx_count,
            "method_rows": method_rows,
            "buyers_rows": buyers_rows,
            "one_time_rows": one_time_rows,
        })

        return TemplateResponse(request, "admin/payments_finance_dashboard.html", ctx)

# -----------------------------
# Discount / Invoice Admins (مبسطة)
# -----------------------------
@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("code", "description", "discount_type", "discount_value", "is_active", "valid_from", "valid_until", "used_count", "max_uses")
    list_filter = ("discount_type", "is_active", "valid_from", "valid_until")
    search_fields = ("code", "description")

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "transaction", "customer_name", "total_amount", "created_at")
    search_fields = ("invoice_number", "customer_name", "transaction__id")
    date_hierarchy = "created_at"
