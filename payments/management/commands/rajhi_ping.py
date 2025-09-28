# payments/management/commands/rajhi_ping.py
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
    نحاول أخذ الدومين من ENV، أو من الإعدادات، أو نستخدم الدومين الإفتراضي.
    """
    return (
        os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")


class Command(BaseCommand):
    help = "Ping Al Rajhi PG with proper trandata composition (AES)."

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

    GATEWAYS = {
        "prod": "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet",
        "uat":  "https://uat3ds.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet",
    }

    def handle(self, *args, **options):
        amount = options["amount"]
        env = options["env"].lower()
        gateway = self.GATEWAYS.get(env, self.GATEWAYS["prod"])

        cfg = getattr(settings, "RAJHI_CONFIG", {})
        transportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        transportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        if not all([transportal_id, transportal_password]):
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

        # داخل trandata (بدون id/password/responseURL/errorURL)
        trandata_pairs = {
            "action": "1",
            "amt": amount_str,
            "currencycode": "682",
            "langid": "AR",
            "trackid": track_id,
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        # تشفير trandata
        enc = encrypt_trandata(trandata_pairs)

        self.stdout.write(f"gateway={env.upper()}")
        self.stdout.write(f"trandata_plain={trandata_pairs}")
        self.stdout.write(f"trandata_hex_len={len(enc)}")

        # POST payload: Tranportal ID + Password + trandata + URLs
        payload = {
            "id": transportal_id,
            "password": transportal_password,
            "trandata": enc,
            "responseURL": success_url,
            "errorURL": error_url,
        }

        # ✅ اطبع القيم قبل الإرسال
        self.stdout.write("=== DEBUG Payload ===")
        for k, v in payload.items():
            self.stdout.write(f"{k}={v}")
        self.stdout.write("=====================")

        try:
            resp = requests.post(gateway, data=payload, timeout=30)
            self.stdout.write(f"POST status={resp.status_code}")
            body = (resp.text or "").strip()
            self.stdout.write(body[:2000])
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"POST error: {e}"))
            sys.exit(1)