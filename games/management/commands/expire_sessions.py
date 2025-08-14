from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from games.models import GameSession

class BaseCommandExpire(BaseCommand):
    help = "تعطيل الجلسات المنتهية (مجانية بعد 1 ساعة / مدفوعة بعد 72 ساعة)."

    def handle(self, *args, **options):
        now = timezone.now()
        # مجانية: 1 ساعة
        free_qs = GameSession.objects.filter(
            is_active=True, package__is_free=True,
            created_at__lt=now - timedelta(hours=1)
        )
        # مدفوعة: 72 ساعة
        paid_qs = GameSession.objects.filter(
            is_active=True, package__is_free=False,
            created_at__lt=now - timedelta(hours=72)
        )
        total = free_qs.update(is_active=False, is_completed=True) + paid_qs.update(is_active=False, is_completed=True)
        self.stdout.write(self.style.SUCCESS(f"Sessions expired: {total}"))