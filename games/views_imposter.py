from django.shortcuts import render
from games.models import GamePackage

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

    return render(request, "games/imposter_packages.html", {
        "packages": packages
    })



from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.utils import timezone
from games.models import GamePackage, GameSession, UserPurchase, ImposterWord
import uuid


@login_required
def create_imposter_session(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid Request")

    package_id = request.POST.get("package_id")
    if not package_id:
        return HttpResponseBadRequest("Missing package")

    package = get_object_or_404(GamePackage, id=package_id, game_type='imposter')

    # إذا الحزمة مجانية → أنشئ جلسة مباشرة
    if package.is_free:
        session = GameSession.objects.create(
            host=request.user,
            package=package,
            game_type="imposter",
            is_active=True
        )
        return redirect(f"/games/imposter/session/{session.id}/")

    # إذا الحزمة مدفوعة → نتأكد هل عنده شراء سابق غير منتهي
    purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=False
    ).first()

    # إذا ما عنده → نرسله للدفع
    if not purchase:
        return redirect(f"/payments/start/{package.id}/")

    # إذا عنده شراء نشط → أنشئ جلسة
    session = GameSession.objects.create(
        host=request.user,
        package=package,
        game_type="imposter",
        purchase=purchase,
        is_active=True
    )

    return redirect(f"/games/imposter/session/{session.id}/")



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
from games.models import GamePackage, ImposterWord, GameSession, UserPurchase
import uuid
import random

def imposter_setup(request, package_id):
    """
    صفحة إعداد الجلسة:
    - عدد اللاعبين
    - عدد الامبوستر
    - الكلمة المختارة
    """

    package = get_object_or_404(GamePackage, id=package_id, game_type="imposter")

    # جلب كلمات الحزمة
    words = package.imposter_words.filter(is_active=True)

    if request.method == "POST":
        players_count   = int(request.POST.get("players"))
        imposters_count = int(request.POST.get("imposters"))
        word_id         = request.POST.get("word_id")

        if imposters_count >= players_count:
            return render(request, "games/imposter/setup.html", {
                "package": package,
                "words": words,
                "error": "عدد الامبوستر يجب أن يكون أقل من عدد اللاعبين.",
            })

        chosen_word = get_object_or_404(ImposterWord, id=word_id, package=package)

        # إنشاء جلسة جديدة في النظام
        # (تمامًا كما تفعل خلية الحروف وتحدي الصور بعد الشراء)
        session = GameSession.objects.create(
            host=request.user,
            package=package,
            game_type="imposter",
            is_active=True
        )

        # تجهيز بيانات الجلسة (في session فقط)
        order = list(range(players_count))
        imposters = random.sample(order, imposters_count)

        request.session[f"imposter_{session.id}"] = {
            "players_count": players_count,
            "imposters_count": imposters_count,
            "secret_word": chosen_word.word,
            "order": order,
            "imposters": imposters,
            "current_index": -1,
        }
        request.session.modified = True

        # تحويل المستخدم إلى صفحة الجلسة
        return redirect(f"/games/imposter/session/{session.id}/")

    return render(request, "games/imposter/setup.html", {
        "package": package,
        "words": words,


    })





from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from .models import GameSession, ImposterWord
import random

def imposter_setup_view(request, session_id):

    session = get_object_or_404(GameSession, id=session_id, game_type="imposter")

    # جيب كلمة من الحزمة
    words_qs = ImposterWord.objects.filter(package=session.package, is_active=True)
    if not words_qs.exists():
        return HttpResponseBadRequest("لا توجد كلمات في هذه الحزمة!")

    # لو POST → نبدأ اللعبة
    if request.method == "POST":
        try:
            players_count = int(request.POST.get("players"))
            imposters_count = int(request.POST.get("imposters"))
        except:
            return HttpResponseBadRequest("بيانات غير صالحة")

        if players_count < 3 or players_count > 20:
            return HttpResponseBadRequest("عدد اللاعبين يجب أن يكون بين 3 و 20.")

        if imposters_count < 1 or imposters_count >= players_count:
            return HttpResponseBadRequest("عدد الإمبوستر غير صالح.")

        # اختيار كلمة عشوائية
        chosen_word = random.choice(list(words_qs)).word

        # وزع الإمبوستر عشوائيًا
        all_players = list(range(players_count))  # 0..players_count-1
        imposters = random.sample(all_players, imposters_count)

        # خزّن البيانات داخل session storage
        key = f"imposter_{session.id}"
        request.session[key] = {
            "players_count": players_count,
            "imposters": imposters,
            "secret_word": chosen_word,
            "current_index": -1,   # قبل أول لاعب
        }
        request.session.modified = True

        # توجيه إلى صفحة تمرير الجوال
        return redirect("games:imposter_session", session_id=session.id)

    # GET → عرض صفحة الإعداد
    return render(request, "games/imposter/setup.html", {
        "session": session,
    })
