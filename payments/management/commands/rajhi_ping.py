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

GATEWAY_URL = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"

class Command(BaseCommand):
    help = "Ping Al Rajhi gateway with a minimal init request and print diagnostics."

    def add_arguments(self, parser):
        parser.add_argument("--amount", default="3.00")
        parser.add_argument("--uat", action="store_true")

    def handle(self, *args, **opts):
        cfg = settings.RAJHI_CONFIG
        tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        base_cb = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
        if not base_cb:
            base_cb = "https://wesh-aljawab.onrender.com"  # fallback معقول

        success_url = f"{base_cb}/payments/rajhi/callback/success/"
        fail_url    = f"{base_cb}/payments/rajhi/callback/fail/"

        trandata_pairs = {
            "action": "1",
            "amt": str(opts["amount"]),
            "currencycode": "682",
            "langid": "AR",
            "trackid": "PINGTEST",
            "responseURL": success_url,  # ← lowercase key
            "errorURL": fail_url,        # ← lowercase key
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        enc = encrypt_trandata(trandata_pairs)
        self.stdout.write(self.style.SUCCESS(f"trandata_plain={urlencode(trandata_pairs)}"))
        self.stdout.write(self.style.SUCCESS(f"trandata_hex_len={len(enc)}"))

        if not tranportal_id or not tranportal_password:
            self.stdout.write(self.style.ERROR("Missing id/password; NOT posting."))
            return

        if not HAS_REQUESTS:
            self.stdout.write(self.style.WARNING("requests غير مثبتة؛ سأتوقف عند بناء البيانات فقط."))
            self.stdout.write(f"POST fields would be: tranportalId={tranportal_id}, tranportalPassword=******, trandata=<HEX {len(enc)}>")
            return

        try:
            resp = requests.post(GATEWAY_URL, data={
                "tranportalId": tranportal_id,            # ← key as expected by Rajhi
                "tranportalPassword": tranportal_password, # ← key as expected by Rajhi
                "trandata": enc,
            }, timeout=20)
            self.stdout.write(self.style.SUCCESS(f"POST status={resp.status_code}"))
            self.stdout.write(resp.text[:500])
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"POST failed: {e}"))