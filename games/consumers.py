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

from games.models import GameSession, Contestant

logger = logging.getLogger('games')


class LettersGameConsumer(AsyncWebsocketConsumer):
    """
    يربط ثلاث واجهات لنفس الجلسة:
    - role=host       : صفحة المقدّم (تحكّم بالحروف/الخلايا/النقاط + استلام اسم أول متسابق ضغط)
    - role=display    : شاشة العرض (قراءة فقط + تستقبل إسم أول من ضغط)
    - role=contestant : المتسابق (زر واحد يرسل محاولة الضغط، يتلقى قبول/رفض *مباشر فقط*)
    """

    BUZZ_RATE_MAX = 5      # أقصى 5 محاولات
    BUZZ_RATE_WINDOW = 10  # خلال 10 ثوانٍ

    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        # استخرج الدور من الـ QueryString
        qs = self._parse_qs()
        self.role = qs.get('role', ['viewer'])[0]  # host | contestant | display | viewer

        # التحقق من الجلسة
        try:
            self.session = await self.get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)  # Not found
            return

        # منع التشغيل على جلسة منتهية/غير نشطة
        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)  # expired/inactive
            return

        # صلاحيات المقدم (لو تبغى تشدد أكثر: أضف host_token لاحقًا)
        if self.role == 'host':
            user = self.scope.get('user')
            if not user or not user.is_authenticated or user.id != self.session.host_id:
                await self.close(code=4403)  # Forbidden
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
            return  # تجاهل البيانات التالفة

        message_type = data.get('type')
        logger.debug(f"WS message: {message_type} | role={self.role} | session={self.session_id}")

        try:
            # keep-alive
            if message_type == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
                return

            # المتسابق: إرسال فقط
            if message_type == "contestant_buzz" and self.role == "contestant":
                await self.handle_contestant_buzz(data)
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

            # display / viewer لا يرسلون شيء
            # تجاهل أي نوع غير معروف أو غير مسموح
        except Exception as e:
            logger.error(f"WS handler error for {message_type}: {e}")
            # لا ترسل تفاصيل الخطأ للمستخدم

    # -------------------------
    # Handlers
    # -------------------------
    async def handle_contestant_buzz(self, data):
        """
        أول متسابق يضغط يحجز الزر 3 ثوانٍ فقط، يظهر اسمه للمقدّم والعرض،
        ثم يفك القفل تلقائيًا من السيرفر.
        """
        contestant_name = (data.get("contestant_name") or "").strip()
        team = data.get("team")
        timestamp = data.get("timestamp")

        if not contestant_name or team not in ("team1", "team2"):
            await self._reply_contestant(error="اسم المتسابق والفريق مطلوبان")
            return

        # Rate limit لكل (IP + اسم) على نفس الجلسة
        if not await self._allow_buzz_rate(contestant_name):
            await self._reply_contestant(rejected="محاولات كثيرة جدًا، حاول بعد قليل")
            return

        buzz_lock_key = f"buzz_lock_{self.session_id}"
        current_buzzer = cache.get(buzz_lock_key)
        if current_buzzer:
            await self._reply_contestant(rejected=f'الزر محجوز من {current_buzzer.get("name", "مشارك")}')
            return

        # حجز لمدة 3 ثوانٍ (قفل مركزي في الكاش)
        cache.set(
            buzz_lock_key,
            {'name': contestant_name, 'team': team, 'timestamp': timestamp},
            timeout=3  # <-- أهم تغيير: القفل 3 ثوانٍ فقط
        )

        # سجّل المتسابق إن لم يكن موجود
        await self.register_contestant_if_needed(self.session, contestant_name, team)

        # تأكيد مباشر لصاحب الضغطة فقط
        await self._reply_contestant(confirmed=True, name=contestant_name, team=team)

        # بث قفل الزر واسم المقبول (إلى المقدم + شاشة العرض فقط)
        await self._group_send_hosts_displays(
            {
                'type': 'broadcast_buzz_lock',
                'message': f'{contestant_name} حجز الزر',
                'locked_by': contestant_name,
                'team': team
            }
        )

        team_display = await self.get_team_display_name(self.session, team)
        await self._group_send_hosts_displays(
            {
                'type': 'broadcast_contestant_buzz',
                'contestant_name': contestant_name,
                'team': team,
                'team_display': team_display,
                'timestamp': timestamp
            }
        )

        # فتح القفل تلقائيًا بعد 3 ثوانٍ + حذف المفتاح من الكاش
        asyncio.create_task(self._auto_unlock_visual())

        logger.info(f"Buzz accepted: {contestant_name} from {team} in session {self.session_id}")

    async def handle_buzz_reset(self):
        """إعادة تعيين القفل يدويًا من المقدم (يفتح فورًا للجميع)."""
        try:
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)

            # بثّ reset + unlock للمقدم + شاشة العرض فقط
            await self._group_send_hosts_displays({'type': 'broadcast_buzz_reset'})
            await self._group_send_hosts_displays(
                {'type': 'broadcast_buzz_unlock', 'message': 'تم فك قفل الزر'}
            )
            logger.info(f"Buzzer reset for session: {self.session_id}")
        except Exception as e:
            logger.error(f"Error resetting buzzer: {e}")

    async def handle_update_cell_state(self, data):
        """المقدم يغيّر لون خلية → نبث اللون (للمقدم + العرض فقط)."""
        letter = (data.get('letter') or '').strip()
        state = data.get('state')
        if not letter or state not in ('normal', 'team1', 'team2'):
            return

        await self._group_send_hosts_displays(
            {'type': 'broadcast_cell_update', 'letter': letter, 'state': state}
        )

    async def handle_update_scores(self, data):
        """المقدم يحدّث نقاط الفريقين → نبث (للمقدم + العرض فقط)."""
        try:
            team1_score = int(data.get('team1_score', 0))
            team2_score = int(data.get('team2_score', 0))
        except (TypeError, ValueError):
            return

        payload = {
            'type': 'broadcast_score_update',
            'team1_score': max(0, team1_score),
            'team2_score': max(0, team2_score)
        }
        await self._group_send_hosts_displays(payload)

    # -------------------------
    # Broadcasters (to clients)
    # -------------------------
    async def broadcast_contestant_buzz(self, event):
        # لا ترسل للمتسابقين (يستلمون رد خاص فقط)
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'show_contestant_buzz',
            'contestant_name': event['contestant_name'],
            'team': event['team'],
            'team_display': event.get('team_display', ''),
            'timestamp': event.get('timestamp')
        }))

    async def broadcast_buzz_lock(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'buzz_lock',
            'message': event.get('message', ''),
            'locked_by': event.get('locked_by', ''),
            'team': event.get('team', '')
        }))

    async def broadcast_buzz_unlock(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'buzz_unlock',
            'message': event.get('message', 'الزر متاح للضغط')
        }))

    async def broadcast_buzz_reset(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({'type': 'buzz_reset'}))

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

    async def register_contestant_if_needed(self, session, contestant_name, team):
        try:
            exists = await sync_to_async(
                Contestant.objects.filter(session=session, name=contestant_name).exists
            )()
            if not exists:
                await sync_to_async(Contestant.objects.create)(
                    session=session, name=contestant_name, team=team
                )
                logger.info(f"New contestant registered: {contestant_name} in team {team}")
        except Exception as e:
            logger.error(f"Error registering contestant: {e}")

    async def get_team_display_name(self, session, team):
        if team == 'team1':
            return session.team1_name
        if team == 'team2':
            return session.team2_name
        return 'فريق غير معروف'

    async def _auto_unlock_visual(self):
        """يفتح القفل بعد 3 ثوانٍ تلقائيًا ويبث الحدث، ويمسح مفتاح القفل من الكاش."""
        try:
            await asyncio.sleep(3)
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)
            await self._group_send_hosts_displays(
                {'type': 'broadcast_buzz_unlock', 'message': 'انتهى الوقت'}
            )
        except Exception as e:
            logger.error(f"auto_unlock_visual error for session {self.session_id}: {e}")

    async def _is_session_expired(self, session: GameSession) -> bool:
        """
        فحص انتهاء الصلاحية دون تعديل DB (lazy check فقط)
        - المجانية: 1 ساعة
        - المدفوعة: 72 ساعة
        """
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

    def _client_ip(self) -> str:
        try:
            client = self.scope.get('client') or ('0.0.0.0', 0)
            return client[0] or '0.0.0.0'
        except Exception:
            return '0.0.0.0'

    async def _allow_buzz_rate(self, name: str) -> bool:
        """
        Rate limit بسيط: أقصى BUZZ_RATE_MAX محاولات خلال BUZZ_RATE_WINDOW ثوانٍ
        لكل (session_id + ip + name)
        """
        ip = self._client_ip()
        key = f"ws_rate:{self.session_id}:{ip}:{name}"
        try:
            current = cache.get(key, 0)
            if current >= self.BUZZ_RATE_MAX:
                return False
            if current == 0:
                cache.set(key, 1, timeout=self.BUZZ_RATE_WINDOW)
            else:
                cache.incr(key)
            return True
        except Exception:
            return True  # ما نحبس المستخدمين لو الكاش خرب

    async def _reply_contestant(self, confirmed: bool = False, name: str = "", team: str = "", rejected: str = "", error: str = ""):
        """رد مباشر إلى المتسابق الحالي فقط، بدون بث جماعي."""
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

    async def _group_send_hosts_displays(self, payload: dict):
        """إرسال إلى المجموعة، وسيتم فلترته عند الاستقبال بحيث لا يظهر للمتسابقين."""
        await self.channel_layer.group_send(self.group_name, payload)
