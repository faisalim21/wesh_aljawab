# payments/management/commands/rajhi_ping.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import time
import uuid
import json
import logging
import requests
from urllib.parse import urljoin

from django.core.management.base import BaseCommand
from django.conf import settings

# تشفير AES-CBC كما في الدليل (IV ثابت)
try:
    from Crypto.Cipher import AES
except Exception as e:
    raise SystemExit("PyCryptodome مطلوب: pip install pycryptodome") from e

log = logging.getLogger("payments.rajhi_ping")
IV = b"PGKEYENCDECIVSPC"  # ثابت حسب الدليل


def _pkcs7_pad(b: bytes, block: int = 16) -> bytes:
    pad = block - (len(b) % block)
    return b + bytes([pad]) * pad


def _base_url() -> str:
    # دومين موقعك (لازم https)
    return (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("BASE_URL")
        or getattr(settings, "SITE_BASE_URL", None)
        or "https://wesh-aljawab.onrender.com"
    ).rstrip("/")


def _get_keys() -> tuple[str, str, bytes]:
    """
    يرجّع: (tranportal_id, tranportal_password, aes_key_bytes)
    نقرأ من settings.RAJHI_CONFIG أو من متغيرات البيئة.
    """
    cfg = getattr(settings, "RAJHI_CONFIG", {}) or {}

    tpid = (os.environ.get("RAJHI_TRANSPORTAL_ID") or cfg.get("TRANSPORTAL_ID") or "").strip()
    tppw = (os.environ.get("RAJHI_TRANSPORTAL_PASSWORD") or cfg.get("TRANSPORTAL_PASSWORD") or "").strip()
    key_hex = (os.environ.get("RAJHI_RESOURCE_KEY") or cfg.get("RESOURCE_KEY") or "").strip()

    if not tpid or not tppw or not key_hex:
        raise SystemExit("⚠️ TRANSPORTAL_ID / TRANSPORTAL_PASSWORD / RESOURCE_KEY ناقصة.")

    try:
        key = bytes.fromhex(key_hex)
    except Exception:
        raise SystemExit("⚠️ RESOURCE_KEY يجب أن يكون HEX صالح (طول 16/24/32 بايت).")

    if len(key) not in (16, 24, 32):
        raise SystemExit(f"⚠️ طول مفتاح AES غير صالح: {len(key)} (مسموح 16 أو 24 أو 32 بايت).")

    return tpid, tppw, key


def _encrypt_trandata(plain_pairs: dict[str, str], key: bytes) -> str:
    """
    يبني نص trandata بصيغة key=value&... بدون URL-encoding للقيم،
    ثم يشفّره AES-CBC مع IV ثابت ويعيده HEX upper-case.
    """
    plain_qs = "&".join(f"{k}={'' if v is None else str(v)}" for k, v in plain_pairs.items())
    cipher = AES.new(key, AES.MODE_CBC, IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_qs.encode("utf-8")))
    return ct.hex().upper()


class Command(BaseCommand):
    help = "Neoleap Bank-Hosted (REST) ping: يبني trandata AES ويرسل JSON إلى hosted.htm ويطبع رابط الدفع."

    def add_arguments(self, parser):
        parser.add_argument("--amount", type=float, default=3.00, help="Amount (e.g. 3.00)")
        parser.add_argument(
            "--endpoint",
            default="https://securepayments.neoleap.com.sa/pg/payment/hosted.htm",
            help="Neoleap Hosted endpoint (default).",
        )
        parser.add_argument("--lang", default="AR", help="langid داخل trandata (AR/EN)")
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--no-verify", action="store_true", help="Disable TLS verification (not recommended).")
        parser.add_argument("--debug", action="store_true", help="اطبع معلومات تشخيصية إضافية.")

    def handle(self, *args, **opts):
        endpoint = opts["endpoint"]
        verify_tls = not opts["no_verify"]
        timeout = opts["timeout"]
        langid = opts["lang"]
        amount = f"{opts['amount']:.2f}"

        # المفاتيح
        tranportal_id, tranportal_pw, aes_key = _get_keys()

        # روابط النجاح/الفشل HTTPS
        base = _base_url()
        response_url = urljoin(base + "/", "payments/rajhi/callback/success/")
        error_url = urljoin(base + "/", "payments/rajhi/callback/fail/")

        # trackId فريد
        track_id = f"{int(time.time()*1000)}{uuid.uuid4().hex[:4]}"

        # === محتوى trandata EXACTLY كما في إيميل Neoleap ===
        trandata_pairs = {
            "id": tranportal_id,             # داخل التشفير
            "password": tranportal_pw,       # داخل التشفير
            "action": "1",                   # 1 = Purchase
            "currencyCode": "682",           # SAR
            "errorURL": error_url,           # داخل التشفير
            "responseURL": response_url,     # داخل التشفير
            "trackId": track_id,
            "amt": amount,
            "langid": langid,
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        trandata_hex = _encrypt_trandata(trandata_pairs, aes_key)

        # === الجسم الخارجي للطلب (JSON) كما في العينة ===
        body = [{
            "id": tranportal_id,
            "trandata": trandata_hex,
            "errorURL": error_url,       # يرسلونه أيضًا بالخارج
            "responseURL": response_url, # يرسلونه أيضًا بالخارج
        }]

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/html;q=0.8",
        }

        if opts["debug"]:
            self.stdout.write("=== DEBUG (keys only) ===")
            self.stdout.write(f"endpoint={endpoint}")
            self.stdout.write(f"id={tranportal_id}")
            self.stdout.write(f"trackId={track_id}")
            self.stdout.write(f"trandata_hex_len={len(trandata_hex)}")
            self.stdout.write(f"responseURL={response_url}")
            self.stdout.write(f"errorURL={error_url}")
            self.stdout.write("=========================")

        # إرسال الطلب (لا نتبع التحويلات؛ هذا endpoint يرجع 200 مع JSON)
        try:
            resp = requests.post(
                endpoint,
                data=json.dumps(body),
                headers=headers,
                timeout=timeout,
                verify=verify_tls,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"POST error: {e}"))
            sys.exit(1)

        self.stdout.write(f"HTTP {resp.status_code}")
        text = (resp.text or "").strip()
        if not text:
            self.stderr.write(self.style.ERROR("رد فارغ من البوابة."))
            sys.exit(2)

        # حاول قراءة JSON حسب صيغة العينة
        try:
            data = json.loads(text)
        except Exception:
            # لو ما قدر يقرأ JSON اطبع الجسم كما هو
            self.stdout.write(text)
            self.stderr.write(self.style.WARNING("تعذر تحويل الاستجابة إلى JSON."))
            sys.exit(0)

        # صيغة العينة: قائمة فيها عنصر واحد فيه (result, status)
        try:
            rec = data[0]
        except Exception:
            self.stdout.write(text)
            self.stderr.write(self.style.WARNING("صيغة JSON غير متوقعة (ليست قائمة بعنصر واحد)."))
            sys.exit(0)

        result = str(rec.get("result", ""))
        status = str(rec.get("status", ""))

        self.stdout.write(f"status={status}")
        self.stdout.write(f"result={result}")

        # إذا النتيجة مثل: "<PAYMENTID>:https://securepayments.alrajhibank.com.sa/pg/paymentpage.htm"
        redirect_url = ""
        payment_id = ""
        if ":" in result:
            payment_id, url = result.split(":", 1)
            payment_id = payment_id.strip()
            redirect_url = f"{url.strip()}?PaymentID={payment_id}"

        if redirect_url:
            self.stdout.write(self.style.SUCCESS(f"REDIRECT URL:\n{redirect_url}"))
        else:
            self.stderr.write(self.style.WARNING("لم أتعرف على رابط تحويل جاهز من result."))

        self.stdout.write(self.style.SUCCESS("Done."))
