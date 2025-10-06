# payments/utils_rajhi.py
from __future__ import annotations
import json
import os
from typing import Dict, Iterable
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES  # PyCryptodome
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e


_IV = b"PGKEYENCDECIVSPC"  # IV ثابت حسب دليل Neoleap/ARB
_BLOCK = 16


def _pkcs7_pad(data: bytes, block: int = _BLOCK) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _read_key_text() -> str:
    cfg = getattr(settings, "RAJHI_CONFIG", {}) or {}

    # ملف resource (لو موجود)
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
    else:
        key = key_text.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ImproperlyConfigured(f"طول مفتاح AES غير صالح ({len(key)}). يجب 16/24/32 بايت.")
    return key


def _ordered_items_for_hosted(pairs: Dict[str, str]) -> Iterable[tuple[str, str]]:
    order = [
        "id", "password", "action", "currencyCode",
        "errorURL", "responseURL", "trackId", "amt", "langid",
        "udf1", "udf2", "udf3", "udf4", "udf5",
    ]
    required = ["id", "password", "action", "currencyCode",
                "errorURL", "responseURL", "trackId", "amt", "langid"]
    missing = [k for k in required if (pairs.get(k) is None or str(pairs.get(k)) == "")]
    if missing:
        raise ImproperlyConfigured(f"حقول ناقصة في trandata (hosted): {', '.join(missing)}")

    for udf in ("udf1", "udf2", "udf3", "udf4", "udf5"):
        pairs.setdefault(udf, "")

    for k in order:
        yield k, "" if pairs.get(k) is None else str(pairs.get(k))

    used = set(order)
    for k, v in pairs.items():
        if k not in used:
            yield k, "" if v is None else str(v)


def _build_plain_json(pairs: Dict[str, str]) -> str:
    """
    يبني plain JSON بالصيغة الصحيحة (Object {} فقط، بدون [])
    """
    ordered = {k: v for k, v in _ordered_items_for_hosted(pairs)}
    return json.dumps(ordered, ensure_ascii=False)


def encrypt_trandata_hosted(trandata_pairs: Dict[str, str]) -> str:
    """
    يُشفّر plain JSON باستخدام AES-CBC و يرجع HEX Uppercase
    """
    plain_json = _build_plain_json(trandata_pairs).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _IV)
    ct = cipher.encrypt(_pkcs7_pad(plain_json))
    return ct.hex().upper()


# لتسهيل الاختبار في shell
def get_plain_and_encrypted(trandata_pairs: Dict[str, str]) -> tuple[str, str]:
    plain = _build_plain_json(trandata_pairs)
    enc = encrypt_trandata_hosted(trandata_pairs)
    return plain, enc
