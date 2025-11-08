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

        # هل لديه شراء ساري مسبقًا؟
        existing = UserPurchase.objects.filter(
            user=request.user,
            package=package,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            messages.info(request, "✅ سبق لك شراء هذه الحزمة وهي ما زالت صالحة.")
            # 👇 نوجه المستخدم حسب نوع اللعبة
            if package.game_type == 'letters':
                return redirect("games:letters_home")
            elif package.game_type == 'images':
                return redirect("games:images_home")
            else:
                return redirect("games:home")

        # إنشاء شراء جديد صالح 72 ساعة
        expiry_time = timezone.now() + timezone.timedelta(hours=72)

        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            expires_at=expiry_time
        )

        messages.success(request, f"🎉 تم تفعيل الحزمة! صالحة حتى {expiry_time.strftime('%Y-%m-%d %H:%M')}")

        # ✅ التوجيه حسب نوع اللعبة:
        if package.game_type == 'letters':
            return redirect(f"/games/letters/create/?package_id={package.id}")
        elif package.game_type == 'images':
            return redirect(f"/games/images/create/?package_id={package.id}")
        else:
            return redirect("games:home")

    except Exception as e:
        logger.error(f"Fake payment failed: {e}", exc_info=True)
        messages.error(request, "⚠️ حدث خطأ غير متوقع أثناء تجهيز الشراء. حاول لاحقًا.")
        return redirect("games:home")
