from django.shortcuts import render
from games.models import GamePackage, ImposterWord
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required

from games.models import (
    GamePackage,
    ImposterWord,
    GameSession,
    UserPurchase,   # ✅ هذا الناقص
)

@login_required
def imposter_start(request, package_id):
    package = get_object_or_404(
        GamePackage,
        id=package_id,
        game_type='imposter'
    )

    # لو الحزمة مجانية → مباشرة صفحة الإعداد
    if package.is_free:
        return redirect('games:imposter_setup', package_id=package.id)

    # لو مدفوعة → نتحقق من الشراء
    purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=False
    ).first()

    # ما اشترى → نرسله للدفع
    if not purchase:
        return redirect(f"/payments/start/{package.id}/")

    # اشترى → نوديه للإعداد
    return redirect('games:imposter_setup', package_id=package.id)



def imposter_home(request):
    packages = GamePackage.objects.filter(
        game_type="imposter",
        is_active=True
    ).order_by("package_number")

    return render(request, "games/imposter/packages.html", {
        "packages": packages
    })


from django.shortcuts import render
from django.db.models import Count
from .models import GamePackage

def imposter_packages(request):
    packages = (
        GamePackage.objects
        .filter(game_type='imposter', is_active=True)
        .annotate(word_count=Count('imposter_words'))
        .order_by('package_number')
    )

    return render(request, "games/imposter/packages.html", {
        "packages": packages
    })


from django.shortcuts import render, redirect
from django.http import Http404
from django.views.decorators.http import require_http_methods

from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .models import (
    GameSession,
    ImposterWord,
)

import random


@require_http_methods(["GET"])
def imposter_session_view(request, session_id):
    session = get_object_or_404(
        GameSession,
        id=session_id,
        game_type="imposter"
    )

    # ---------------------------
    # إعداد بيانات الجلسة
    # ---------------------------
    game_data = request.session.get(f"imposter_game_{session.id}")

    if not game_data:
        # حماية: لو دخل الصفحة بدون إعداد
        return redirect("games:imposter_setup", session.package.id)

    players_count   = game_data["players_count"]
    imposters_count = game_data["imposters_count"]
    words            = game_data["words"]          # قائمة الكلمات
    imposters_map    = game_data["imposters_map"]  # dict: player_index -> True/False

    current_index = game_data.get("current_player", 0)
    current_round = game_data.get("current_round", 0)

    # ---------------------------
    # انتهاء الجولة
    # ---------------------------
    if current_index >= players_count:
        game_data["current_player"] = 0
        game_data["current_round"] += 1
        request.session[f"imposter_game_{session.id}"] = game_data
        return redirect("games:imposter_session", session.id)

    if current_round >= len(words):
        # انتهت كل الجولات
        del request.session[f"imposter_game_{session.id}"]
        return render(request, "games/imposter/finished.html", {
            "session": session
        })

    # ---------------------------
    # بيانات اللاعب الحالي
    # ---------------------------
    is_imposter = imposters_map.get(str(current_index), False)
    secret_word = words[current_round]

    # ---------------------------
    # التحكم في الكشف
    # ---------------------------
    reveal = request.GET.get("reveal") == "1"

    # لو تم الكشف ثم ضغط التالي → ننتقل للاعب التالي
    if reveal and request.GET.get("next") == "1":
        game_data["current_player"] += 1
        request.session[f"imposter_game_{session.id}"] = game_data
        return redirect("games:imposter_session", session.id)

    # ---------------------------
    # العرض
    # ---------------------------
    context = {
        "session": session,
        "player_number": current_index + 1,
        "is_imposter": is_imposter,
        "secret_word": None if is_imposter else secret_word,
        "round_number": current_round + 1,
        "total_rounds": len(words),
        "reveal": reveal,
    }

    return render(request, "games/imposter/player_screen.html", context)


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
