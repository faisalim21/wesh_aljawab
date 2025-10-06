# payments/utils_rajhi.py
from __future__ import annotations
import json
import os
from typing import Dict
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES  # PyCryptodome
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e


_IV = b"PGKEYENCDECIVSPC"  # ثابت من الدليل
_BLOCK = 16


# ===================== Padding =====================
def _pkcs7_pad(data: bytes, block: int = _BLOCK) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


# ===================== قراءة المفتاح =====================
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

    # من config أو env
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


# ===================== Plain JSON =====================
def _build_plain_json(pairs: Dict[str, str]) -> str:
    """
    يبني plain JSON كـ Array يحتوي Object واحد [ { ... } ]
    بنفس الترتيب المطلوب من البنك.
    """
    order = [
        "id", "password", "action", "currencyCode",
        "errorURL", "responseURL", "trackId", "amt", "langid",
        "udf1", "udf2", "udf3", "udf4", "udf5",
    ]

    # التحقق من الحقول الأساسية
    required = ["id", "password", "action", "currencyCode",
                "errorURL", "responseURL", "trackId", "amt", "langid"]
    missing = [k for k in required if (pairs.get(k) is None or str(pairs.get(k)) == "")]
    if missing:
        raise ImproperlyConfigured(f"حقول ناقصة في trandata: {', '.join(missing)}")

    # ضمان وجود UDFs
    for udf in ("udf1", "udf2", "udf3", "udf4", "udf5"):
        pairs.setdefault(udf, "")

    # إعادة بناء dict مرتب
    ordered_dict = {k: str(pairs.get(k, "")) for k in order}

    # JSON Array يحتوي Object واحد
    return json.dumps([ordered_dict], ensure_ascii=False)


# ===================== التشفير =====================
def encrypt_trandata_hosted(trandata_pairs: Dict[str, str]) -> str:
    """
    يشفر plain JSON باستخدام AES-CBC ويرجع HEX Uppercase.
    """
    plain_json = _build_plain_json(trandata_pairs).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_json))
    return ct.hex().upper()


# ===================== مساعد للاختبارات =====================
def get_plain_and_encrypted(trandata_pairs: Dict[str, str]) -> tuple[str, str]:
    """
    يرجع (plain_json, encrypted_hex) عشان نرسلهما معاً لخدمة العملاء.
    """
    plain_json = _build_plain_json(trandata_pairs)
    enc_hex = encrypt_trandata_hosted(trandata_pairs)
    return plain_json, enc_hex
