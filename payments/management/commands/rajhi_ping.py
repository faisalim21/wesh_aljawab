# payments/management/commands/rajhi_ping.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import time
import uuid
import requests

from django.core.management.base import BaseCommand
from django.conf import settings

from payments.rajhi_crypto import encrypt_trandata


def _get_base_url() -> str:
    """
    يحاول أخذ الدومين من ENV أو من الإعدادات، وإلا يرجع دومين الإنتاج.
    """
    return (
        os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")


class Command(BaseCommand):
    help = "Ping Al Rajhi PG (hosted checkout) with proper payload."

    def add_arguments(self, parser):
        parser.add_argument(
            "--amount",
            type=float,
            default=3.00,
            help="Amount to test with (e.g. 3.00)",
        )

    # ✅ حسب دعم الراجحي: استخدم hosted.htm
    GATEWAY_URL = "https://securepayments.alrajhibank.com.sa/pg/hosted.htm"

    def handle(self, *args, **options):
        amount = options["amount"]

        cfg = getattr(settings, "RAJHI_CONFIG", {})
        transportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        transportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        if not transportal_id or not transportal_password:
            self.stderr.write(
                self.style.ERROR("RAJHI_CONFIG ناقصة: TRANSPORTAL_ID / TRANSPORTAL_PASSWORD")
            )
            sys.exit(1)

        base_url = _get_base_url()
        success_url = f"{base_url}/payments/rajhi/callback/success/"
        error_url   = f"{base_url}/payments/rajhi/callback/fail/"

        # trackid مميز
        track_id = f"{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"
        amount_str = f"{amount:.2f}"

        # داخل trandata: الحقول الأساسية فقط
        trandata_pairs = {
            "action": "1",
            "amt": amount_str,
            "currencycode": "682",  # SAR
            "langid": "AR",
            "trackid": track_id,
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        enc = encrypt_trandata(trandata_pairs)

        self.stdout.write("gateway=HOSTED.HTM")
        self.stdout.write(f"trandata_plain={trandata_pairs}")
        self.stdout.write(f"trandata_hex_len={len(enc)}")

        # ✅ حسب التكامل الشائع مع hosted.htm:
        # نرسل id/password/trandata + responseURL/errorURL خارج التشفير
        payload = {
            "id": transportal_id,
            "password": transportal_password,
            "trandata": enc,
            "responseURL": success_url,
            "errorURL": error_url,
        }

        # طباعة للتشخيص قبل الإرسال (بدون كشف أسرار إضافية)
        self.stdout.write("=== DEBUG Payload (keys) ===")
        self.stdout.write(f"id={payload['id']}")
        self.stdout.write(f"password={'*' * len(payload['password'])}")
        self.stdout.write(f"trandata={payload['trandata'][:64]}... (len={len(payload['trandata'])})")
        self.stdout.write(f"responseURL={payload['responseURL']}")
        self.stdout.write(f"errorURL={payload['errorURL']}")
        self.stdout.write("============================")

        try:
            resp = requests.post(self.GATEWAY_URL, data=payload, timeout=30)
            self.stdout.write(f"POST status={resp.status_code}")
            body = (resp.text or "").strip()
            self.stdout.write(body[:2000])  # اطبع أول 2000 حرف للتشخيص
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"POST error: {e}"))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Done."))
