# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import time
import uuid
import urllib.parse
import requests

from django.core.management.base import BaseCommand
from django.conf import settings

from payments.rajhi_crypto import encrypt_trandata


def _get_base_url() -> str:
    """
    نحاول أخذ الدومين من الـ ENV، ثم من الإعدادات إن وُجدت،
    وإلا نستخدم دومين الإنتاج لديك.
    """
    return (
        os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")


class Command(BaseCommand):
    help = "Ping Al Rajhi PG (creates a hosted payment init) with proper trandata composition."

    def add_arguments(self, parser):
        parser.add_argument(
            "--amount",
            type=float,
            default=3.00,
            help="Amount to test with (e.g. 3.00)",
        )
        parser.add_argument(
            "--env",
            type=str,
            default="prod",
            choices=["prod", "uat"],
            help="Which gateway to hit (prod|uat). Default: prod",
        )

    # خرائط بوابة الراجحي
    GATEWAYS = {
        "prod": "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet",
        "uat": "https://uat3ds.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet",
    }

    def handle(self, *args, **options):
        amount = options["amount"]
        env = options["env"].lower()
        gateway = self.GATEWAYS.get(env, self.GATEWAYS["prod"])

        cfg = getattr(settings, "RAJHI_CONFIG", {})
        merchant_id = cfg.get("MERCHANT_ID")
        terminal_id = cfg.get("TERMINAL_ID")
        transportal_id = cfg.get("TRANSPORTAL_ID")
        transportal_password = cfg.get("TRANSPORTAL_PASSWORD")

        if not all([merchant_id, terminal_id, transportal_id, transportal_password]):
            self.stderr.write(self.style.ERROR("RAJHI_CONFIG ناقصة. تحقق من: MERCHANT_ID/TERMINAL_ID/TRANSPORTAL_ID/TRANSPORTAL_PASSWORD"))
            sys.exit(1)

        base_url = _get_base_url()
        success_url = urllib.parse.urljoin(base_url + "/", "payments/rajhi/callback/success/")
        error_url = urllib.parse.urljoin(base_url + "/", "payments/rajhi/callback/fail/")

        # نبني trandata فقط بالحقول الأساسية (بدون الروابط)
        track_id = f"{int(time.time()*1000)}{uuid.uuid4().hex[:4]}"
        amount_str = f"{amount:.2f}"

        trandata_pairs = {
            # IMPORTANT: أسماء الحقول حساسة للبوابة
            "action": "1",
            "amt": amount_str,
            "currencycode": "682",
            "langid": "AR",
            "trackid": track_id,
            # UDFs اختيارية
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        # تشفير trandata (AES حسب إعداداتك)
        enc = encrypt_trandata(trandata_pairs)
        self.stdout.write(f"gateway={env.upper()}")
        plain_str = "&".join([f"{k}={urllib.parse.quote(v, safe='')}" for k, v in trandata_pairs.items() if v is not None])
        self.stdout.write(f"trandata_plain={plain_str}")
        self.stdout.write(f"trandata_hex_len={len(enc)}")

        # IMPORTANT: الروابط تُرسل خارج trandata
        payload = {
            "id": transportal_id,
            "password": transportal_password,
            "trandata": enc,
            "responseURL": success_url,  # خارج التشفير
            "errorURL": error_url,       # خارج التشفير
        }

        # إرسال الطلب
        try:
            resp = requests.post(gateway, data=payload, timeout=30)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"POST error: {e}"))
            sys.exit(1)

        self.stdout.write(f"POST status={resp.status_code}")
        # نطبع جزء من الاستجابة للتشخيص
        body = (resp.text or "").strip()
        self.stdout.write(body[:2000])
        self.stdout.write(self.style.SUCCESS("Done."))
