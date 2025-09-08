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
    - قفل 3 ثوانٍ تلقائي (تقدر ترفعه لـ 4 ثوانٍ لو تبي توحّد مع HTTP)
    - أوامر المقدم تُحفَظ في DB وتُبث عبر المجموعة
    - بث إبراز الحرف المختار (letter_selected)
    - توافق مع views.update_scores عبر alias broadcast_scores
    """

    # ============ Group broadcasts (called by views/group_send) ============
    async def broadcast_letters_replace(self, event):
        # لا نرسل للمتسابقين
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'letters_updated',
            'letters': event.get('letters', []),
            'reset_progress': bool(event.get('reset_progress', False)),
        }))

    async def broadcast_buzz_event(self, event):
        action = event.get('action')
        if action == 'buzz_accepted':
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
            await self.send(text_data=json.dumps({'type': 'buzz_reset_by_host'}))

    async def broadcast_cell_state(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'cell_state_updated',
            'letter': event.get('letter'),
            'state': event.get('state')
        }))

    async def broadcast_cell_update(self, event):
        # توافق قديم
        await self.broadcast_cell_state(event)

    async def broadcast_score_update(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event.get('team1_score'),
            'team2_score': event.get('team2_score')
        }))

    async def broadcast_scores(self, event):
        """Alias لاستقبال ما ترسله views.update_scores(type='broadcast_scores')."""
        await self.broadcast_score_update(event)

    async def broadcast_letter_selected(self, event):
        """يُستخدم عند البث من views أو من نفس هذا الـConsumer"""
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            "type": "letter_selected",
            "letter": event.get("letter")
        }))

    # ============================== Lifecycle ==============================
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        qs = self._parse_qs()
        self.role = qs.get('role', ['viewer'])[0]

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
        # إغلاق أنيق لو انتهت الصلاحية
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
            if message_type == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
                return

            # المتسابق: البازر الفوري
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
                if message_type == "letter_selected":
                    # 👈 جديد: استقبل اختيار الحرف من المقدم ثم ابثّه للجميع (ما عدا المتسابقين)
                    letter = (data.get('letter') or '').strip()
                    if letter:
                        await self.channel_layer.group_send(self.group_name, {
                            "type": "broadcast_letter_selected",
                            "letter": letter
                        })
                    return

        except Exception as e:
            logger.error(f"WS handler error for {message_type}: {e}")

    # ============================= Handlers ================================
    async def handle_contestant_buzz_instant(self, data):
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
            added = await sync_to_async(cache.add)(buzz_lock_key, lock_payload, timeout=3)  # يمكن ترفعها 4
        except Exception:
            added = False

        if not added:
            current_buzzer = await sync_to_async(cache.get)(buzz_lock_key) or {}
            await self._reply_contestant(rejected=f'الزر محجوز من {current_buzzer.get("name", "مشارك")}')
            return

        await self.ensure_contestant(self.session, contestant_name, team)

        await self._reply_contestant(confirmed=True, name=contestant_name, team=team)

        team_display = await self.get_team_display_name(self.session, team)
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'contestant_name': contestant_name,
            'team': team,
            'team_display': team_display,
            'timestamp': timestamp,
            'action': 'buzz_accepted'
        })

        asyncio.create_task(self._auto_unlock_after_3_seconds())  # يمكن تغييره لـ 4

        logger.info(f"INSTANT Buzz (atomic): {contestant_name} from {team} in session {self.session_id}")

    async def handle_buzz_reset(self):
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
        letter = (data.get('letter') or '').strip()
        state = (data.get('state') or '').strip()

        if not letter or state not in ('normal', 'team1', 'team2'):
            return

        try:
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

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_cell_state',
            'letter': letter,
            'state': state
        })

    async def handle_update_scores(self, data):
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
        try:
            await asyncio.sleep(3)  # يمكن جعلها 4 لمطابقة HTTP
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







# games/consumers.py — استبدال كامل لـ PicturesGameConsumer

import json
import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

from games.models import GameSession, Contestant, PictureRiddle, PictureGameProgress

logger = logging.getLogger('games')


class PicturesGameConsumer(AsyncWebsocketConsumer):
    """
    WebSocket لتحدّي الصور:
    - المتسابق: buzz فوري بقفل 3 ثواني.
    - المقدم: تنقّل/تعيين/تحديث نقاط/Reset للبازر.
    - العرض: يستقبل الصورة الحالية + إشعارات البازر + النقاط.
    - نقبل الاتصال أولاً ثم نجلب البيانات لتفادي فشل الـhandshake.
    """

    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"images_session_{self.session_id}"

        # استخرج الدور
        self.role = self._parse_qs().get('role', ['viewer'])[0]

        # جرّب الحصول على الجلسة
        try:
            self.session = await self._get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        # لو منتهية لا نقبل
        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return

        # *** اقبل الاتصال أولًا ثم أكمل التهيئة ***
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WS connected (images): session={self.session_id}, role={self.role}")

        # حمّل قائمة الألغاز بشكل آمن
        self.riddles = []
        try:
            self.riddles = await sync_to_async(lambda: list(
                PictureRiddle.objects.filter(package=self.session.package)
                .order_by('order')
                .values('order', 'image_url', 'hint', 'answer')
            ))()
        except Exception as e:
            logger.error(f'Pics: failed to load riddles for {self.session_id}: {e}')

        # تأكد من progress
        self.current_index, self.total = 1, max(1, len(self.riddles) or 1)
        try:
            self.current_index, self.total = await self._ensure_progress_bounds()
        except Exception as e:
            logger.error(f'Pics: ensure progress failed for {self.session_id}: {e}')

        # بثّ الحالة الأولية للمقدم/العرض
        if self.role in ('host', 'display'):
            await self._send_puzzle_state()

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

    async def receive(self, text_data: str):
        # إنهاء أنيق لو انتهت الجلسة
        if await self._is_session_expired(self.session) or not self.session.is_active:
            try:
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'انتهت صلاحية الجلسة'}))
            finally:
                await self.close(code=4401)
            return

        try:
            data = json.loads(text_data or '{}')
        except json.JSONDecodeError:
            return

        t = data.get('type')

        # keep-alive
        if t == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        # المتسابق
        if t == 'contestant_buzz' and self.role == 'contestant':
            await self._handle_buzz(data)
            return

        # المقدم
        if self.role == 'host':
            if t == 'puzzle_nav':
                await self._handle_nav(data.get('dir'))
                return
            if t == 'puzzle_set_index':
                await self._handle_set_index(data.get('index'))
                return
            if t == 'update_scores':
                try:
                    t1 = int(data.get('team1_score', 0))
                    t2 = int(data.get('team2_score', 0))
                except (TypeError, ValueError):
                    return
                await self._handle_update_scores(t1, t2)
                return
            if t == 'buzz_reset':
                await self._handle_buzz_reset()
                return

    # --------------------- handlers: puzzle ---------------------
    async def _handle_nav(self, dir_):
        if dir_ not in ('next', 'prev'):
            return
        total = max(1, len(self.riddles) or 1)

        def _upd():
            prog = PictureGameProgress.objects.select_for_update().get(session=self.session)
            if dir_ == 'next':
                prog.current_index = min(prog.current_index + 1, total)
            else:
                prog.current_index = max(prog.current_index - 1, 1)
            prog.save(update_fields=['current_index'])
            return prog.current_index

        self.current_index = await sync_to_async(_upd)()
        await self._broadcast_puzzle_state()

    async def _handle_set_index(self, index):
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return
        total = max(1, len(self.riddles) or 1)

        def _upd():
            prog = PictureGameProgress.objects.select_for_update().get(session=self.session)
            prog.current_index = max(1, min(idx, total))
            prog.save(update_fields=['current_index'])
            return prog.current_index

        self.current_index = await sync_to_async(_upd)()
        await self._broadcast_puzzle_state()

    # --------------------- handlers: scores ---------------------
    async def _handle_update_scores(self, t1, t2):
        try:
            def _upd():
                from django.db import transaction
                with transaction.atomic():
                    s = GameSession.objects.select_for_update().get(id=self.session_id)
                    s.team1_score = max(0, int(t1))
                    s.team2_score = max(0, int(t2))
                    winning_score = 10
                    if s.team1_score >= winning_score and s.team1_score > s.team2_score:
                        s.winner_team = 'team1'; s.is_completed = True
                    elif s.team2_score >= winning_score and s.team2_score > s.team1_score:
                        s.winner_team = 'team2'; s.is_completed = True
                    s.save(update_fields=['team1_score', 'team2_score', 'winner_team', 'is_completed'])
                    return s.team1_score, s.team2_score
            t1f, t2f = await sync_to_async(_upd)()
        except Exception as e:
            logger.error(f'Pics scores DB error: {e}')
            return

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_score_update',
            'team1_score': t1f, 'team2_score': t2f
        })

    # --------------------- handlers: buzzer ---------------------
    async def _handle_buzz(self, data):
        name = (data.get('contestant_name') or '').strip()
        team = data.get('team')
        timestamp = data.get('timestamp')

        if not name or team not in ('team1', 'team2'):
            await self._reply_contestant(error='اسم المتسابق والفريق مطلوبان')
            return

        key = f'buzz_lock_{self.session_id}'
        payload = {'name': name, 'team': team, 'timestamp': timestamp, 'session_id': self.session_id, 'method': 'WS'}
        try:
            added = await sync_to_async(cache.add)(key, payload, timeout=3)
        except Exception:
            added = False

        if not added:
            cur = await sync_to_async(cache.get)(key) or {}
            await self._reply_contestant(rejected=f'الزر محجوز من {cur.get("name","مشارك")}')
            return

        await self._ensure_contestant(name, team)
        await self._reply_contestant(confirmed=True, name=name, team=team)

        team_display = self.session.team1_name if team == 'team1' else self.session.team2_name
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'contestant_name': name, 'team': team,
            'team_display': team_display, 'timestamp': timestamp,
            'action': 'buzz_accepted'
        })

        asyncio.create_task(self._auto_unlock_3s())

    async def _handle_buzz_reset(self):
        try:
            await sync_to_async(cache.delete)(f'buzz_lock_{self.session_id}')
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event', 'action': 'buzz_reset'
            })
        except Exception as e:
            logger.error(f'Pics buzz reset error: {e}')

    # --------------------- group handlers ---------------------
    async def broadcast_buzz_event(self, event):
        action = event.get('action')
        if self.role == 'contestant':
            return
        if action == 'buzz_accepted':
            await self.send(text_data=json.dumps({
                'type': 'contestant_buzz_accepted',
                'contestant_name': event.get('contestant_name'),
                'team': event.get('team'),
                'team_display': event.get('team_display'),
                'timestamp': event.get('timestamp'),
                'start_countdown': True
            }))
        elif action == 'buzz_unlock':
            await self.send(text_data=json.dumps({'type': 'buzz_unlocked'}))
        elif action == 'buzz_reset':
            await self.send(text_data=json.dumps({'type': 'buzz_reset_by_host'}))

    async def broadcast_score_update(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event.get('team1_score'),
            'team2_score': event.get('team2_score'),
        }))

    async def broadcast_scores(self, event):
        await self.broadcast_score_update(event)

    async def broadcast_puzzle_state(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'puzzle_updated',
            'index': event.get('index'),
            'total': event.get('total'),
            'image_url': event.get('image_url'),
            'hint': event.get('hint'),
            'answer': event.get('answer'),
        }))

    async def broadcast_image_index(self, event):
        if self.role == 'contestant':
            return
        try:
            idx = int(event.get('current_index') or 1)
        except (TypeError, ValueError):
            idx = 1

        total = max(1, len(self.riddles) or int(event.get('count') or 1))
        idx = max(1, min(idx, total))
        r = self.riddles[idx - 1] if 1 <= idx <= len(self.riddles) else {'image_url': '', 'hint': '', 'answer': ''}

        await self.send(text_data=json.dumps({
            'type': 'puzzle_updated',
            'index': idx,
            'total': len(self.riddles) or total,
            'image_url': r.get('image_url') or '',
            'hint': r.get('hint') or '',
            'answer': r.get('answer') or '',
        }))

    # --------------------- helpers ---------------------
    async def _send_puzzle_state(self):
        idx = await self._get_current_index()
        payload = self._state_payload(idx)
        await self.send(text_data=json.dumps({'type': 'puzzle_updated', **payload}))

    async def _broadcast_puzzle_state(self):
        idx = await self._get_current_index()
        payload = self._state_payload(idx)
        await self.channel_layer.group_send(self.group_name, {'type': 'broadcast_puzzle_state', **payload})

    def _state_payload(self, idx: int):
        if 1 <= idx <= len(self.riddles):
            r = self.riddles[idx - 1]
        else:
            r = {'image_url': '', 'hint': '', 'answer': ''}
        return {
            'index': max(1, idx),
            'total': max(1, len(self.riddles) or 1),
            'image_url': r.get('image_url') or '',
            'hint': (r.get('hint') or ''),
            'answer': (r.get('answer') or ''),
        }

    async def _get_current_index(self) -> int:
        def _read():
            return PictureGameProgress.objects.filter(session=self.session)\
                   .values_list('current_index', flat=True).first() or 1
        return await sync_to_async(_read)()

    async def _ensure_progress_bounds(self):
        def _ensure():
            obj, _ = PictureGameProgress.objects.get_or_create(
                session=self.session, defaults={'current_index': 1}
            )
            total = max(1, len(self.riddles) or 1)
            if obj.current_index < 1 or obj.current_index > total:
                obj.current_index = 1
                obj.save(update_fields=['current_index'])
            return obj.current_index, total
        return await sync_to_async(_ensure)()

    async def _get_session(self):
        return await sync_to_async(
            lambda: GameSession.objects.select_related('package').get(id=self.session_id)
        )()

    async def _ensure_contestant(self, name, team):
        def _ensure():
            obj = Contestant.objects.filter(session=self.session, name=name).first()
            if obj:
                if obj.team != team:
                    obj.team = team
                    obj.save(update_fields=['team'])
            else:
                Contestant.objects.create(session=self.session, name=name, team=team)
        await sync_to_async(_ensure)()

    async def _auto_unlock_3s(self):
        try:
            await asyncio.sleep(3)
            await sync_to_async(cache.delete)(f'buzz_lock_{self.session_id}')
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event', 'action': 'buzz_unlock'
            })
        except Exception as e:
            logger.error(f'auto unlock error: {e}')

    async def _is_session_expired(self, session: GameSession) -> bool:
        expiry = session.created_at + (timedelta(hours=1) if session.package and session.package.is_free else timedelta(hours=72))
        return timezone.now() >= expiry

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}

    async def _reply_contestant(self, confirmed=False, name="", team="", rejected="", error=""):
        if confirmed:
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed', 'contestant_name': name, 'team': team,
                'message': f'تم تسجيل إجابتك يا {name}!'
            }))
            return
        if rejected:
            await self.send(text_data=json.dumps({'type': 'buzz_rejected', 'message': rejected}))
            return
        if error:
            await self.send(text_data=json.dumps({'type': 'error', 'message': error}))
