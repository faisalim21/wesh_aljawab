import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone

from games.models import GamePackage, UserPurchase

# إعداد لوق لتتبع الأخطاء
logger = logging.getLogger(__name__)

@login_required
@transaction.atomic
def create_payment(request, package_id):
    """
    دفع وهمي: يضيف شراء صالح لمدة 72 ساعة بدون أي بوابة دفع.
    آمن - منظم - مختصر - عملي.
    """

    try:
        # 1) نحضر الحزمة
        package = get_object_or_404(GamePackage, id=package_id)

        # 2) نتحقق هل المستخدم اشترى مسبقًا (وما زال صالح)
        existing = UserPurchase.objects.filter(
            user=request.user,
            package=package,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            messages.info(request, f"✅ سبق لك شراء هذه الحزمة وهي ما زالت صالحة.")
            return redirect("games:letters_home")

        # 3) إنشاء شراء جديد صالح 72 ساعة
        expiry_time = timezone.now() + timezone.timedelta(hours=72)

        UserPurchase.objects.create(
            user=request.user,
            package=package,
            expires_at=expiry_time
        )

        # 4) رسالة للمستخدم
        messages.success(request,
                         f"🎉 تم تفعيل الحزمة بنجاح! "
                         f"صالح لمدة 72 ساعة حتى {expiry_time.strftime('%Y-%m-%d %H:%M')}")

        # 5) تحويله مباشرة لصفحة اختيار الجلسة وبدء اللعب
        return redirect("games:create_letters_session")  # ✅ هذا هو السلوك الصحيح

    except Exception as e:
        # تسجيل الخطأ للاحتفاظ به في السيرفر (لن يصل للمستخدم)
        logger.error(f"Fake payment failed: {e}", exc_info=True)

        messages.error(request,
                       "⚠️ حدث خطأ غير متوقع أثناء تجهيز الشراء. حاول مرة أخرى بعد قليل.")

        return redirect("games:letters_home")
