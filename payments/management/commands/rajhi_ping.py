# payments/management/commands/rajhi_ping.py
from django.core.management.base import BaseCommand
from django.conf import settings
from urllib.parse import urlencode
import os

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    requests = None  # type: ignore
    HAS_REQUESTS = False

from payments.rajhi_crypto import encrypt_trandata

GATEWAY_URL_PROD = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
GATEWAY_URL_UAT  = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"

class Command(BaseCommand):
    help = "Ping Al Rajhi gateway with a minimal init request and print diagnostics."

    def add_arguments(self, parser):
        parser.add_argument("--amount", default="3.00")
        parser.add_argument("--uat", action="store_true")

    def handle(self, *args, **opts):
        cfg = settings.RAJHI_CONFIG
        # استخدم الحقول التاريخية لمسار Servlet: id/password
        merchant_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        merchant_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        use_uat = bool(opts.get("uat"))
        gateway_url = GATEWAY_URL_UAT if use_uat else GATEWAY_URL_PROD

        base_cb = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
        if not base_cb:
            base_cb = "https://wesh-aljawab.onrender.com"  # fallback

        success_url = f"{base_cb}/payments/rajhi/callback/success/"
        fail_url    = f"{base_cb}/payments/rajhi/callback/fail/"

        # داخل trandata: الأسماء Case-Sensitive لمسار Servlet
        trandata_pairs = {
            "action":       "1",
            "amt":          str(opts["amount"]),
            "currencycode": "682",
            "langid":       "AR",
            "trackid":      "PINGTEST",
            "ResponseURL":  success_url,  # R كبيرة
            "ErrorURL":     fail_url,     # E كبيرة
            "udf1":         "",
            "udf2":         "",
            "udf3":         "",
            "udf4":         "",
            "udf5":         "",
        }

        enc = encrypt_trandata(trandata_pairs)
        self.stdout.write(self.style.SUCCESS(f"gateway={'UAT' if use_uat else 'PROD'}"))
        self.stdout.write(self.style.SUCCESS(f"trandata_plain={urlencode(trandata_pairs)}"))
        self.stdout.write(self.style.SUCCESS(f"trandata_hex_len={len(enc)}"))

        if not merchant_id or not merchant_password:
            self.stdout.write(self.style.ERROR("Missing TRANSPORTAL_ID / TRANSPORTAL_PASSWORD; NOT posting."))
            return

        if not HAS_REQUESTS:
            self.stdout.write(self.style.WARNING("requests غير مثبتة؛ سأتوقف عند بناء البيانات فقط."))
            self.stdout.write(
                f"POST fields would be: id={merchant_id}, password=******, "
                f"trandata=<HEX {len(enc)}>, ResponseURL/ErrorURL + responseURL/errorURL"
            )
            return

        try:
            # مهم: نرسل روابط العودة كحقول POST علوية بحالتي الأحرف (احتياط)
            # وكذلك نستعمل حقول Servlet التاريخية: id/password
            post_data = {
                "id": merchant_id,
                "password": merchant_password,
                "trandata": enc,
                # علويًّا: كلا الحالتين تحسّبًا لاختلافات البوابة
                "ResponseURL": success_url,
                "ErrorURL": fail_url,
                "responseURL": success_url,
                "errorURL": fail_url,
            }
            resp = requests.post(gateway_url, data=post_data, timeout=20)
            self.stdout.write(self.style.SUCCESS(f"POST status={resp.status_code}"))
            self.stdout.write(resp.text[:1200])
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"POST failed: {e}"))
