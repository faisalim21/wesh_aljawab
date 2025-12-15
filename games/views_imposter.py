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


from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from games.models import GameSession


@login_required
def imposter_session_view(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)

    # (اختياري للأمان) لا أحد يدخل الجلسة إلا صاحبها
    if session.host_id and session.host_id != request.user.id:
        return redirect("games:imposter_packages")

    key = f"imposter_{session.id}"
    game_data = request.session.get(key)

    # لو ما فيه بيانات (مثلاً سوا ريفرش بعد ما انتهت السيشن/انمسحت)
    if not game_data:
        return redirect("games:imposter_setup", package_id=session.package_id)

    players_count = int(game_data.get("players_count", 0))
    imposters = set(game_data.get("imposters", []))
    words = game_data.get("words", [])
    current_round = int(game_data.get("current_round", 0))

    # حماية بسيطة
    if players_count < 3 or not words or current_round < 0 or current_round >= len(words):
        return redirect("games:imposter_setup", package_id=session.package_id)

    secret_word = words[current_round]

    # ---------
    # الحالة التي نحتاجها عشان ما "يفضحها":
    # current_index: رقم اللاعب الحالي (0-based)
    # reveal: هل الآن في شاشة كشف الدور؟ (True) أو شاشة "أعط الجوال" (False)
    # ---------
    if "current_index" not in game_data:
        game_data["current_index"] = 0
    if "reveal" not in game_data:
        game_data["reveal"] = False

    current_index = int(game_data["current_index"])
    reveal = bool(game_data["reveal"])

    # --------- POST: زر التالي ---------
    if request.method == "POST":
        step = request.POST.get("step", "")

        # من شاشة "أعط الجوال" -> نكشف الدور لنفس اللاعب
        if step == "1":
            game_data["reveal"] = True

        # من شاشة كشف الدور -> نروح للي بعده (شاشة "أعط الجوال")
        elif step == "next":
            if game_data.get("reveal"):
                game_data["current_index"] = int(game_data.get("current_index", 0)) + 1
                game_data["reveal"] = False

        request.session[key] = game_data
        request.session.modified = True

        return redirect("games:imposter_session", session_id=session.id)

    # --------- GET: عرض الشاشة حسب الحالة ---------
    current_index = int(game_data.get("current_index", 0))
    reveal = bool(game_data.get("reveal", False))

    # انتهينا من تمرير الجوال على كل اللاعبين
    if current_index >= players_count:
        return render(request, "games/imposter/session.html", {
            "session": session,
            "step": "done",
        })

    # شاشة "أعط الجوال للاعب رقم X"
    if not reveal:
        return render(request, "games/imposter/session.html", {
            "session": session,
            "step": 0,
            "player_number": current_index + 1,
        })

    # شاشة كشف الدور للاعب رقم X
    is_imposter = (current_index in imposters)
    return render(request, "games/imposter/session.html", {
        "session": session,
        "step": 1,
        "player_number": current_index + 1,  # ✅ عشان ما يطلع 0
        "is_imposter": is_imposter,
        "secret_word": secret_word,
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
