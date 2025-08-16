# games/consumers.py

import json
import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

from games.models import GameSession, Contestant, LettersGameProgress

logger = logging.getLogger('games')


class LettersGameConsumer(AsyncWebsocketConsumer):
    """
    Consumer محسّن مع ربط فوري بين الصفحات:
    - المتسابق يضغط → فوري لشاشة العرض + المقدم
    - قفل 3 ثوانٍ تلقائي
    - أوامر المقدم تُحفَظ في DB وتُبث عبر المجموعة
    - لا حاجة لأي توكن؛ الرابط يكفي (حسب طلبك)
    """

    # ============ Group broadcasts (called by views/group_send) ============
    async def broadcast_letters_replace(self, event):
        """
        استقبال بث تغيير ترتيب الحروف من الـAPI.
        يُرسل للمقدم وشاشة العرض (وليس المتسابقين).
        payload inbound: {letters: [...], reset_progress: bool}
        """
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'letters_updated',
            'letters': event.get('letters', []),
            'reset_progress': bool(event.get('reset_progress', False)),
        }))

    async def broadcast_buzz_event(self, event):
        """
        موحّد لجميع أحداث الـ buzz القادمة من HTTP/WS.
        actions: buzz_accepted, buzz_unlock, buzz_reset
        """
        action = event.get('action')
        if action == 'buzz_accepted':
            # للمتسابقين: رد مباشر يُرسل لهم من handler؛ ما نكرر هنا
            if self.role == 'contestant':
                return
            await self.send(text_data=json.dumps({
                'type': 'contestant_buzz_accepted',
                'contestant_name': event.get('contestant_name'),
                'team': event.get('team'),
                'team_display': event.get('team_display'),
                'timestamp': event.get('timestamp'),
                'start_countdown': True
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

    async def broadcast_cell_state(self, event):
        """
        اسم متوافق مع ما يرسله الـviews عبر group_send(type='broadcast_cell_state')
        payload inbound: {letter, state}
        """
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'cell_state_updated',
            'letter': event.get('letter'),
            'state': event.get('state')
        }))

    # إبقاء توافق قديم لو تم النداء بـ broadcast_cell_update بالخطأ
    async def broadcast_cell_update(self, event):
        await self.broadcast_cell_state(event)

    async def broadcast_score_update(self, event):
        """
        بث تحديث النقاط.
        payload inbound: {team1_score, team2_score}
        """
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event.get('team1_score'),
            'team2_score': event.get('team2_score')
        }))

    # ============================== Lifecycle ==============================
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        # استخرج الدور من QueryString
        qs = self._parse_qs()
        self.role = qs.get('role', ['viewer'])[0]

        # التحقق من الجلسة موجودة وفعالة وغير منتهية
        try:
            self.session = await self.get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WS connected: session={self.session_id}, role={self.role}")

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass
        logger.info(f"WS disconnected: session={self.session_id}, role={self.role}, code={close_code}")

    # ============================== Receive ================================
    async def receive(self, text_data: str):
        # إنهاء أنيق لو الجلسة انتهت
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

            # المتسابق: إرسال buzz فوري بقفل ذرّي
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

    # ============================= Handlers ================================
    async def handle_contestant_buzz_instant(self, data):
        """
        معالجة فورية للـ buzz بقفل ذرّي عبر cache.add(timeout=3)
        """
        contestant_name = (data.get("contestant_name") or "").strip()
        team = data.get("team")
        timestamp = data.get("timestamp")

        if not contestant_name or team not in ("team1", "team2"):
            await self._reply_contestant(error="اسم المتسابق والفريق مطلوبان")
            return

        buzz_lock_key = f"buzz_lock_{self.session_id}"
        lock_payload = {
            'name': contestant_name,
            'team': team,
            'timestamp': timestamp,
            'session_id': self.session_id,
            'method': 'WS',
        }

        try:
            added = await sync_to_async(cache.add)(buzz_lock_key, lock_payload, timeout=3)
        except Exception:
            added = False

        if not added:
            current_buzzer = await sync_to_async(cache.get)(buzz_lock_key) or {}
            await self._reply_contestant(rejected=f'الزر محجوز من {current_buzzer.get("name", "مشارك")}')
            return

        # سجل/حدّث المتسابق
        await self.ensure_contestant(self.session, contestant_name, team)

        # رد فوري للمتسابق
        await self._reply_contestant(confirmed=True, name=contestant_name, team=team)

        # بث فوري للجمهور/المقدم
        team_display = await self.get_team_display_name(self.session, team)
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'contestant_name': contestant_name,
            'team': team,
            'team_display': team_display,
            'timestamp': timestamp,
            'action': 'buzz_accepted'
        })

        # فك القفل بعد 3 ثواني
        asyncio.create_task(self._auto_unlock_after_3_seconds())

        logger.info(f"INSTANT Buzz (atomic): {contestant_name} from {team} in session {self.session_id}")

    async def handle_buzz_reset(self):
        """إعادة تعيين فورية من المقدم"""
        try:
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            await sync_to_async(cache.delete)(buzz_lock_key)
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event',
                'action': 'buzz_reset'
            })
        except Exception as e:
            logger.error(f"Error resetting buzzer: {e}")

    async def handle_update_cell_state(self, data):
        """
        تحديث حالة الخلية (team1/team2/normal):
        - حفظ في LettersGameProgress (cell_states + used_letters)
        - بث التغيير للجمهور/العرض
        """
        letter = (data.get('letter') or '').strip()
        state = (data.get('state') or '').strip()

        if not letter or state not in ('normal', 'team1', 'team2'):
            return

        try:
            # احصل/أنشئ progress
            def _update_progress():
                progress, _ = LettersGameProgress.objects.get_or_create(
                    session=self.session,
                    defaults={'cell_states': {}, 'used_letters': []}
                )
                if not isinstance(progress.cell_states, dict):
                    progress.cell_states = {}
                progress.cell_states[letter] = state

                if not isinstance(progress.used_letters, list):
                    progress.used_letters = []
                if letter not in progress.used_letters:
                    progress.used_letters.append(letter)

                progress.save(update_fields=['cell_states', 'used_letters'])

            await sync_to_async(_update_progress)()
        except Exception as e:
            logger.error(f"DB update error (cell_state) in session {self.session_id}: {e}")

        # بث فوري
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_cell_state',
            'letter': letter,
            'state': state
        })

    async def handle_update_scores(self, data):
        """
        تحديث نقاط الفريقين:
        - حفظ في الجلسة (مع تحديد الفائز لو وصل أحدهم للحد)
        - بث النتيجة للجميع (ما عدا المتسابقين)
        """
        try:
            team1_score = max(0, int(data.get('team1_score', 0)))
            team2_score = max(0, int(data.get('team2_score', 0)))
        except (TypeError, ValueError):
            return

        try:
            def _update_scores():
                session = GameSession.objects.select_for_update().get(id=self.session_id)
                session.team1_score = team1_score
                session.team2_score = team2_score

                winning_score = 10
                if session.team1_score >= winning_score and session.team1_score > session.team2_score:
                    session.winner_team = 'team1'
                    session.is_completed = True
                elif session.team2_score >= winning_score and session.team2_score > session.team1_score:
                    session.winner_team = 'team2'
                    session.is_completed = True
                session.save(update_fields=['team1_score', 'team2_score', 'winner_team', 'is_completed'])

            await sync_to_async(_update_scores)()
        except Exception as e:
            logger.error(f"DB update error (scores) in session {self.session_id}: {e}")

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_score_update',
            'team1_score': team1_score,
            'team2_score': team2_score
        })

    # ============================== Helpers ================================
    async def get_session(self):
        return await sync_to_async(
            lambda: GameSession.objects.select_related('package').get(id=self.session_id)
        )()

    async def ensure_contestant(self, session, contestant_name, team):
        try:
            def _ensure():
                obj = Contestant.objects.filter(session=session, name=contestant_name).first()
                if obj:
                    if obj.team != team:
                        obj.team = team
                        obj.save(update_fields=['team'])
                else:
                    Contestant.objects.create(session=session, name=contestant_name, team=team)
            await sync_to_async(_ensure)()
        except Exception as e:
            logger.error(f"Error ensuring contestant: {e}")

    async def get_team_display_name(self, session, team):
        if team == 'team1':
            return session.team1_name
        if team == 'team2':
            return session.team2_name
        return 'فريق غير معروف'

    async def _auto_unlock_after_3_seconds(self):
        """فك القفل تلقائياً بعد 3 ثوانٍ + بث unlock"""
        try:
            await asyncio.sleep(3)
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            await sync_to_async(cache.delete)(buzz_lock_key)
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
