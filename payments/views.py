import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone

from games.models import GamePackage, UserPurchase

logger = logging.getLogger(__name__)

@login_required
@transaction.atomic
def create_payment(request, package_id):
    try:
        package = get_object_or_404(GamePackage, id=package_id)

        # هل عنده شراء غير منتهي؟
        existing = UserPurchase.objects.filter(
            user=request.user,
            package=package,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            messages.info(request, "✅ لديك وصول نشط لهذه الحزمة مسبقًا.")
        else:
            expiry_time = timezone.now() + timezone.timedelta(hours=72)
            UserPurchase.objects.create(
                user=request.user,
                package=package,
                expires_at=expiry_time
            )
            messages.success(request, "🎉 تم تفعيل الحزمة بنجاح لمدة 72 ساعة!")

        # ✅ توجيه حسب نوع اللعبة
        if package.game_type == 'letters':
            return redirect("games:create_letters_session")

        elif package.game_type == 'images':
            return redirect("games:create_images_session")

        elif package.game_type == 'time':
            return redirect("games:time_home")  # لو عندك صفحة تحدّي الوقت

        elif package.game_type == 'quiz':
            return redirect("games:quiz_home")  # لو عندك صفحة الأسئلة

        else:
            messages.warning(request, "⚠️ نوع اللعبة غير معروف.")
            return redirect("games:letters_home")

    except Exception as e:
        logger.error(f"Fake payment failed: {e}", exc_info=True)
        messages.error(request, "⚠️ حدث خطأ أثناء تجهيز الشراء. حاول لاحقًا.")
        return redirect("games:letters_home")

