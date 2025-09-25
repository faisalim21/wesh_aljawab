# payments/rajhi_crypto.py
from __future__ import annotations

"""
وحدة التشفير الخاصة ببوابة الراجحي.

- الافتراضي الآن: AES-CBC مع IV ثابت كما في وثائق الراجحي: b"PGKEYENCDECIVSPC"
- تبقى 3DES (ECB) مدعومة للتوافق الخلفي فقط عبر اختيار env: RAJHI_TRANDATA_ALGO=3DES
- قراءة مفتاح التشفير من:
    1) settings.RAJHI_CONFIG["RESOURCE_FILE"] إن كان يشير لملف موجود
    2) أو settings.RAJHI_CONFIG["RESOURCE_KEY"] / env[RAJHI_RESOURCE_KEY]
- تحديد تنسيق المفتاح عبر env: RAJHI_KEY_FORMAT ∈ {"HEX","TEXT"} (الافتراضي HEX لأن مفاتيح الراجحي عادة تزوَّد كـ Hex)
- تبادل البيانات يتم كسلسلة QueryString (URL-Encoded للقيم) قبل التشفير
"""

import os
import binascii
import urllib.parse
import logging
from typing import Dict, Tuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    # PyCryptodome
    from Crypto.Cipher import AES, DES3
except Exception as e:
    raise ImproperlyConfigured("PyCryptodome مطلوب: pip install pycryptodome") from e

# ===== إعدادات عامة =====
logger = logging.getLogger("payments.rajhi_crypto")

_BLOCK16 = 16
_BLOCK8 = 8
_AES_IV = b"PGKEYENCDECIVSPC"  # IV ثابت بحسب وثائق الراجحي (طوله 16 بايت)

# ===== أدوات Padding =====
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

# ===== بناء/تفكيك QueryString =====
def _build_qs(params: Dict[str, str]) -> str:
    """
    يبني QueryString بترميز القيم فقط (كما هو شائع مع بوابات الدفع).
    """
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote, doseq=True)

# ===== قراءة المفتاح =====
def _read_key_text() -> str:
    """
    يحاول قراءة المفتاح كنص (عادة HEX) من:
      - ملف RAJHI_CONFIG["RESOURCE_FILE"] إن كان موجودًا
      - أو متغير RAJHI_CONFIG["RESOURCE_KEY"] / env[RAJHI_RESOURCE_KEY]
    """
    cfg = getattr(settings, "RAJHI_CONFIG", {})
    # من ملف (إن وُجد ومساره صحيح)
    resource_path = (cfg.get("RESOURCE_FILE") or "").strip()
    if resource_path and os.path.isfile(resource_path):
        try:
            with open(resource_path, "r", encoding="utf-8") as f:
                txt = (f.read() or "").strip()
                if txt:
                    return txt
        except Exception as e:
            logger.error("فشل قراءة ملف مفتاح الراجحي: %s", e)

    # أو من الإعداد/البيئة
    txt = (cfg.get("RESOURCE_KEY") or os.environ.get("RAJHI_RESOURCE_KEY") or "").strip()
    if not txt:
        raise ImproperlyConfigured("مفتاح الراجحي غير موجود: RESOURCE_FILE أو RESOURCE_KEY/RAJHI_RESOURCE_KEY.")
    return txt

# ===== AES (CBC) =====
def _get_aes_key() -> bytes:
    """
    يعيد مفتاح AES بصيغة bytes. يدعم:
      - HEX (افتراضيًا): عبر env RAJHI_KEY_FORMAT=HEX
      - TEXT: يأخذ النص كما هو (UTF-8)
    الطول المقبول: 16 أو 24 أو 32 بايت.
    """
    text = _read_key_text()
    fmt = (os.environ.get("RAJHI_KEY_FORMAT") or "HEX").upper()  # الافتراضي HEX لأن مفاتيح الراجحي غالبًا HEX
    if fmt == "HEX":
        try:
            key = bytes.fromhex(text)
        except Exception as e:
            raise ImproperlyConfigured("RESOURCE_KEY بصيغة HEX غير صالح.") from e
    else:
        key = text.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ImproperlyConfigured(
            f"طول مفتاح AES غير صالح ({len(key)}). يجب أن يكون 16 أو 24 أو 32 بايت."
        )
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

# ===== 3DES (ECB) - للتوافق الخلفي فقط =====
def _get_3des_key() -> bytes:
    """
    الراجحي يعرض Terminal Resource Key غالبًا كنص Hex (32 أو 48 حرف Hex).
    إذا كان 16 بايت => نمدده إلى 24 بايت K1|K2|K1 ليناسب 3DES.
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
    pad = pt_padded[-1]
    if pad < 1 or pad > _BLOCK8:
        raise ValueError("Bad padding")
    return pt_padded[:-pad].decode("utf-8")

# ===== API عامّة =====
def encrypt_trandata(params: Dict[str, str]) -> str:
    """
    تشفير trandata:
      - الافتراضي AES (موصى به من الراجحي)
      - يمكن إجبار 3DES عبر env: RAJHI_TRANDATA_ALGO=3DES
    """
    algo = (os.environ.get("RAJHI_TRANDATA_ALGO") or "AES").upper()
    if algo == "AES":
        return _encrypt_aes(params)
    # مسار توافق قديم
    logger.warning("يتم استخدام 3DES لتشفير trandata (لأغراض التوافق فقط). يُنصح بالتحول إلى AES.")
    return _encrypt_3des(params)

def decrypt_trandata(enc_hex: str) -> Tuple[str, Dict[str, str]]:
    """
    فكّ تشفير trandata:
      - نحاول AES أولًا ثم 3DES (توافقية أعلى)
      - نرجع (plain_qs, dict_params)
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
