# payments/utils_rajhi.py
from __future__ import annotations
import json
import os
from typing import Dict, Tuple
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES  # PyCryptodome
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e

# ثابت حسب دليل Neoleap/AlRajhi
_IV = b"PGKEYENCDECIVSPC"
_BLOCK = 16


# -------------------------------
# Helpers
# -------------------------------
def _pkcs7_pad(data: bytes, block: int = _BLOCK) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _read_key_text() -> str:
    cfg = getattr(settings, "RAJHI_CONFIG", {}) or {}
    # من ملف إذا فيه
    path = (cfg.get("RESOURCE_FILE") or "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = (f.read() or "").strip()
                if txt:
                    return txt
        except Exception:
            pass

    # من الإعدادات أو البيئة
    txt = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not txt:
        raise ImproperlyConfigured("RESOURCE_KEY/RAJHI_RESOURCE_KEY غير موجود.")
    return txt


def _get_aes_key() -> bytes:
    key_text = _read_key_text()
    fmt = (
        os.environ.get("RAJHI_KEY_FORMAT")
        or (getattr(settings, "RAJHI_CONFIG", {}) or {}).get("KEY_FORMAT")
        or "HEX"
    ).upper()

    if fmt == "HEX":
        try:
            key = bytes.fromhex(key_text)
        except Exception as e:
            raise ImproperlyConfigured("RESOURCE_KEY بصيغة HEX غير صالح.") from e
    else:
        key = key_text.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ImproperlyConfigured(
            f"طول مفتاح AES غير صالح ({len(key)}). يجب أن يكون 16 أو 24 أو 32 بايت."
        )
    return key


# -------------------------------
# Plain JSON Builder
# -------------------------------
def _ordered_json_for_hosted(pairs: Dict[str, str]) -> str:
    """
    يبني JSON Array [ { ... } ] بالترتيب المطلوب للبوابة.
    هذا هو الـ "plain" اللي يطلبونه (قبل التشفير).
    """
    order = [
        "id", "password", "action", "currencyCode",
        "errorURL", "responseURL", "trackId", "amt", "langid",
        "udf1", "udf2", "udf3", "udf4", "udf5",
    ]

    required = ["id", "password", "action", "currencyCode",
                "errorURL", "responseURL", "trackId", "amt", "langid"]
    missing = [k for k in required if not pairs.get(k)]
    if missing:
        raise ImproperlyConfigured(f"حقول ناقصة: {', '.join(missing)}")

    # اضمن وجود udf1..udf5
    for udf in ("udf1", "udf2", "udf3", "udf4", "udf5"):
        pairs.setdefault(udf, "")

    ordered = {k: str(pairs.get(k, "")) for k in order}
    return json.dumps([ordered], ensure_ascii=False)


# -------------------------------
# Encryption
# -------------------------------
def encrypt_trandata_hosted(trandata_pairs: Dict[str, str]) -> str:
    """
    يشفر Plain JSON باستخدام AES-CBC ويرجع HEX Uppercase.
    """
    plain_json = _ordered_json_for_hosted(trandata_pairs).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_json))
    return ct.hex().upper()


# -------------------------------
# Utility for debugging
# -------------------------------
def get_plain_and_encrypted(pairs: Dict[str, str]) -> Tuple[str, str]:
    """
    يرجع plain JSON + التشفير HEX معًا (للاختبار أو الإرسال للدعم).
    """
    plain = _ordered_json_for_hosted(pairs)
    enc = encrypt_trandata_hosted(pairs)
    return plain, enc
