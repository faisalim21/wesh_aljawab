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
    return (
        os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")

HOSTED_URL = "https://securepayments.alrajhibank.com.sa/pg/hosted.htm"
HOSTED_URL_WITH_PARAM = "https://securepayments.alrajhibank.com.sa/pg/hosted.htm?param=paymentInit"

def _post_once(url: str, payload: dict, timeout: int = 45):
    """Send one POST without following redirects; print status + headers snippet."""
    try:
        resp = requests.post(
            url,
            data=payload,
            timeout=timeout,
            allow_redirects=False,  # 👈 لا تتبع التحويلات
        )
        return resp, None
    except Exception as e:
        return None, e

class Command(BaseCommand):
    help = "Ping Al Rajhi PG (hosted checkout) and print status/headers without following redirects."

    def add_arguments(self, parser):
        parser.add_argument("--amount", type=float, default=3.00, help="Amount to test (e.g. 3.00)")

    def handle(self, *args, **options):
        amount = options["amount"]

        cfg = getattr(settings, "RAJHI_CONFIG", {})
        transportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
        transportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

        if not transportal_id or not transportal_password:
            self.stderr.write(self.style.ERROR("RAJHI_CONFIG ناقصة: TRANSPORTAL_ID / TRANSPORTAL_PASSWORD"))
            sys.exit(1)

        base_url = _get_base_url()
        success_url = f"{base_url}/payments/rajhi/callback/success/"
        error_url   = f"{base_url}/payments/rajhi/callback/fail/"

        track_id = f"{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"
        amount_str = f"{amount:.2f}"

        trandata_pairs = {
            "action": "1",
            "amt": amount_str,
            "currencycode": "682",  # SAR
            "langid": "AR",
            "trackid": track_id,
            "udf1": "", "udf2": "", "udf3": "", "udf4": "", "udf5": "",
        }
        enc = encrypt_trandata(trandata_pairs)

        payload = {
            "id": transportal_id,
            "password": transportal_password,
            "trandata": enc,
            "responseURL": success_url,
            "errorURL": error_url,
        }

        self.stdout.write("gateway=HOSTED.HTM (no redirects)")
        self.stdout.write(f"trandata_hex_len={len(enc)}")
        self.stdout.write("=== DEBUG Payload (keys) ===")
        self.stdout.write(f"id={payload['id']}")
        self.stdout.write(f"password={'*' * len(payload['password'])}")
        self.stdout.write(f"responseURL={payload['responseURL']}")
        self.stdout.write(f"errorURL={payload['errorURL']}")
        self.stdout.write("============================")

        # 1) جرب hosted.htm بدون بارام
        resp, err = _post_once(HOSTED_URL, payload)
        if err:
            self.stderr.write(self.style.ERROR(f"POST error (hosted.htm): {err}"))
        else:
            self.stdout.write(f"POST status (hosted.htm)={resp.status_code}")
            # اطبع رأس Location و Content-Type لمعرفة التحويل
            loc = resp.headers.get("Location", "")
            ctype = resp.headers.get("Content-Type", "")
            self.stdout.write(f"Location: {loc}")
            self.stdout.write(f"Content-Type: {ctype}")
            body = (resp.text or "")
            self.stdout.write(body[:500])

        # 2) إن ما نجحت الأولى، جرّب hosted.htm?param=paymentInit
        if (not resp) or (resp is not None and 300 <= resp.status_code < 400 and resp.headers.get("Location", "").lower().startswith("http://")):
            self.stdout.write("\nTrying hosted.htm?param=paymentInit (no redirects)...")
            resp2, err2 = _post_once(HOSTED_URL_WITH_PARAM, payload)
            if err2:
                self.stderr.write(self.style.ERROR(f"POST error (hosted.htm?param=paymentInit): {err2}"))
            else:
                self.stdout.write(f"POST status (hosted.htm?param=paymentInit)={resp2.status_code}")
                loc2 = resp2.headers.get("Location", "")
                ctype2 = resp2.headers.get("Content-Type", "")
                self.stdout.write(f"Location: {loc2}")
                self.stdout.write(f"Content-Type: {ctype2}")
                body2 = (resp2.text or "")
                self.stdout.write(body2[:500])

        self.stdout.write(self.style.SUCCESS("Done."))
