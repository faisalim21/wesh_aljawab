# payments/rajhi_crypto.py
from __future__ import annotations
import os
import binascii
import urllib.parse
from typing import Dict, Tuple
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES, DES3  # PyCryptodome
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e

# ===== مشتركات =====
_BLOCK16 = 16
_BLOCK8  = 8
_AES_IV  = b"PGKEYENCDECIVSPC"  # IV ثابت بحسب وثائق الراجحي

def _pad_16(b: bytes) -> bytes:
    pad = _BLOCK16 - (len(b) % _BLOCK16)
    return b + bytes([pad]) * pad

def _unpad_16(b: bytes) -> bytes:
    if not b:
        return b
    pad = b[-1]
    if pad < 1 or pad > _BLOCK16:
        raise ValueError("Bad padding")
    return b[:-pad]

def _pad_8(b: bytes) -> bytes:
    pad = _BLOCK8 - (len(b) % _BLOCK8)
    return b + bytes([pad]) * pad

# نبني querystring مع ترميز القيم فقط (حسب العادة مع بوابات الدفع)
def _build_qs(params: Dict[str, str]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote, doseq=True)

def _read_key_text() -> str:
    cfg = getattr(settings, "RAJHI_CONFIG", {})
    # من ملف
    resource_path = (cfg.get("RESOURCE_FILE") or "").strip()
    if resource_path and os.path.isfile(resource_path):
        with open(resource_path, "r", encoding="utf-8") as f:
            txt = (f.read() or "").strip()
            if txt:
                return txt
    # أو من الإعداد/البيئة
    txt = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not txt:
        raise ImproperlyConfigured("مفتاح الراجحي غير موجود: RESOURCE_FILE أو RESOURCE_KEY/RAJHI_RESOURCE_KEY.")
    return txt

# ===== AES (CBC) =====
def _get_aes_key() -> bytes:
    text = _read_key_text()
    fmt  = (os.environ.get("RAJHI_KEY_FORMAT") or "TEXT").upper()
    if fmt == "HEX":
        try:
            key = bytes.fromhex(text)
        except Exception as e:
            raise ImproperlyConfigured("RESOURCE_KEY بصيغة HEX غير صالح.") from e
    else:
        key = text.encode("utf-8")
    if len(key) not in (16, 24, 32):
        raise ImproperlyConfigured(f"طول مفتاح AES غير صالح ({len(key)}). يجب 16 أو 24 أو 32 بايت.")
    return key

def _encrypt_aes(params: Dict[str, str]) -> str:
    plain = _build_qs(params).encode("utf-8")
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _AES_IV)
    ct = cipher.encrypt(_pad_16(plain))
    return ct.hex().upper()

def _decrypt_aes(enc_hex: str) -> str:
    key = _get_aes_key()
    cipher = AES.new(key, AES.MODE_CBC, _AES_IV)
    pt_padded = cipher.decrypt(bytes.fromhex(enc_hex))
    return _unpad_16(pt_padded).decode("utf-8")

# ===== 3DES (ECB) =====
def _get_3des_key() -> bytes:
    """
    الراجحي يُظهر Terminal Resource Key كنص Hex (طوله 32 أو 48 hex).
    16 بايت => نمددها إلى 24 بايت K1|K2|K1.
    """
    hex_key = _read_key_text()
    try:
        raw = binascii.unhexlify(hex_key)
    except Exception as e:
        raise ImproperlyConfigured("RESOURCE_KEY يجب أن يكون Hex لمود 3DES.") from e

    if len(raw) == 16:
        raw = raw + raw[:8]  # 16 -> 24
    elif len(raw) == 24:
        pass
    else:
        raise ImproperlyConfigured(f"طول مفتاح 3DES غير مدعوم ({len(raw)}). يجب 16 أو 24 بايت قبل الضبط.")
    return DES3.adjust_key_parity(raw)

def _encrypt_3des(params: Dict[str, str]) -> str:
    plain = _build_qs(params).encode("utf-8")
    key = _get_3des_key()
    cipher = DES3.new(key, DES3.MODE_ECB)
    ct = cipher.encrypt(_pad_8(plain))
    return binascii.hexlify(ct).decode("ascii").upper()

def _decrypt_3des(enc_hex: str) -> str:
    key = _get_3des_key()
    cipher = DES3.new(key, DES3.MODE_ECB)
    pt_padded = cipher.decrypt(binascii.unhexlify(enc_hex))
    # PKCS5/7 على بلوك 8
    pad = pt_padded[-1]
    if pad < 1 or pad > _BLOCK8:
        raise ValueError("Bad padding")
    return pt_padded[:-pad].decode("utf-8")

# ===== API عامّة =====
def encrypt_trandata(params: Dict[str, str]) -> str:
    """
    افتراضيًا نستخدم 3DES (المناسب لـ PaymentInitHTTPServlet).
    بدّل بالسطر: RAJHI_TRANDATA_ALGO=AES لو احتجت AES.
    """
    algo = (os.environ.get("RAJHI_TRANDATA_ALGO") or "3DES").upper()
    if algo == "AES":
        return _encrypt_aes(params)
    return _encrypt_3des(params)

def decrypt_trandata(enc_hex: str) -> Tuple[str, Dict[str, str]]:
    """
    نفك بتجربة AES ثم 3DES (لتوافقية أعلى)، ثم نعيد (plain, dict).
    """
    last_err = None
    for fn in (_decrypt_aes, _decrypt_3des):
        try:
            qs = fn(enc_hex)
            pairs = urllib.parse.parse_qsl(qs, keep_blank_values=True)
            return qs, dict(pairs)
        except Exception as e:
            last_err = e
            continue
    raise ImproperlyConfigured(f"فشل فك trandata بـ AES و3DES: {last_err}")
