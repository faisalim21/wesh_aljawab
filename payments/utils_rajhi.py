# payments/utils_rajhi.py
from __future__ import annotations

"""
أداة تشفير trandata الخاصة بتكامل Bank-Hosted (حسب ملف ARB/Neoleap PDF).
- AES-CBC IV = b"PGKEYENCDECIVSPC"
- مفتاح التشفير يُقرأ من settings.RAJHI_CONFIG أو متغيرات البيئة.
- ترتيب ومفاتيح الحقول داخل trandata يجب أن تكون EXACT:
  id,password,action,currencyCode,errorURL,responseURL,trackId,amt,langid,udf1..udf5
"""

import os
from typing import Dict, Iterable
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES  # PyCryptodome
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e

_IV = b"PGKEYENCDECIVSPC"  # ثابت من الدليل
_BLOCK = 16


def _pkcs7_pad(data: bytes, block: int = _BLOCK) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _read_key_text() -> str:
    """
    يحاول قراءة المفتاح كنص من:
      - settings.RAJHI_CONFIG["RESOURCE_FILE"] (إن كان ملفًا موجودًا)
      - أو settings.RAJHI_CONFIG["RESOURCE_KEY"]
      - أو env["RAJHI_RESOURCE_KEY"]
    """
    cfg = getattr(settings, "RAJHI_CONFIG", {}) or {}

    # من ملف
    path = (cfg.get("RESOURCE_FILE") or "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = (f.read() or "").strip()
                if txt:
                    return txt
        except Exception:
            # نتابع للمصادر الأخرى
            pass

    # من الإعداد/البيئة
    txt = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not txt:
        raise ImproperlyConfigured("RESOURCE_KEY/RAJHI_RESOURCE_KEY غير موجود.")
    return txt


def _get_aes_key() -> bytes:
    """
    يرجّع مفتاح AES كـ bytes.
    KEY_FORMAT يُقرأ من:
      - env["RAJHI_KEY_FORMAT"] أو settings.RAJHI_CONFIG["KEY_FORMAT"] (الافتراضي HEX)
    """
    key_text = _read_key_text()
    fmt = (os.environ.get("RAJHI_KEY_FORMAT")
           or (getattr(settings, "RAJHI_CONFIG", {}) or {}).get("KEY_FORMAT")
           or "HEX").upper()

    if fmt == "HEX":
        try:
            key = bytes.fromhex(key_text)
        except Exception as e:
            raise ImproperlyConfigured("RESOURCE_KEY بصيغة HEX غير صالح.") from e
    else:  # TEXT
        key = key_text.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ImproperlyConfigured(f"طول مفتاح AES غير صالح ({len(key)}). يجب 16/24/32 بايت.")
    return key


def _ordered_items_for_hosted(pairs: Dict[str, str]) -> Iterable[tuple[str, str]]:
    """
    يبني الترتيب EXACT للبوابة (Bank-Hosted) ويضمن وجود udf1..udf5.
    ملاحظة: لا نعمل URL-encoding للقيم.
    """
    order = [
        "id", "password", "action", "currencyCode",
        "errorURL", "responseURL", "trackId", "amt", "langid",
        "udf1", "udf2", "udf3", "udf4", "udf5",
    ]

    # تحقّق من الحقول الإلزامية
    required = ["id", "password", "action", "currencyCode", "errorURL", "responseURL", "trackId", "amt", "langid"]
    missing = [k for k in required if (pairs.get(k) is None or str(pairs.get(k)) == "")]
    if missing:
        raise ImproperlyConfigured(f"حقول ناقصة في trandata (hosted): {', '.join(missing)}")

    # اضمن وجود UDFs فارغة إن لم تُرسل
    for udf in ("udf1", "udf2", "udf3", "udf4", "udf5"):
        pairs.setdefault(udf, "")

    for k in order:
        yield k, "" if pairs.get(k) is None else str(pairs.get(k))

    # أي مفاتيح إضافية يضيفها المطوّر تُلحق في النهاية (اختياري)
    used = set(order)
    for k, v in pairs.items():
        if k not in used:
            yield k, "" if v is None else str(v)


def _build_qs_hosted(pairs: Dict[str, str]) -> str:
    return "&".join(f"{k}={v}" for k, v in _ordered_items_for_hosted(pairs))


def encrypt_trandata_hosted(trandata_pairs: Dict[str, str]) -> str:
    """
    يُشفّر نص trandata (بالترتيب والمفاتيح EXACT كما في الدليل) باستخدام AES-CBC ويفرجع HEX Uppercase.
    """
    plain_qs = _build_qs_hosted(trandata_pairs).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_qs))
    return ct.hex().upper()
