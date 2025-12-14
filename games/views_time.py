# games/views_time.py
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_GET, require_POST
from django.template import TemplateDoesNotExist
from django.urls import reverse, NoReverseMatch
from games.models import GamePackage, ImposterWord
from .models import GameSession

# موديلات إضافية (حسب مشروعك)
try:
    from .models import GamePackage  # الحزم (سنستعملها مع game_type='time')
except Exception:
    GamePackage = None

try:
    # تقدّم/ألغاز التحدي (للتوافق مع API القديمة)
    from .models import TimeGameProgress, TimeRiddle
except Exception:
    TimeGameProgress = None
    TimeRiddle = None

# موديلات “تحدي الوقت” الداعمة للتدفق الجديد
try:
    from .models import TimeCategory, TimePlayHistory, TimeSessionPackage
except Exception:
    TimeCategory = None
    TimePlayHistory = None
    TimeSessionPackage = None


# ======================== Helpers ========================

def _is_free_trial_session(session: GameSession) -> bool:
    """
    يحدد إن كانت الجلسة مجانية (تجربة) حتى لو session.package = None.
    نعتمد على وجود أي ربط لحزمة رقم 0 داخل TimeSessionPackage.
    """
    if not session:
        return False
    # إن كانت الجلسة القديمة بحزمة محددة ومعلّم عليها مجانية
    if getattr(session, "package", None) and getattr(session.package, "is_free", False):
        return True
    # الجلسات الجديدة متعددة الحزم
    if TimeSessionPackage and GamePackage:
        return TimeSessionPackage.objects.filter(
            session=session,
            package__game_type="time",
            package__package_number=0,
        ).exists()
    return False


def _is_session_expired(session: GameSession) -> bool:
    """تحقق انتهاء الصلاحية: تجربة=1 ساعة، مدفوعة=72 ساعة."""
    is_trial = _is_free_trial_session(session)
    expiry = session.created_at + (timedelta(hours=1) if is_trial else timedelta(hours=72))
    return timezone.now() >= expiry


def _gen_code(n=12) -> str:
    """مولّد أكواد قصيرة للروابط العامة (عرض/متسابقين)."""
    return get_random_string(n=n, allowed_chars="abcdefghijklmnopqrstuvwxyz0123456789")


def _choose_next_time_package_for_user(user, category, *, allow_zero=True):
    """
    يختار أول حزمة فعّالة داخل الفئة لم يلعبها المستخدم من قبل.
    - إن allow_zero=True يعطي أولوية للحزمة #0 (التجريبية) إن وُجدت ولم تُلعب.
    - إن allow_zero=False يستثني الحزمة #0 (للجولات المدفوعة).
    """
    if not (GamePackage and TimePlayHistory and category):
        return None

    played_pkg_ids = set(
        TimePlayHistory.objects.filter(user=user, category=category).values_list("package_id", flat=True)
    )
    qs = (GamePackage.objects
          .filter(game_type="time", time_category=category, is_active=True)
          .order_by("package_number", "created_at"))
    for pkg in qs:
        if not allow_zero and pkg.package_number == 0:
            continue
        if pkg.id not in played_pkg_ids:
            return pkg
    return None


def _remaining_for(user, category, *, paid_only=False) -> int:
    """
    عدد الحزم المتبقية (الفعّالة) للمستخدم داخل فئة معينة.
    - paid_only=True: نستبعد الحزمة #0 من الحساب.
    """
    if not GamePackage:
        return 0

    base_qs = GamePackage.objects.filter(game_type="time", time_category=category, is_active=True)
    if paid_only:
        base_qs = base_qs.exclude(package_number=0)

    total_pkgs = base_qs.count()

    used = 0
    if TimePlayHistory and user and user.is_authenticated:
        used_qs = TimePlayHistory.objects.filter(user=user, category=category)
        if paid_only:
            used_qs = used_qs.exclude(package__package_number=0)
        used = used_qs.values("package_id").distinct().count()

    return max(0, total_pkgs - used)


# ======================== Home (Packages/Categories) ========================

def time_home(request):
    """
    صفحة اختيار الفئات/الحزم لتحدّي الوقت:
    - تبرز الفئات المجانية بالأعلى (عادة تحتوي الحزمة 0 فقط للتجربة).
    - الجولات المتاحة:
        * تجربة مجانية: 4 فئات (كلها من فئات is_free_category=True).
        * مدفوعة:       8 فئات (قد تكون مدفوعة أو خليط؟ — نمنع المزج في السيرفر).
    القالب: games/time/packages.html
    """
    if not TimeCategory:
        return HttpResponse("تحدّي الوقت — صفحة الحزم (قريبًا).", content_type="text/plain; charset=utf-8")

    user = request.user if request.user.is_authenticated else None

    cats = (TimeCategory.objects
            .filter(is_active=True)
            .order_by("-is_free_category", "order", "name"))

    # احسب المتبقي لكل فئة (للمعلومة فقط في الواجهة)
    remaining_map = {c.id: _remaining_for(user, c, paid_only=False) for c in cats}

    context = {
        "page_title": "تحدّي الوقت — اختيار الفئات",
        "categories": cats,
        "remaining_map": remaining_map,  # {cat_id: remaining}
        # قيم للواجهة الحالية + قيم إضافية ستفيدك لاحقًا عند تحديث الـ JS
        "bundle_size": 8,            # الحجم الافتراضي (المدفوع)
        "trial_bundle_size": 4,      # للتجربة
        "fixed_price_sar": 20,       # سعر الجولة المدفوعة
        "per_cat_price": 2.5,        # فقط لعرض تقديري في الواجهة الحالية (20/8)
        "bundle_discount_pct": 0,    # لا يوجد خصم — السعر ثابت
    }
    try:
        return render(
            request,
            "games/time/packages.html",
            context,
            content_type="text/html; charset=utf-8",
        )
    except TemplateDoesNotExist:
        # احتياط لو القالب غير موجود
        lines = ["تحدّي الوقت — اختيار الفئات:"]
        for c in cats:
            rem = remaining_map.get(c.id, 0)
            flag = "🆓" if c.is_free_category else "💳"
            lines.append(f"- {flag} {c.name} (متبقي: {rem})")
        return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


# ======================== Create Session (Selection & Payment) ========================

@login_required
def create_time_session(request):
    """
    إنشاء جلسة (تحدّي الوقت) وفق نمطين:
    1) جولة تجريبية (مجانية): 4 فئات بالضبط — يجب أن تكون كل الفئات من فئات free_category
       ويتم ربط كل فئة بحزمة #0 فقط.
    2) جولة مدفوعة: 8 فئات بالضبط — وجود أي فئة غير مجانية ⇒ تحويل لبوابة الدفع (سعر ثابت 20 ر.س للجولة كاملة).

    ملاحظات:
    - نتعامل بمرونة مع أسماء الحقول القادمة من الواجهة:
      * selected_category_ids = "1,2,3" (CSV)
      * category_ids[] أو category_ids = [1,2,3]
    - نمنع المزج بين فئات مجانية ومدفوعة في نفس الطلب.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("طريقة الطلب غير صحيحة")

    if not TimeCategory:
        return HttpResponseBadRequest("النظام غير مهيأ بعد (TimeCategory غير متاح).")

    # ===== 1) قراءة القيم القادمة من الفورم بمرونة =====
    raw_csv = (request.POST.get("selected_category_ids") or "").strip()
    if raw_csv:
        try:
            cat_ids = [int(x) for x in raw_csv.split(",") if x.strip()]
        except Exception:
            return HttpResponseBadRequest("قائمة الفئات غير صالحة (CSV).")
    else:
        lst = request.POST.getlist("category_ids[]") or request.POST.getlist("category_ids")
        try:
            cat_ids = [int(x) for x in lst if x]
        except Exception:
            return HttpResponseBadRequest("قائمة الفئات غير صالحة")

    if not cat_ids:
        return HttpResponseBadRequest("لم يتم اختيار أي فئة")

    # ===== 2) جلب الفئات والتحقق من الفعالية =====
    cats = list(TimeCategory.objects.filter(id__in=cat_ids, is_active=True))
    if len(cats) != len(cat_ids):
        return HttpResponseBadRequest("إحدى الفئات غير متاحة")

    # منع المزج بين فئات مجانية ومدفوعة في نفس الطلب
    has_free_cats = any(c.is_free_category for c in cats)
    has_paid_cats = any(not c.is_free_category for c in cats)
    if has_free_cats and has_paid_cats:
        return HttpResponseBadRequest("لا يمكن المزج بين فئات مجانية وفئات مدفوعة في نفس الجولة")

    fixed_price_sar = 20  # السعر الثابت للجولة المدفوعة كاملة

    # ===== 3) النمط المدفوع =====
    if has_paid_cats:
        if len(cats) != 8:
            return HttpResponseBadRequest("يجب اختيار 8 فئات بالضبط للجولة المدفوعة")

        # تحقّق أن هناك حزم مدفوعة متبقية في كل فئة (نستبعد #0)
        for c in cats:
            if _remaining_for(request.user, c, paid_only=True) <= 0:
                return HttpResponseBadRequest(f"الفئة ({c.name}) لا تحتوي حزمًا مدفوعة متاحة لهذا الحساب")

        # خزّن الاختيارات ثم وجّه لبوابة الدفع
        request.session["time_selected_category_ids"] = [c.id for c in cats]
        try:
            checkout_url = reverse("payments:create_time_checkout") + f"?amount={fixed_price_sar}"
        except NoReverseMatch:
            checkout_url = f"/payments/time-checkout/?amount={fixed_price_sar}"
        return redirect(checkout_url)

    # ===== 4) النمط المجاني (تجربة) =====
    if len(cats) != 4:
        return HttpResponseBadRequest("يجب اختيار 4 فئات بالضبط لجولة التجربة المجانية")

    # يجب وجود حزمة #0 مفعلة لكل فئة
    if not (GamePackage and TimeSessionPackage):
        return HttpResponseBadRequest("النظام غير مهيأ بعد (الحزم/الربط غير متاح).")

    from django.db import transaction
    with transaction.atomic():
        session = GameSession.objects.create(
            host=request.user,           # ← بدّلنا من user إلى host
            game_type="time",
            package=None,                # جلسة متعددة الحزم
            team1_name="الفريق A",
            team2_name="الفريق B",
            display_link=_gen_code(12),
            contestants_link=_gen_code(12),
            is_active=True,
        )
        for c in cats:
            pkg0 = GamePackage.objects.filter(
                game_type="time", time_category=c, is_active=True, package_number=0
            ).first()
            if not pkg0:
                transaction.set_rollback(True)
                return HttpResponseBadRequest(f"لا توجد حزمة تجريبية (#0) مفعّلة لفئة {c.name}")
            TimeSessionPackage.objects.create(session=session, category=c, package=pkg0)

    return redirect("games:time_host", session_id=session.id)


@login_required
def finalize_time_checkout(request):
    """
    تُستدعى بعد نجاح الدفع من البوابة (return_url).
    تعتمد على session var: time_selected_category_ids
    وتنشئ الجلسة وتخصّص حزمة (مدفوعة) لكل فئة، ثم تحوّل للمقدم.
    """
    if not (TimeCategory and GamePackage and TimeSessionPackage):
        return HttpResponseBadRequest("النظام غير مهيأ بعد.")

    cat_ids = request.session.get("time_selected_category_ids") or []
    if not cat_ids or len(cat_ids) != 8:
        return HttpResponseBadRequest("لا توجد فئات محفوظة للجولة المدفوعة")

    cats = list(TimeCategory.objects.filter(id__in=cat_ids, is_active=True))
    if len(cats) != 8:
        return HttpResponseBadRequest("بعض الفئات لم تعد متاحة")

    # تأكد أن هناك حزم مدفوعة متاحة
    for c in cats:
        if _remaining_for(request.user, c, paid_only=True) <= 0:
            return HttpResponseBadRequest(f"الفئة ({c.name}) لا تحتوي حزمًا مدفوعة متاحة لهذا الحساب")

    from django.db import transaction
    with transaction.atomic():
        session = GameSession.objects.create(
            host=request.user,          # ← بدّلنا من user إلى host
            game_type="time",
            package=None,
            team1_name="الفريق A",
            team2_name="الفريق B",
            display_link=_gen_code(12),
            contestants_link=_gen_code(12),
            is_active=True,
        )
        for c in cats:
            # للمدفوع: لا نسمح بالحزمة #0
            pkg = _choose_next_time_package_for_user(request.user, c, allow_zero=False)
            if not pkg:
                transaction.set_rollback(True)
                return HttpResponseBadRequest(f"لا توجد حزمة مدفوعة متاحة الآن لفئة {c.name}")
            TimeSessionPackage.objects.create(session=session, category=c, package=pkg)

    # نظّف المتغيرات المؤقتة
    request.session.pop("time_selected_category_ids", None)

    return redirect("games:time_host", session_id=session.id)


# ======================== API: تحديث أسماء الفرق ========================

@login_required
@require_POST
def api_time_update_team_names(request):
    """
    يحدّث أسماء الفريقين للجلسة من قِبل المقدم (المالك) أو staff.
    POST:
      - session_id (uuid)
      - team1_name
      - team2_name
    """
    session_id = request.POST.get("session_id")
    session = get_object_or_404(GameSession, id=session_id, game_type="time")

    # صلاحية: المالك (host) أو موظف
    if not (request.user.is_staff or (session.host_id == request.user.id)):  # ← استبدال user بـ host
        return JsonResponse({"success": False, "detail": "permission_denied"}, status=403)

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({"success": False, "detail": "expired"}, status=410)

    t1 = (request.POST.get("team1_name") or "").strip()
    t2 = (request.POST.get("team2_name") or "").strip()
    if not t1 or not t2:
        return JsonResponse({"success": False, "detail": "invalid_names"}, status=400)

    session.team1_name = t1[:50]
    session.team2_name = t2[:50]
    session.save(update_fields=["team1_name", "team2_name"])

    return JsonResponse({"success": True, "team1_name": session.team1_name, "team2_name": session.team2_name})


# ======================== Session Pages ========================

def time_host(request, session_id):
    """
    صفحة المقدم (المتحكم) لجلسة تحوي عدة فئات/حزم.
    """
    session = get_object_or_404(GameSession, id=session_id, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if _is_free_trial_session(session) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة.",
            },
            status=410,
        )

    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related("category", "package")
            .order_by("category__order", "category__name")
        )

    try:
        return render(
            request,
            "games/time/time_host.html",
            {
                "session": session,
                "page_title": f"المقدم — {session.team1_name} ضد {session.team2_name}",
                "time_session_packages": tsp_list,
            },
            content_type="text/html; charset=utf-8",
        )
    except TemplateDoesNotExist:
        return HttpResponse("واجهة المقدم (قريبًا).", content_type="text/plain; charset=utf-8")


def time_display(request, display_link):
    """شاشة العرض (للمشاهدين)."""
    session = get_object_or_404(GameSession, display_link=display_link, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if _is_free_trial_session(session) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة.",
            },
            status=410,
        )

    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related("category", "package")
            .order_by("category__order", "category__name")
        )

    return render(
        request,
        "games/time/time_display.html",
        {
            "session": session,
            "page_title": f"{session.team1_name} ضد {session.team2_name} — شاشة العرض",
            "time_session_packages": tsp_list,
        },
        content_type="text/html; charset=utf-8",
    )


def time_contestants(request, contestants_link):
    """صفحة المتسابقين (زر التبديل والمؤقت لكل لاعب)."""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if _is_free_trial_session(session) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة.",
            },
            status=410,
        )

    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related("category", "package")
            .order_by("category__order", "category__name")
        )

    try:
        return render(
            request,
            "games/time/time_contestants.html",
            {
                "session": session,
                "page_title": f"المتسابقون — {session.team1_name} ضد {session.team2_name}",
                "time_session_packages": tsp_list,
            },
            content_type="text/html; charset=utf-8",
        )
    except TemplateDoesNotExist:
        return HttpResponse("واجهة المتسابقين (قريبًا).", content_type="text/plain; charset=utf-8")


# ======================== API (initial sync — legacy/simple) ========================

@require_GET
def api_time_get_current(request):
    """
    API قديمة للتماشي مع قوالب قديمة مبنية على "حزمة واحدة"؛
    في التدفق الجديد، يُفضّل الاعتماد على WebSocket لكل تبويب/حزمة.
    """
    session_id = request.GET.get("session_id")
    session = get_object_or_404(GameSession, id=session_id, game_type="time")

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({"detail": "expired"}, status=410)

    # تأكيد/تهيئة التقدّم القديم
    progress = None
    if TimeGameProgress:
        progress, _ = TimeGameProgress.objects.get_or_create(
            session=session,
            defaults={
                "current_index": 1,
                "active_team": "team1",
                "team1_ms": 60000,
                "team2_ms": 60000,
            },
        )

    active_team = getattr(progress, "active_team", "team1")
    team1_ms = getattr(progress, "team1_ms", 60000)
    team2_ms = getattr(progress, "team2_ms", 60000)
    current_index = getattr(progress, "current_index", 1)

    # الصورة الحالية من TimeRiddle إن كانت الجلسة القديمة بحزمة واحدة
    cur = {}
    total = 0
    if TimeRiddle and GamePackage and session.package:
        riddles = list(
            TimeRiddle.objects.filter(package=session.package)
            .order_by("order")
            .values("image_url", "hint", "answer")
        )
        total = len(riddles)
        if 1 <= current_index <= total:
            cur = riddles[current_index - 1]

    return JsonResponse(
        {
            "success": True,
            "active_team": active_team,
            "team1_ms": int(team1_ms),
            "team2_ms": int(team2_ms),
            "current_index": int(current_index),
            "count": int(total or 0),
            "current": cur or {},
        }
    )




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