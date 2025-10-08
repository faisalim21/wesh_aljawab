# payments/utils_rajhi.py
import json
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
    الخطوات حسب الدليل:
    1. JSON → URL-encode
    2. AES/CBC/PKCS5 + IV ثابت
    3. تحويل HEX upper-case
    """
    key = _get_key()
    plain = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    encoded = urllib.parse.quote(plain, safe="")
    cipher = AES.new(key, AES.MODE_CBC, iv=IV)
    ct = cipher.encrypt(pad(encoded.encode("utf-8"), BLOCK_SIZE))
    return binascii.hexlify(ct).upper().decode("ascii")


def decrypt_trandata(cipher_hex: str) -> Dict:
    key = _get_key()
    cipher_bytes = binascii.unhexlify(cipher_hex)
    cipher = AES.new(key, AES.MODE_CBC, iv=IV)
    plain = unpad(cipher.decrypt(cipher_bytes), BLOCK_SIZE).decode("utf-8")
    decoded = urllib.parse.unquote(plain)
    try:
        return json.loads(decoded)
    except Exception as e:
        logger.error("Failed to parse decrypted trandata: %s", e)
        raise
