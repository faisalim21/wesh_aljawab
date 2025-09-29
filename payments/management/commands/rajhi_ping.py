# payments/management/commands/rajhi_ping.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os, sys, time, uuid, json, binascii, logging
import requests
from urllib.parse import urljoin

from django.core.management.base import BaseCommand
from django.conf import settings

# سنشفّر هنا مباشرةً (بدون الاعتماد على _build_plain_qs) لنلتزم بحروف/أسماء الحقول
try:
    from Crypto.Cipher import AES
except Exception as e:
    raise SystemExit("PyCryptodome مطلوب: pip install pycryptodome") from e

log = logging.getLogger("payments.rajhi_ping")

IV = b"PGKEYENCDECIVSPC"  # كما في الدليل

def _pkcs7_pad(b: bytes, block: int = 16) -> bytes:
    pad = block - (len(b) % block)
    return b + bytes([pad]) * pad

def _aes_encrypt_hex(plain_qs: str) -> str:
    cfg = getattr(settings, "RAJHI_CONFIG", {})
    key_hex = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not key_hex:
        raise SystemExit("RESOURCE_KEY مفقود (RAJHI_CONFIG['RESOURCE_KEY'] أو RAJHI_RESOURCE_KEY).")
    try:
        key = bytes.fromhex(key_hex)
    except Exception:
        raise SystemExit("RESOURCE_KEY يجب أن يكون HEX صالح.")
    if len(key) not in (16, 24, 32):
        raise SystemExit(f"طول مفتاح AES غير صالح: {len(key)} (يجب 16/24/32).")

    cipher = AES.new(key, AES.MODE_CBC, IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_qs.encode("utf-8")))
    return ct.hex().upper()

def _base_url() -> str:
    return (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")


class Command(BaseCommand):
    help = "Bank-Hosted REST ping to hosted.htm using JSON + AES-CBC trandata (per ARB REST guide)."

    def add_arguments(self, parser):
        parser.add_argument("--amount", type=float, default=3.00)
        parser.add_argument(
            "--endpoint",
            default="https://securepayments.alrajhibank.com.sa/pg/hosted.htm",
            help="REST endpoint (default: bank hosted).",
        )
        parser.add_argument("--lang", default="AR")
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--no-verify", action="store_true")

    def handle(self, *args, **opts):
        cfg = getattr(settings, "RAJHI_CONFIG", {})
        tranportal_id = (cfg.get("TRANSPORTAL_ID") or os.environ.get("RAJHI_TRANSPORTAL_ID") or "").strip()
        tranportal_pw = (cfg.get("TRANSPORTAL_PASSWORD") or os.environ.get("RAJHI_TRANSPORTAL_PASSWORD") or "").strip()

        if not tranportal_id or not tranportal_pw:
            self.stderr.write(self.style.ERROR("TRANSPORTAL_ID/TRANSPORTAL_PASSWORD مفقودة في RAJHI_CONFIG"))
            sys.exit(1)

        amount = f"{opts['amount']:.2f}"
        endpoint = opts["endpoint"]
        langid = opts["lang"]
        verify_tls = not opts["no_verify"]
        timeout = opts["timeout"]

        base = _base_url()
        responseURL = urljoin(base + "/", "payments/rajhi/callback/success/")
        errorURL    = urljoin(base + "/", "payments/rajhi/callback/fail/")
        trackId = f"{int(time.time()*1000)}{uuid.uuid4().hex[:4]}"

        # === Plain trandata EXACTLY as in REST guide (names/case matter) ===
        # Mandatory + optional (udf*, langid) — كلها داخل trandata
        plain_pairs = {
            "amt": amount,
            "action": "1",              # 1 = Purchase
            "password": tranportal_pw,  # داخل التشفير
            "id": tranportal_id,        # داخل التشفير
            "currencyCode": "682",
            "trackId": trackId,
            "responseURL": responseURL,
            "errorURL": errorURL,
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
            "langid": langid,
        }
        # لا نعمل URL-encode؛ الدليل يطلب key=value&… مباشرة
        plain_qs = "&".join(f"{k}={'' if v is None else str(v)}" for k, v in plain_pairs.items())

        trandata_hex = _aes_encrypt_hex(plain_qs)

        # === Outer JSON body per guide: id + trandata + responseURL + errorURL ===
        body = [{
            "id": tranportal_id,
            "trandata": trandata_hex,
            "responseURL": responseURL,
            "errorURL": errorURL,
        }]

        headers = {
            "Content-Type": "application/json",
            # بعض البوابات تعتمد هذا الهيدر للتمييز
            "Accept": "application/json, text/html;q=0.8",
        }

        self.stdout.write(f"gateway=HOSTED.REST")
        self.stdout.write(f"trackId={trackId}")
        self.stdout.write(f"trandata_hex_len={len(trandata_hex)}")
        self.stdout.write("=== DEBUG (keys only) ===")
        self.stdout.write(f"id={tranportal_id}")
        self.stdout.write(f"responseURL={responseURL}")
        self.stdout.write(f"errorURL={errorURL}")
        self.stdout.write("=========================")

        # أرسل الطلب؛ لا نتبع التحويلات كي نظهر Location بوضوح (غالباً 302 لصفحة الدفع/الخطأ)
        try:
            resp = requests.post(
                endpoint,
                data=json.dumps(body),
                headers=headers,
                timeout=timeout,
                verify=verify_tls,
                allow_redirects=False,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"POST error: {e}"))
            sys.exit(1)

        loc = resp.headers.get("Location", "")
        ctype = resp.headers.get("Content-Type", "")
        self.stdout.write(f"POST status={resp.status_code}")
        if loc:
            self.stdout.write(f"Location: {loc}")
        if ctype:
            self.stdout.write(f"Content-Type: {ctype}")
        # اطبع أول 1.5KB من الجسم للتشخيص فقط
        body_text = (resp.text or "")[:1500]
        if body_text:
            self.stdout.write(body_text)

        self.stdout.write(self.style.SUCCESS("Done."))
