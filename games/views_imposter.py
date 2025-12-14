from django.shortcuts import render
from games.models import GamePackage, ImposterWord
from django.shortcuts import get_object_or_404, redirect

def imposter_start(request, package_id):
    """
    صفحة بداية الحزمة: تعرض وصف الحزمة + عدد الكلمات + زر (ابدأ)
    ثم ينتقل المستخدم لصفحة setup لإدخال عدد اللاعبين.
    """
    package = get_object_or_404(GamePackage, id=package_id, game_type='imposter')

    word_count = package.imposter_words.filter(is_active=True).count()

    return render(request, "games/imposter/start.html", {
        "package": package,
        "word_count": word_count,
    })

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

@require_http_methods(["GET", "POST"])
def imposter_session_view(request, session_id):
    """
    صفحة تمرير الجوال — كل لاعب يشوف دوره فقط.
    """
    session = get_object_or_404(GameSession, id=session_id, game_type="imposter")
    key = f"imposter_{session.id}"

    game_data = request.session.get(key)
    if not game_data:
        return render(request, "games/imposter/error.html", {
            "message": "تعذر تحميل بيانات الجلسة."
        })

    players_count   = game_data["players_count"]
    imposters       = game_data["imposters"]
    secret_word     = game_data["secret_word"]
    current_index   = game_data["current_index"]

    # الانتقال للاعب التالي
    current_index += 1

    # إذا خلصوا اللاعبين → انتهت مرحلة الكشف
    if current_index >= players_count:
        return render(request, "games/imposter/done.html", {
            "session": session,
            "players": players_count,
            "imposters": len(imposters),
        })

    # تحديد دور اللاعب الحالي
    is_imposter = current_index in imposters

    # حفظ التقدم
    game_data["current_index"] = current_index
    request.session[key] = game_data
    request.session.modified = True

    return render(request, "games/imposter/player_screen.html", {
        "session": session,
        "player_number": current_index + 1,
        "is_imposter": is_imposter,
        "secret_word": secret_word if not is_imposter else None,
    })



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
    صفحة إعداد لعبة الامبوستر:
    - اختيار عدد اللاعبين
    - اختيار عدد الإمبوستر
    - اختيار كلمة
    - إنشاء جلسة
    - حفظ بيانات اللعبة في session
    - التحويل لصفحة تمرير الجوال
    """

    # جلب الحزمة
    package = get_object_or_404(
        GamePackage,
        id=package_id,
        game_type="imposter",
        is_active=True
    )

    # جلب الكلمات الفعالة
    words = package.imposter_words.filter(is_active=True)

    if not words.exists():
        return render(request, "games/imposter/error.html", {
            "message": "لا توجد كلمات مفعلة في هذه الحزمة."
        })

    # عند الإرسال
    if request.method == "POST":
        try:
            players_count = int(request.POST.get("players_count"))
            imposters_count = int(request.POST.get("imposters_count"))
            word_id = request.POST.get("word_id")
        except (TypeError, ValueError):
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "words": words,
                "error": "بيانات غير صالحة."
            })

        # تحقق منطقي
        if players_count < 3 or players_count > 20:
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "words": words,
                "error": "عدد اللاعبين يجب أن يكون بين 3 و 20."
            })

        if imposters_count < 1 or imposters_count >= players_count:
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "words": words,
                "error": "عدد الإمبوستر يجب أن يكون أقل من عدد اللاعبين."
            })

        # الكلمة المختارة
        chosen_word = get_object_or_404(
            ImposterWord,
            id=word_id,
            package=package,
            is_active=True
        )

        # إنشاء جلسة جديدة
        session = GameSession.objects.create(
            host=request.user,
            package=package,
            game_type="imposter",
            is_active=True
        )

        # تجهيز بيانات اللعبة
        order = list(range(players_count))
        imposters = random.sample(order, imposters_count)

        request.session[f"imposter_{session.id}"] = {
            "players_count": players_count,
            "imposters_count": imposters_count,
            "imposters": imposters,
            "secret_word": chosen_word.word,
            "order": order,
            "current_index": -1,
        }
        request.session.modified = True

        # تحويل لصفحة تمرير الجوال
        return redirect(f"/games/imposter/session/{session.id}/")

    # GET → عرض صفحة الإعداد
    return render(request, "games/imposter/setup.html", {
        "package": package,
        "words": words,
    })
