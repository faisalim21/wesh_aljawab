# games/utils_letters.py
from django.core.cache import cache
from secrets import SystemRandom

# ملاحظة مهمة:
# استخدمنا "هـ" بدل "ه" لتكون متطابقة مع الحرف المستخدم في الأسئلة والقوالب.
ALPHABET28 = [
    'أ','ب','ت','ث','ج','ح','خ','د','ذ','ر','ز','س','ش','ص','ض','ط',
    'ظ','ع','غ','ف','ق','ك','ل','م','ن','هـ','و','ي'
]

_rng = SystemRandom()

# مفاتيح الكاش
CACHE_KEY_ORDER = "letters_order_{sid}"      # ترتيب جلسة مدفوعة معيّن
FREE_CACHE_KEY   = "letters_order_free_v1"   # ترتيب موحّد للمجاني لكل المستخدمين

# TTL
PAID_TTL_SECONDS = 72 * 60 * 60  # 72 ساعة

def get_free_order():
    """
    ترتيب ثابت (واحد) لجميع جلسات المجاني.
    - يُخلط مرة واحدة فقط عند أول طلب، ثم يُحفظ بدون انتهاء صلاحية.
    - يضمن أن كل جلسات المجاني (وكل المستخدمين) يشوفون نفس الترتيب دائمًا.
    """
    order = cache.get(FREE_CACHE_KEY)
    if order:
        return order

    # ننسخ الـ28 حرف ونخلطهم مرة واحدة ونثبتهم في الكاش.
    arr = ALPHABET28[:]
    _rng.shuffle(arr)
    cache.set(FREE_CACHE_KEY, arr, None)  # بدون انتهاء
    return arr

def get_paid_order_fresh():
    """ينشئ ترتيبًا عشوائيًا جديدًا (28 حرف) للجلسات المدفوعة."""
    arr = ALPHABET28[:]
    _rng.shuffle(arr)
    return arr

def get_session_order(session_id, is_free):
    """
    مصدر الحقيقة لترتيب حروف الجلسة.
    - المدفوع: نقرأ الترتيب المخزّن للجلسة؛ وإن ما وُجد ننشئ واحدًا جديدًا ونخزّنه 72 ساعة.
    - المجاني: نرجّع الترتيب الموحد للمجاني (بدون نسخ لكل جلسة لتقليل استهلاك الكاش).
    """
    if is_free:
        return get_free_order()

    key = CACHE_KEY_ORDER.format(sid=session_id)
    order = cache.get(key)
    if order:
        return order

    order = get_paid_order_fresh()
    cache.set(key, order, PAID_TTL_SECONDS)
    return order

def set_session_order(session_id, letters, is_free):
    """
    تحديث ترتيب الجلسة يدويًا (مثلاً عند 'جولة جديدة' للمدفوع).
    - للمجاني عادة ما نحتاج تخزين per-session لأن عندنا FREE_CACHE_KEY موحد،
      لكن لو أرسلت هنا للاتساق ما فيه ضرر.
    """
    key = CACHE_KEY_ORDER.format(sid=session_id)
    if is_free:
        # اختيارياً: تجاهل الكتابة per-session لأن المجاني موحد عبر FREE_CACHE_KEY.
        # لو حاب تثبّت نسخة للجلسة نفسها، أزل السطر التالي وفعّل set().
        return
    cache.set(key, letters, PAID_TTL_SECONDS)
