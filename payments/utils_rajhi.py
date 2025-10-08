# payments/utils_rajhi.py
import logging
import binascii
import urllib.parse
from typing import Dict
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from django.conf import settings

logger = logging.getLogger(__name__)

IV = b"PGKEYENCDECIVSPC"  # ثابت من دليل الراجحي
BLOCK_SIZE = 16


def _get_key() -> bytes:
    cfg = settings.RAJHI_CONFIG
    key_format = (cfg.get("KEY_FORMAT") or "HEX").upper()
    raw = cfg.get("RESOURCE_KEY")
    if not raw:
        raise ValueError("Missing RAJHI_CONFIG.RESOURCE_KEY")

    if key_format == "HEX":
        try:
            key = binascii.unhexlify(raw)
        except Exception as e:
            raise ValueError(f"Invalid HEX key: {e}")
    else:
        key = raw.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ValueError(f"Invalid AES key length {len(key)} (expected 16/24/32).")
    return key


def encrypt_trandata(data: Dict) -> str:
    """
    1. dict → query string (key=value&key2=value2)
    2. URL encode للنص كامل
    3. AES/CBC/PKCS5 باستخدام مفتاح و IV ثابت
    4. HEX upper-case
    """
    key = _get_key()
    query = urllib.parse.urlencode(data)  # key=value&key2=value2
    encoded = urllib.parse.quote(query, safe="")  # URL encode
    cipher = AES.new(key, AES.MODE_CBC, iv=IV)
    ct = cipher.encrypt(pad(encoded.encode("utf-8"), BLOCK_SIZE))
    return binascii.hexlify(ct).upper().decode("ascii")


def decrypt_trandata(cipher_hex: str) -> Dict:
    key = _get_key()
    cipher_bytes = binascii.unhexlify(cipher_hex)
    cipher = AES.new(key, AES.MODE_CBC, iv=IV)
    plain = unpad(cipher.decrypt(cipher_bytes), BLOCK_SIZE).decode("utf-8")
    decoded = urllib.parse.unquote(plain)
    # decoded هنا string على شكل key=value&key2=value2 → نحوله dict
    parsed = dict(urllib.parse.parse_qsl(decoded))
    return parsed
