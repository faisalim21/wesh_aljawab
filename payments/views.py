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
    """
    دفع وهمي — يعمل لجميع الألعاب:
    Letters / Images / Time / Quiz
    """
    try:
        package = get_object_or_404(GamePackage, id=package_id)

        # هل لديه شراء ساري لنفس الحزمة؟
        existing = UserPurchase.objects.filter(
            user=request.user,
            package=package,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            messages.info(request, "✅ الحزمة مفعلة لديك مسبقًا.")
        else:
            expiry_time = timezone.now() + timezone.timedelta(hours=72)
            UserPurchase.objects.create(
                user=request.user,
                package=package,
                expires_at=expiry_time
            )
            messages.success(
                request,
                f"🎉 تم تفعيل الحزمة! صالحة حتى {expiry_time.strftime('%Y-%m-%d %H:%M')}"
            )

        # ✅ نرجّع حسب نوع اللعبة
        if package.game_type == "letters":
            return redirect("games:create_letters_session")

        elif package.game_type == "images":
            return redirect("games:images_home")

        elif package.game_type == "time":
            return redirect("games:time_home")

        elif package.game_type == "quiz":
            return redirect("games:quiz_home")

        # fallback احتياطي
        return redirect("games:home")

    except Exception as e:
        logger.error(f"[FakePayment] ERROR: {e}", exc_info=True)
        messages.error(request, "⚠️ حدث خطأ أثناء الدفع. حاول مرة أخرى.")
        return redirect("games:home")
