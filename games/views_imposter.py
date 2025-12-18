from django.shortcuts import render
from games.models import GamePackage, ImposterWord
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from games.models import (
    GamePackage,
    ImposterWord,
    GameSession,
    UserPurchase,   # ✅ هذا الناقص
)

from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from games.models import GamePackage, UserPurchase


@login_required
def imposter_start(request, package_id):
    package = get_object_or_404(
        GamePackage,
        id=package_id,
        game_type="imposter",
        is_active=True
    )

    # الحزمة مجانية → مباشرة
    if package.is_free or package.package_number == 0:
        return redirect("games:imposter_setup", package_id=package.id)

    # التحقق من الشراء المكتمل
    has_purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=True
    ).exists()

    if not has_purchase:
        return redirect("payments:start_payment", package_id=package.id)

    return redirect("games:imposter_setup", package_id=package.id)



from django.shortcuts import render
from django.db.models import Count
from .models import GamePackage

from games.models import UserPurchase

from django.db.models import Count
from games.models import GamePackage, UserPurchase

from django.shortcuts import render
from django.utils import timezone
from django.db.models import Count

from games.models import GamePackage, UserPurchase


from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from games.models import (
    GamePackage,
    UserPurchase,
    GameSession,
)

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from games.models import GamePackage, UserPurchase


from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from games.models import GamePackage, UserPurchase, GameSession

from django.db import models

@login_required
def imposter_packages(request):
    """
    صفحة حزم لعبة امبوستر
    - عرض الحزم
    - قلب زر (شراء) إلى (ابدأ اللعب) للحزم المدفوعة عند وجود شراء مكتمل ونشط
    - الأهم: نعتمد على "الجلسات النشطة" كمصدر حقيقة إضافي (لأن expires_at قد يكون NULL أو غير مضبوط)
    """

    # 1) جلب الحزم
    packages = (
        GamePackage.objects
        .filter(game_type="imposter", is_active=True)
        .order_by("package_number")
    )

    now = timezone.now()

    # 2) كل مشتريات المستخدم المكتملة (حتى لو منتهية) — فقط للوسم "سبق شراء"
    purchased_packages = set(
        UserPurchase.objects.filter(
            user=request.user,
            package__game_type="imposter",
            is_completed=True,
        ).values_list("package_id", flat=True)
    )

    # 3) المشتريات النشطة فعليًا:
    #    - إما expires_at أكبر من الآن
    #    - أو expires_at = NULL (احتياطًا إذا ما انحفظت)
    active_purchase_packages = set(
        UserPurchase.objects.filter(
            user=request.user,
            package__game_type="imposter",
            is_completed=True,
        ).filter(
            models.Q(expires_at__gt=now) | models.Q(expires_at__isnull=True)
        ).values_list("package_id", flat=True)
    )

    # 4) الجلسات النشطة (هذه أهم نقطة) — إذا الجلسة موجودة ونشطة، الحزمة تعتبر نشطة
    active_session_packages = set(
        GameSession.objects.filter(
            host=request.user,
            is_active=True,
            package__game_type="imposter",
        ).values_list("package_id", flat=True)
    )

    # ✅ المصدر النهائي للحزم النشطة
    active_packages_ids = active_purchase_packages.union(active_session_packages)

    # 5) جلسة قادمة مباشرة بعد الدفع (?session=)
    session_id = request.GET.get("session")
    paid_session = None

    if session_id:
        paid_session = (
            GameSession.objects.filter(
                id=session_id,
                host=request.user,
                is_active=True,
                package__game_type="imposter",
            ).select_related("package").first()
        )
        if paid_session:
            active_packages_ids.add(paid_session.package_id)

    # 6) context
    context = {
        "packages": packages,

        # ✅ نخليها باسمين عشان ما يتعطل قالبك الحالي
        "active_packages": active_packages_ids,
        "active_packages_ids": active_packages_ids,

        # للقالب اللي يحط "تم شراؤها مسبقاً"
        "purchased_packages": purchased_packages,

        "paid_session": paid_session,
    }
    return render(request, "games/imposter/packages.html", context)





from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods
from games.models import GameSession


from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods
from games.models import GameSession
import random


@require_http_methods(["GET", "POST"])
def imposter_session_view(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)

    session_key = f"imposter_{session.id}"
    data = request.session.get(session_key)

    if not data:
        return render(request, "games/imposter/error.html", {
            "message": "بيانات الجلسة غير موجودة."
        })

    players_count = data["players_count"]
    imposters = data["imposters"]
    words = data["words"]

    current_index = data.get("current_index", -1)
    current_round = data.get("current_round", 0)

    # =========================
    # GET → أول دخول
    # =========================
    if request.method == "GET":
        data["current_index"] = 0
        request.session.modified = True

        return render(request, "games/imposter/session.html", {
            "step": "pass",
            "player_number": 1,
            "round_number": current_round + 1,
        })

    # =========================
    # POST
    # =========================
    action = request.POST.get("action")

    # --------
    # عرض الدور (كشف)
    # --------
    if action == "show":
        is_imposter = current_index in imposters
        secret_word = None if is_imposter else words[current_round]

        return render(request, "games/imposter/session.html", {
            "step": "reveal",
            "player_number": current_index + 1,
            "is_imposter": is_imposter,
            "secret_word": secret_word,
            "round_number": current_round + 1,
        })

    # --------
    # اللاعب التالي
    # --------
    if action == "next":
        current_index += 1
        data["current_index"] = current_index
        request.session.modified = True

        # انتهى كل اللاعبين
        if current_index >= players_count:
            return render(request, "games/imposter/session.html", {
                "step": "done",
                "round_number": current_round + 1,
                "is_last_round": current_round >= len(words) - 1,
            })

        return render(request, "games/imposter/session.html", {
            "step": "pass",
            "player_number": current_index + 1,
            "round_number": current_round + 1,
        })

    # --------
    # جولة جديدة
    # --------
    if action == "next_round":
        current_round += 1

        # انتهت الكلمات
        if current_round >= len(words):
            return render(request, "games/imposter/session.html", {
                "step": "finished"
            })

        # إعادة تعيين
        data["current_round"] = current_round
        data["current_index"] = 0
        data["imposters"] = random.sample(
            list(range(players_count)),
            len(imposters)
        )
        request.session.modified = True

        return render(request, "games/imposter/session.html", {
            "step": "pass",
            "player_number": 1,
            "round_number": current_round + 1,
        })


def build_context(data):
    players_count = data["players_count"]
    imposters = data["imposters"]
    words = data["words"]
    current_round = data["current_round"]
    current_index = data["current_index"]

    # المرحلة 0 — تمرير الجوال
    if current_index == -1:
        return {
            "step": 0,
            "player_number": 1
        }

    # داخل اللاعبين
    if current_index < players_count:
        return {
            "step": 1,
            "player_number": current_index + 1,
            "is_imposter": current_index in imposters,
            "secret_word": words[current_round]
        }

    # نهاية الجولة
    return {
        "step": "done",
        "round_number": current_round + 1,
        "is_last_round": current_round == len(words) - 1
    }





import random

def start_imposter_session(request, session_id, secret_word, players_count, imposters_count):
    """
    تُستدعى عند بدء الجلسة (بعد الدفع).
    تجهّز كل المعلومات اللازمة في request.session.
    """

    # ترتيب عرض اللاعبين بشكل طبيعي: [0,1,2,3]
    order = list(range(players_count))

    # اختيار الامبوستر بشكل عشوائي
    imposters = random.sample(order, imposters_count)

    request.session[f"imposter_{session_id}"] = {
        "players_count": players_count,
        "imposters_count": imposters_count,
        "secret_word": secret_word,
        "order": order,
        "imposters": imposters,
        "current_index": -1,   # لم نبدأ بعد
    }

    request.session.modified = True





from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from games.models import GamePackage, ImposterWord, GameSession
import random


@login_required
def imposter_setup(request, package_id):
    """
    إعداد لعبة امبوستر:
    - اختيار عدد اللاعبين
    - اختيار عدد الإمبوسترات
    - اختيار كلمة عشوائية (من الأدمن)
    - تجهيز الجلسة في session
    """

    package = get_object_or_404(
        GamePackage,
        id=package_id,
        game_type="imposter",
        is_active=True
    )

    # الكلمات المفعّلة فقط
    words_qs = ImposterWord.objects.filter(
        package=package,
        is_active=True
    )

    if not words_qs.exists():
        return render(request, "games/imposter/error.html", {
            "message": "لا توجد كلمات مضافة لهذه الحزمة."
        })

    if request.method == "POST":
        try:
            players_count = int(request.POST.get("players_count"))
            imposters_count = int(request.POST.get("imposters_count"))
        except (TypeError, ValueError):
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "error": "بيانات غير صحيحة."
            })

        if players_count < 3:
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "error": "عدد اللاعبين يجب أن يكون 3 على الأقل."
            })

        if imposters_count < 1 or imposters_count >= players_count:
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "error": "عدد الإمبوسترات غير صالح."
            })

        # إنشاء جلسة
        session = GameSession.objects.create(
            host=request.user,
            package=package,
            game_type="imposter",
            is_active=True
        )

        # عدد الجولات:
        # المجانية = كلمة وحدة
        # المدفوعة = 3 كلمات (أو أقل لو ما توفر)
        if package.is_free or package.package_number == 0:
            rounds_count = 1
        else:
            rounds_count = min(3, words_qs.count())

        # اختيار كلمات عشوائية
        words = random.sample(list(words_qs), rounds_count)

        # اختيار الإمبوسترات
        order = list(range(players_count))
        imposters = random.sample(order, imposters_count)

        # حفظ كل شيء في session
        request.session[f"imposter_{session.id}"] = {
            "players_count": players_count,
            "imposters_count": imposters_count,
            "imposters": imposters,
            "words": [w.word for w in words],  # قائمة الكلمات (جولات)
            "current_round": 0,
            "current_index": -1,
        }
        request.session.modified = True

        return redirect("games:imposter_session", session_id=session.id)

    return render(request, "games/imposter/setup.html", {
        "package": package,
    })
