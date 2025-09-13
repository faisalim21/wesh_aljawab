# games/views_time.py
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_GET, require_POST
from django.template import TemplateDoesNotExist
from django.urls import reverse, NoReverseMatch
from django.db.models import Count

from .models import GameSession

# موديلات إضافية (اختيارية حسب مشروعك)
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

# موديلات “تحدي الوقت” الجديدة/الداعمة للتدفق
try:
    from .models import TimeCategory, TimePlayHistory, TimeSessionPackage
except Exception:
    TimeCategory = None
    TimePlayHistory = None
    TimeSessionPackage = None


# ======================== Helpers ========================

def _is_session_expired(session: GameSession) -> bool:
    """تحقق انتهاء الصلاحية بحسب نوع الباقة (مجانية=1 ساعة، مدفوعة=72 ساعة)."""
    expiry = session.created_at + (
        timedelta(hours=1) if session.package and session.package.is_free else timedelta(hours=72)
    )
    return timezone.now() >= expiry


def _gen_code(n=12) -> str:
    """مولّد أكواد قصيرة للروابط العامة (عرض/متسابقين)."""
    return get_random_string(n=n, allowed_chars="abcdefghijklmnopqrstuvwxyz0123456789")


def _choose_next_time_package_for_user(user, category):
    """
    يختار أول حزمة فعّالة (game_type='time', ضمن الفئة) لم يلعبها المستخدم من قبل.
    - يعطي أولوية للحزمة 0 إذا كانت موجودة ولم تُلعب.
    - ثم يُرتب تصاعديًا بالرقم.
    """
    if not (GamePackage and TimePlayHistory and category):
        return None
    played_pkg_ids = set(
        TimePlayHistory.objects.filter(user=user, category=category).values_list('package_id', flat=True)
    )
    qs = (GamePackage.objects
          .filter(game_type='time', time_category=category, is_active=True)
          .order_by('package_number', 'created_at'))
    for pkg in qs:
        if pkg.id not in played_pkg_ids:
            return pkg
    return None


def _remaining_for(user, category) -> int:
    """عدد الحزم المتبقية (الفعّالة) للمستخدم داخل فئة معينة."""
    if not GamePackage:
        return 0
    total_pkgs = GamePackage.objects.filter(game_type='time', time_category=category, is_active=True).count()
    used = 0
    if TimePlayHistory and user and user.is_authenticated:
        used = TimePlayHistory.objects.filter(user=user, category=category).count()
    return max(0, total_pkgs - used)


# ======================== Home (Packages/Categories) ========================

def time_home(request):
    """
    صفحة اختيار الفئات/الحزم لتحدّي الوقت:
    - تبرز الفئات المجانية بالأعلى (عادة تحتوي الحزمة 0 فقط للتجربة).
    - يختار اللاعب 8 فئات بالضبط.
    - تعرض شارة "المتبقي" لكل فئة = (حزم فعّالة) - (ما لعبه المستخدم).
    القالب: games/time/packages.html
    """
    if not TimeCategory:
        return HttpResponse("تحدّي الوقت — صفحة الحزم (قريبًا).", content_type="text/plain; charset=utf-8")

    user = request.user if request.user.is_authenticated else None

    cats = (TimeCategory.objects
            .filter(is_active=True)
            .order_by('-is_free_category', 'order', 'name'))

    # احسب المتبقي لكل فئة
    remaining_map = {c.id: _remaining_for(user, c) for c in cats}

    context = {
        "page_title": "تحدّي الوقت — اختر 8 فئات",
        "categories": cats,
        "remaining_map": remaining_map,  # {cat_id: remaining}
        "must_pick": 8,
        "fixed_price_sar": 20,          # السعر الثابت بعد الخصم
    }
    try:
        return render(
            request,
            "games/time/packages.html",
            context,
            content_type="text/html; charset=utf-8",  # يحسم الترميز
        )
    except TemplateDoesNotExist:
        # احتياط لو القالب غير موجود
        lines = ["تحدّي الوقت — اختر 8 فئات:"]
        for c in cats:
            rem = remaining_map.get(c.id, 0)
            flag = "🆓" if c.is_free_category else "💳"
            lines.append(f"- {flag} {c.name} (متبقي: {rem})")
        return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


# ======================== Create Session (Selection & Payment) ========================

@login_required
def create_time_session(request):
    """
    إنشاء جلسة (تحدّي الوقت) بعد اختيار 8 فئات.
    التدفق:
    - يتحقق أن 8 فئات بالضبط وصلت (POST['category_ids[]']).
    - لو أي فئة ليست free_category ⇒ يحتاج دفع (20 ريال ثابت).
      * نخزّن الاختيار مؤقتًا في session ونحوّل لبوابة الدفع.
    - لو كلها مجانية ⇒ ننشئ الجلسة فورًا ونخصّص حزمة واحدة لكل فئة.
    - التخصيص: نختار أول حزمة فعّالة لم تُلعب سابقًا. إن نفدت ⇒ منع.

    ⚠️ أسماء الفرق لا تُطلب هنا — يكتبها المقدم من واجهة المقدم لاحقًا.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("طريقة الطلب غير صحيحة")

    if not TimeCategory:
        return HttpResponseBadRequest("النظام غير مهيأ بعد (TimeCategory غير متاح).")

    # قائمة الفئات المختارة (بالضبط 8)
    cat_ids = request.POST.getlist("category_ids[]") or request.POST.getlist("category_ids")
    try:
        cat_ids = [int(x) for x in cat_ids if x]
    except Exception:
        return HttpResponseBadRequest("قائمة الفئات غير صالحة")

    if len(cat_ids) != 8:
        return HttpResponseBadRequest("يجب اختيار 8 فئات بالضبط")

    cats = list(TimeCategory.objects.filter(id__in=cat_ids, is_active=True))
    if len(cats) != 8:
        return HttpResponseBadRequest("إحدى الفئات غير متاحة")

    # التحقق من النفاد (لا تسمح بفئة متبقية=0)
    for c in cats:
        if _remaining_for(request.user, c) <= 0:
            return HttpResponseBadRequest(f"الفئة ({c.name}) نفدت حزمها لهذا الحساب")

    # هل بين الاختيارات فئات غير مجانية؟ (تستوجب دفع)
    needs_payment = any(not c.is_free_category for c in cats)
    fixed_price_sar = 20

    if needs_payment:
        # خزّن الاختيارات مؤقتًا ثم وجّه لبوابة الدفع
        request.session['time_selected_category_ids'] = cat_ids

        # حاول استخدام مسار بوابة دفع مسمى؛ وإلا وفّر عنوانًا احتياطيًا
        try:
            checkout_url = reverse('payments:create_time_checkout') + f"?amount={fixed_price_sar}"
        except NoReverseMatch:
            checkout_url = f"/payments/time-checkout/?amount={fixed_price_sar}"
        return redirect(checkout_url)

    # لا يحتاج دفع (كلها مجانية) ⇒ أنشئ الجلسة وخصص الحزم الآن
    from django.db import transaction
    if not (GamePackage and TimeSessionPackage):
        return HttpResponseBadRequest("النظام غير مهيأ بعد (الحزم/الربط غير متاح).")

    with transaction.atomic():
        session = GameSession.objects.create(
            user=request.user,
            game_type="time",
            package=None,  # هذه الجلسة تحمل 8 فئات؛ الحزم ستخزن في TimeSessionPackage
            team1_name="الفريق A",   # أسماء افتراضية — سيعدلها المقدم لاحقًا
            team2_name="الفريق B",
            display_link=_gen_code(12),
            contestants_link=_gen_code(12),
            is_active=True,
        )
        # تخصيص حزمة واحدة/فئة
        for c in cats:
            pkg = _choose_next_time_package_for_user(request.user, c)
            if not pkg:
                transaction.set_rollback(True)
                return HttpResponseBadRequest(f"لا توجد حزمة متاحة الآن لفئة {c.name}")
            TimeSessionPackage.objects.create(session=session, category=c, package=pkg)

    return redirect("games:time_host", session_id=session.id)


@login_required
def finalize_time_checkout(request):
    """
    تُستدعى بعد نجاح الدفع من البوابة (return_url).
    تعتمد على session var: time_selected_category_ids
    وتنشئ الجلسة وتخصّص حزمة لكل فئة، ثم تحوّل للمقدم.

    ⚠️ الفريقان بأسماء افتراضية — المقدم سيُعدّلها من واجهة المقدم.
    """
    if not (TimeCategory and GamePackage and TimeSessionPackage):
        return HttpResponseBadRequest("النظام غير مهيأ بعد.")

    cat_ids = request.session.get('time_selected_category_ids') or []
    if not cat_ids or len(cat_ids) != 8:
        return HttpResponseBadRequest("لا توجد فئات محفوظة")

    cats = list(TimeCategory.objects.filter(id__in=cat_ids, is_active=True))
    if len(cats) != 8:
        return HttpResponseBadRequest("بعض الفئات لم تعد متاحة")

    # منع الفئات النافدة
    for c in cats:
        if _remaining_for(request.user, c) <= 0:
            return HttpResponseBadRequest(f"الفئة ({c.name}) نفدت حزمها لهذا الحساب")

    from django.db import transaction
    with transaction.atomic():
        session = GameSession.objects.create(
            user=request.user,
            game_type="time",
            package=None,
            team1_name="الفريق A",   # أسماء افتراضية — سيعدلها المقدم لاحقًا
            team2_name="الفريق B",
            display_link=_gen_code(12),
            contestants_link=_gen_code(12),
            is_active=True,
        )
        for c in cats:
            pkg = _choose_next_time_package_for_user(request.user, c)
            if not pkg:
                transaction.set_rollback(True)
                return HttpResponseBadRequest(f"لا توجد حزمة متاحة الآن لفئة {c.name}")
            TimeSessionPackage.objects.create(session=session, category=c, package=pkg)

    # نظّف المتغيرات المؤقتة
    request.session.pop('time_selected_category_ids', None)

    return redirect("games:time_host", session_id=session.id)


# ======================== API: تحديث أسماء الفرق (يحدده المقدم) ========================

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

    # صلاحية: المالك أو موظف
    if not (request.user.is_staff or (session.user_id == request.user.id)):
        return JsonResponse({"success": False, "detail": "permission_denied"}, status=403)

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({"success": False, "detail": "expired"}, status=410)

    t1 = (request.POST.get("team1_name") or "").strip()
    t2 = (request.POST.get("team2_name") or "").strip()
    if not t1 or not t2:
        return JsonResponse({"success": False, "detail": "invalid_names"}, status=400)

    # حدود بسيطة للطول
    session.team1_name = t1[:50]
    session.team2_name = t2[:50]
    session.save(update_fields=["team1_name", "team2_name"])

    return JsonResponse({"success": True, "team1_name": session.team1_name, "team2_name": session.team2_name})


# ======================== Session Pages ========================

def time_host(request, session_id):
    """
    صفحة المقدم (المتحكم).
    - الآن الجلسة قد تحمل 8 فئات؛ نمرّر قائمة TimeSessionPackage للقالب.
    - القالب سيعرض تبويبات/شبكة للفئات المُختارة، وكل تبويب يستخدم الحزمة الخاصة به.
    - المقدم يستطيع تعديل أسماء الفريقين عبر استدعاء api_time_update_team_names.
    """
    session = get_object_or_404(GameSession, id=session_id, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )

    # اجلب ربط الفئات/الحزم لهذه الجلسة (إن وُجد)
    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related('category', 'package')
            .order_by('category__order', 'category__name')
        )

    try:
        return render(
            request,
            "games/time/time_host.html",
            {
                "session": session,
                "page_title": f"المقدم — {session.team1_name} ضد {session.team2_name}",
                "time_session_packages": tsp_list,  # [{category, package}, ...]
                # نقطة مهمة للواجهة: اجعل حقول اسم الفريقين تستدعي api_time_update_team_names
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
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )

    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related('category', 'package')
            .order_by('category__order', 'category__name')
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
    """صفحة المتسابقين (زر التبديل وعرض المؤقت لكل لاعب أمامه)."""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )

    tsp_list = []
    if TimeSessionPackage:
        tsp_list = list(
            TimeSessionPackage.objects
            .filter(session=session)
            .select_related('category', 'package')
            .order_by('category__order', 'category__name')
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
    تُستخدم في بداية شاشة العرض لحمل:
    - الفريق النشط
    - رصيد الوقت لكل فريق بالملي ثانية
    - الصورة الحالية (من TimeRiddle على نفس الحزمة)

    ملاحظة: هذه API متوافقة مع نموذج "حزمة واحدة" القديم.
    بعد انتقالنا لتعدد الفئات/الحزم داخل الجلسة الواحدة، يُفضّل أن تُحمّل
    واجهة الويب البيانات عبر WebSocket لكل حزمة نشطة/تبويب.
    الإبقاء عليها هنا للانسجام مع قوالب قديمة إن وُجدت.
    """
    session_id = request.GET.get("session_id")
    session = get_object_or_404(GameSession, id=session_id, game_type="time")

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({"detail": "expired"}, status=410)

    # تأكيد/تهيئة التقدّم
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

    # الصورة الحالية من TimeRiddle (اختياري) — تستخدم الحزمة المرتبطة بالجلسة (القديمة)
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
