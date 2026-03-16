# games/utils_letters.py
from django.core.cache import cache
from secrets import SystemRandom

ALPHABET28 = [
    'أ','ب','ت','ث','ج','ح','خ','د','ذ','ر','ز','س','ش','ص','ض','ط',
    'ظ','ع','غ','ف','ق','ك','ل','م','ن','هـ','و','ي'
]

# الحزم الرياضية: بدون ض و ظ (26 حرف)
ALPHABET_SPORTS = [h for h in ALPHABET28 if h not in ('ض', 'ظ')]

_rng = SystemRandom()

CACHE_KEY_ORDER = "letters_order_{sid}"
FREE_CACHE_KEY  = "letters_order_free_v1"
PAID_TTL_SECONDS = 72 * 60 * 60

def get_free_order():
    order = cache.get(FREE_CACHE_KEY)
    if order:
        return order
    arr = ALPHABET28[:]
    _rng.shuffle(arr)
    cache.set(FREE_CACHE_KEY, arr, None)
    return arr

def get_paid_order_fresh(is_sports=False):
    arr = (ALPHABET_SPORTS if is_sports else ALPHABET28)[:]
    _rng.shuffle(arr)
    return arr

def get_session_order(session_id, is_free):
    if is_free:
        return get_free_order()
    key = CACHE_KEY_ORDER.format(sid=session_id)
    order = cache.get(key)
    if order:
        return order
    order = get_paid_order_fresh()
    cache.set(key, order, PAID_TTL_SECONDS)
    return order

def set_session_order(session_id, letters, is_free=False):
    if is_free:
        return
    key = CACHE_KEY_ORDER.format(sid=session_id)
    cache.set(key, letters, PAID_TTL_SECONDS)