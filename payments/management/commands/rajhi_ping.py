# payments/management/commands/rajhi_ping.py
from django.core.management.base import BaseCommand
from django.conf import settings
from urllib.parse import urlencode
import os
import random

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    requests = None  # type: ignore
    HAS_REQUESTS = False

from payments.rajhi_crypto import encrypt_trandata

# بوابة الإنتاج
GATEWAY_URL_PROD = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"

class Command(BaseCommand):
    help = "Ping Al Rajhi gateway (PROD) with a minimal init request and print diagnostics."

    def add_arguments(self, parser):
        parser.add_argument("--amount", default="3.00")

    def handle(self, *args, **opts):
        cfg = settings.RAJHI_CONFIG
        tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        # عنوان الرجوع الأساسي (HTTPS دائمًا)
        base_cb = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
        if not base_cb:
            base_cb = "https://wesh-aljawab.onrender.com"
        if base_cb.startswith("http://"):
            base_cb = "https://" + base_cb[len("http://"):]

        success_url = f"{base_cb}/payments/rajhi/callback/success/"
        fail_url    = f"{base_cb}/payments/rajhi/callback/fail/"

        # trackid رقمي فقط (12 خانة)
        trackid = "".join(random.choices("0123456789", k=12))

        # مفاتيح trandata بحالة الأحرف الصحيحة
        trandata_pairs = {
            "action":       "1",
            "amt":          str(opts["amount"]),
            "currencycode": "682",
            "langid":       "AR",
            "trackid":      trackid,
            "ResponseURL":  success_url,
            "ErrorURL":     fail_url,
            "udf1":         "",  # اختياري: user id
            "udf2":         "",  # اختياري: transaction id
            "udf3":         "",
            "udf4":         "",
            "udf5":         "",
        }

        enc = encrypt_trandata(trandata_pairs)
        self.stdout.write(self.style.SUCCESS("gateway=PROD"))
        self.stdout.write(self.style.SUCCESS(f"trandata_plain={urlencode(trandata_pairs)}"))
        self.stdout.write(self.style.SUCCESS(f"trandata_hex_len={len(enc)}"))

        if not tranportal_id or not tranportal_password:
            self.stdout.write(self.style.ERROR("Missing TRANSPORTAL_ID / TRANSPORTAL_PASSWORD; NOT posting."))
            return

        if not HAS_REQUESTS:
            self.stdout.write(self.style.WARNING("requests غير مثبتة؛ سأتوقف عند بناء البيانات فقط."))
            self.stdout.write(
                f"POST fields would be: tranportalId={tranportal_id}, tranportalPassword=******, "
                f"trandata=<HEX {len(enc)}>, ResponseURL/ErrorURL + responseURL/errorURL"
            )
            return

        try:
            # نرسل الصيغتين لروابط الرجوع (حساسية السيرفلت أحيانًا تختلف)
            post_data = {
                "tranportalId": tranportal_id,
                "tranportalPassword": tranportal_password,
                "trandata": enc,
                "ResponseURL": success_url,
                "ErrorURL": fail_url,
                "responseURL": success_url,
                "errorURL": fail_url,
            }
            resp = requests.post(GATEWAY_URL_PROD, data=post_data, timeout=20)
            self.stdout.write(self.style.SUCCESS(f"POST status={resp.status_code}"))
            self.stdout.write(resp.text[:1200])
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"POST failed: {e}"))
