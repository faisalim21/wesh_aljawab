# games/consumers.py - محسّن للسرعة والربط الفوري

import json
import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

from games.models import GameSession, Contestant

logger = logging.getLogger('games')


class LettersGameConsumer(AsyncWebsocketConsumer):
    """
    Consumer محسّن للسرعة مع ربط فوري بين الصفحات:
    - المتسابق يضغط → فوري لشاشة العرض + المقدم
    - قفل 3 ثواني تلقائي
    - صوتيات وعد تنازلي مترابط
    """

    async def broadcast_letters_replace(self, event):
        """
        استقبال بث تغيير ترتيب الحروف من الـAPI.
        يُرسل للمقدم وشاشة العرض (وليس المتسابقين).
        """
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'letters_updated',
            'letters': event.get('letters', []),
            'reset_progress': bool(event.get('reset_progress', False)),
        }))


    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        # استخرج الدور + host_token من الـ QueryString
        qs = self._parse_qs()
        self.role = qs.get('role', ['viewer'])[0]
        self.host_token_qs = qs.get('host_token', [None])[0]

        # التحقق من الجلسة
        try:
            self.session = await self.get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        # منع التشغيل على جلسة منتهية/غير نشطة
        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return

        # صلاحيات المقدم
        if self.role == 'host':
            user = self.scope.get('user')
            if not user or not user.is_authenticated or user.id != self.session.host_id:
                await self.close(code=4403)
                return

            # تحقق host_token
            expected_token = cache.get(self._host_token_key(self.session_id))
            if not expected_token or self.host_token_qs != expected_token:
                await self.close(code=4403)
                return

        # الانضمام للمجموعة
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WS connected: session={self.session_id}, role={self.role}")

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass
        logger.info(f"WS disconnected: session={self.session_id}, role={self.role}, code={close_code}")

    async def receive(self, text_data: str):
        # إغلاق أنيق إذا انتهت الجلسة
        if await self._is_session_expired(self.session) or not self.session.is_active:
            try:
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'انتهت صلاحية الجلسة'}))
            finally:
                await self.close(code=4401)
            return

        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return

        message_type = data.get('type')

        try:
            # keep-alive
            if message_type == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
                return

            # المتسابق: إرسال buzz فوري
            if message_type == "contestant_buzz" and self.role == "contestant":
                await self.handle_contestant_buzz_instant(data)
                return

            # المقدم: أوامر التحكم
            if self.role == "host":
                if message_type == "update_cell_state":
                    await self.handle_update_cell_state(data)
                    return
                if message_type == "update_scores":
                    await self.handle_update_scores(data)
                    return
                if message_type == "buzz_reset":
                    await self.handle_buzz_reset()
                    return

        except Exception as e:
            logger.error(f"WS handler error for {message_type}: {e}")

    # -------------------------
    # Handlers محسّنة للسرعة
    # -------------------------
    async def handle_contestant_buzz_instant(self, data):
        """
        معالجة فورية للـ buzz:
        1. تحقق من القفل
        2. إذا متاح: حجز فوري + بث لكل الصفحات
        3. رد مباشر للمتسابق
        4. تشغيل مؤقت 3 ثواني تلقائي
        """
        contestant_name = (data.get("contestant_name") or "").strip()
        team = data.get("team")
        timestamp = data.get("timestamp")

        if not contestant_name or team not in ("team1", "team2"):
            await self._reply_contestant(error="اسم المتسابق والفريق مطلوبان")
            return

        buzz_lock_key = f"buzz_lock_{self.session_id}"
        current_buzzer = cache.get(buzz_lock_key)
        
        if current_buzzer:
            # الزر محجوز
            await self._reply_contestant(rejected=f'الزر محجوز من {current_buzzer.get("name", "مشارك")}')
            return

        # حجز فوري لمدة 3 ثواني
        cache.set(buzz_lock_key, {
            'name': contestant_name,
            'team': team,
            'timestamp': timestamp,
            'session_id': self.session_id
        }, timeout=3)

        # حفظ المتسابق في قاعدة البيانات
        await self.ensure_contestant(self.session, contestant_name, team)

        # رد فوري للمتسابق
        await self._reply_contestant(confirmed=True, name=contestant_name, team=team)

        # بث فوري لجميع الصفحات
        team_display = await self.get_team_display_name(self.session, team)
        
        # بث واحد يصل للجميع
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'contestant_name': contestant_name,
            'team': team,
            'team_display': team_display,
            'timestamp': timestamp,
            'action': 'buzz_accepted'
        })

        # تشغيل مؤقت فك القفل تلقائياً
        asyncio.create_task(self._auto_unlock_after_3_seconds())

        logger.info(f"INSTANT Buzz: {contestant_name} from {team} in session {self.session_id}")

    async def handle_buzz_reset(self):
        """إعادة تعيين فورية من المقدم"""
        try:
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)

            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event',
                'action': 'buzz_reset'
            })
            
        except Exception as e:
            logger.error(f"Error resetting buzzer: {e}")

    async def handle_update_cell_state(self, data):
        """تحديث فوري للخلايا"""
        letter = (data.get('letter') or '').strip()
        state = data.get('state')
        if not letter or state not in ('normal', 'team1', 'team2'):
            return

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_cell_update',
            'letter': letter,
            'state': state
        })

    async def handle_update_scores(self, data):
        """تحديث فوري للنقاط"""
        try:
            team1_score = int(data.get('team1_score', 0))
            team2_score = int(data.get('team2_score', 0))
        except (TypeError, ValueError):
            return

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_score_update',
            'team1_score': max(0, team1_score),
            'team2_score': max(0, team2_score)
        })

    # -------------------------
    # Broadcasters موحدة
    # -------------------------
    async def broadcast_buzz_event(self, event):
        """
        موحد لجميع أحداث الـ buzz
        يرسل للجميع ويترك كل صفحة تقرر كيف تتعامل معه
        """
        action = event.get('action')
        
        if action == 'buzz_accepted':
            # للمتسابقين: لا شيء (يستلمون رد مباشر)
            if self.role == 'contestant':
                return
                
            # للمقدم وشاشة العرض
            await self.send(text_data=json.dumps({
                'type': 'contestant_buzz_accepted',
                'contestant_name': event.get('contestant_name'),
                'team': event.get('team'),
                'team_display': event.get('team_display'),
                'timestamp': event.get('timestamp'),
                'start_countdown': True  # إشارة لبدء العد التنازلي
            }))
            
        elif action == 'buzz_unlock':
            if self.role == 'contestant':
                return
            await self.send(text_data=json.dumps({
                'type': 'buzz_unlocked',
                'message': 'انتهى الوقت - الزر متاح الآن'
            }))
            
        elif action == 'buzz_reset':
            if self.role == 'contestant':
                return
            await self.send(text_data=json.dumps({
                'type': 'buzz_reset_by_host'
            }))

    async def broadcast_cell_update(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'cell_state_updated',
            'letter': event['letter'],
            'state': event['state']
        }))

    async def broadcast_score_update(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event['team1_score'],
            'team2_score': event['team2_score']
        }))

    # -------------------------
    # Helpers
    # -------------------------
    async def get_session(self):
        return await sync_to_async(
            lambda: GameSession.objects.select_related('package').get(id=self.session_id)
        )()

    async def ensure_contestant(self, session, contestant_name, team):
        try:
            obj = await sync_to_async(
                lambda: Contestant.objects.filter(session=session, name=contestant_name).first()
            )()
            if obj:
                if obj.team != team:
                    obj.team = team
                    await sync_to_async(obj.save)(update_fields=['team'])
            else:
                await sync_to_async(Contestant.objects.create)(
                    session=session, name=contestant_name, team=team
                )
        except Exception as e:
            logger.error(f"Error ensuring contestant: {e}")

    async def get_team_display_name(self, session, team):
        if team == 'team1':
            return session.team1_name
        if team == 'team2':
            return session.team2_name
        return 'فريق غير معروف'

    async def _auto_unlock_after_3_seconds(self):
        """فك القفل تلقائياً بعد 3 ثواني"""
        try:
            await asyncio.sleep(3)
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)
            
            # بث فك القفل
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event',
                'action': 'buzz_unlock'
            })
            
        except Exception as e:
            logger.error(f"Auto unlock error for session {self.session_id}: {e}")

    async def _is_session_expired(self, session: GameSession) -> bool:
        if session.package and session.package.is_free:
            expiry_time = session.created_at + timedelta(hours=1)
        else:
            expiry_time = session.created_at + timedelta(hours=72)
        return timezone.now() >= expiry_time

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}

    @staticmethod
    def _host_token_key(session_id: str) -> str:
        return f"host_token_{session_id}"

    async def _reply_contestant(self, confirmed: bool = False, name: str = "", team: str = "", rejected: str = "", error: str = ""):
        """رد مباشر فوري للمتسابق"""
        if confirmed:
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed',
                'contestant_name': name,
                'team': team,
                'message': f'تم تسجيل إجابتك يا {name}!'
            }))
            return
        if rejected:
            await self.send(text_data=json.dumps({
                'type': 'buzz_rejected',
                'message': rejected
            }))
            return
        if error:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': error
            }))