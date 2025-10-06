# payments/utils_rajhi.py
from __future__ import annotations
import json

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
            pass

    # من الإعداد/البيئة
    txt = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not txt:
        raise ImproperlyConfigured("RESOURCE_KEY/RAJHI_RESOURCE_KEY غير موجود.")
    return txt


def _get_aes_key() -> bytes:
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


def _ordered_json_for_hosted(pairs: Dict[str, str]) -> str:
    """
    يبني JSON String EXACT للبوابة (Bank-Hosted) بالترتيب المطلوب.
    يضمن وجود udf1..udf5.
    """
    order = [
        "id", "password", "action", "currencyCode",
        "errorURL", "responseURL", "trackId", "amt", "langid",
        "udf1", "udf2", "udf3", "udf4", "udf5",
    ]

    # الحقول الإلزامية
    required = ["id", "password", "action", "currencyCode", "errorURL", "responseURL", "trackId", "amt"]
    missing = [k for k in required if (pairs.get(k) is None or str(pairs.get(k)) == "")]
    if missing:
        raise ImproperlyConfigured(f"حقول ناقصة في trandata (hosted): {', '.join(missing)}")

    # تأكد من وجود UDFs
    for udf in ("udf1", "udf2", "udf3", "udf4", "udf5"):
        pairs.setdefault(udf, "")

    # نرتب المفاتيح
    ordered_dict = {k: str(pairs.get(k, "")) for k in order}

    # أي مفاتيح إضافية يضيفها المطوّر تُلحق في النهاية
    for k, v in pairs.items():
        if k not in order:
            ordered_dict[k] = "" if v is None else str(v)

    # البوابة تتوقع Array من Object (لاحظ القوسين [])
    return json.dumps([ordered_dict], ensure_ascii=False, separators=(",", ":"))


def encrypt_trandata_hosted(trandata_pairs: Dict[str, str]) -> str:
    """
    يُشفّر نص trandata (كـ JSON String) باستخدام AES-CBC ويرجع HEX Uppercase.
    """
    plain_json = _ordered_json_for_hosted(trandata_pairs).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_json))
    return ct.hex().upper()
