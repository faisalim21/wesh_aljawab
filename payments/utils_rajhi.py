# payments/utils_rajhi.py
"""
تم الاستغناء عن 3DES نهائيًا وتوحيد التشفير على AES-CBC (IV = PGKEYENCDECIVSPC).
هذه الوحدة توفر أغلفة بسيطة للتوافق الخلفي إن وُجِد استيراد قديم.
"""
from .rajhi_crypto import encrypt_trandata as encrypt_trandata_aes, decrypt_trandata as decrypt_trandata_aes

def encrypt_trandata_3des(trandata_dict, hex_key):
    """
    DEPRECATED: احتفظنا بالاسم لأجل التوافق الخلفي.
    سيستخدم AES تحت الغطاء، لأن بوابة الراجحي تعتمد AES-CBC في الإصدارات الحالية.
    """
    return encrypt_trandata_aes(trandata_dict)

def decrypt_trandata_3des(enc_hex):
    """
    DEPRECATED: واجهة اسمية لفك التشفير؛ تنفّذ AES بالفعل.
    """
    plain, params = decrypt_trandata_aes(enc_hex)
    return plain
