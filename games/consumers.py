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

import re

EQUIVALENT_GROUPS = [
    {'أ', 'إ', 'آ', 'ا', 'ء', 'ئ', 'ؤ'},  # كل الهمزات
    {'ة', 'ه'},                               # تاء مربوطة
    {'ي', 'ى'},                               # ياء
]

def _remove_tashkeel(text: str) -> str:
    return re.sub('[\u064b-\u065f\u0670]', '', text).strip()

def _chars_equivalent(c1: str, c2: str) -> bool:
    for group in EQUIVALENT_GROUPS:
        if c1 in group and c2 in group:
            return True
    return c1 == c2

def _answers_equivalent(a: str, b: str) -> bool:
    a = _remove_tashkeel(a)
    b = _remove_tashkeel(b)
    if len(a) != len(b):
        return False
    return all(_chars_equivalent(c1, c2) for c1, c2 in zip(a, b))

def _strip_al(text: str) -> str:
    """يحذف 'ال' التعريف من بداية الكلمة"""
    text = text.strip()
    if text.startswith('ال'):
        return text[2:]
    return text

def _normalize_al(text: str) -> str:
    """يحذف 'ال' من كل كلمة في الجملة"""
    return ' '.join(_strip_al(w) for w in text.split())

def check_answer(user_answer: str, correct_answer: str, accepted_answers: list, answer_type: str, smart_correction: bool = True) -> dict:
    """
    يرجع dict:
    - is_correct: True/False
    - exact_match: True إذا مطابق تماماً
    - corrected: True إذا النظام صحح إملائياً
    """
    user = user_answer.strip()
    all_accepted = [correct_answer.strip()] + [a.strip() for a in (accepted_answers or [])]

    # مطابقة حرفية أولاً
    if any(user == a for a in all_accepted):
        return {'is_correct': True, 'exact_match': True, 'corrected': False}

    # مطابقة مع حذف "ال" التعريف
    user_no_al = _normalize_al(user)
    if any(user_no_al == _normalize_al(a) for a in all_accepted):
        return {'is_correct': True, 'exact_match': False, 'corrected': True}

    if answer_type == 'arabic' and smart_correction:
        if any(_answers_equivalent(user, a) for a in all_accepted):
            return {'is_correct': True, 'exact_match': False, 'corrected': True}

    return {'is_correct': False, 'exact_match': False, 'corrected': False}



logger = logging.getLogger('games')



# قفل asyncio لكل جلسة — يمنع تزامن البازر
_buzz_session_locks: dict = {}
_buzz_locks_meta = None  # asyncio.Lock — يتهيأ عند أول استخدام
    
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
        if self.role == 'contestant':
            try:
                from games.models import GameSettings
                show = await sync_to_async(
                    lambda: GameSettings.get_or_create_for_session(self.session).show_grid_to_contestants
                )()
                if show:
                    await self.send(text_data=json.dumps({
                        'type': 'letters_updated',
                        'letters': event.get('letters', []),
                        'reset_progress': bool(event.get('reset_progress', False)),
                    }))
            except Exception:
                pass
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
            try:
                from games.models import GameSettings
                show = await sync_to_async(
                    lambda: GameSettings.get_or_create_for_session(self.session).show_grid_to_contestants
                )()
                if not show:
                    return
            except Exception:
                return
        await self.send(text_data=json.dumps({
            'type': 'cell_state_updated',
            'letter': event.get('letter'),
            'state': event.get('state'),
            'cell_index': event.get('cell_index'),
        }))

    async def broadcast_cell_update(self, event):
        # توافق قديم
        await self.broadcast_cell_state(event)

    async def broadcast_score_update(self, event):
        """
        بثّ النقاط:
        - المقدم/شاشة العرض: دائمًا يستقبلون التحديث
        - المتسابق: يستقبل التحديث فقط إذا كان خيار إظهار الخلية للمتسابقين مفعّل
        """
        if self.role == 'contestant':
            try:
                from games.models import GameSettings
                show = await sync_to_async(
                    lambda: GameSettings.get_or_create_for_session(self.session).show_grid_to_contestants
                )()
                if not show:
                    return
            except Exception:
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
        if self.role == 'contestant':
            try:
                from games.models import GameSettings
                s = await sync_to_async(
                    lambda: GameSettings.get_or_create_for_session(self.session)
                )()
                # نرسل الحرف دائماً للمتسابق لو وضع الآلي مفعّل
                # حتى لو show_grid_to_contestants معطّل
                if s.show_grid_to_contestants or s.auto_host_mode:
                    await self.send(text_data=json.dumps({
                        "type": "letter_selected",
                        "letter": event.get("letter"),
                        "cell_index": event.get("cell_index"),
                    }))
            except Exception:
                pass
            return
        await self.send(text_data=json.dumps({
            "type": "letter_selected",
            "letter": event.get("letter"),
            "cell_index": event.get("cell_index"),
        }))
    # ============================== Lifecycle ==============================
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"
    
        qs = self._parse_qs()
        self.role = qs.get('role', ['viewer'])[0]
    
        # قيمة المؤقت — تُحمّل مرة وحدة وتُحدَّث عند تغيير الإعدادات
        self.buzz_timer = 3
        self._unlock_task = None
    
        try:
            self.session = await self.get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return
    
        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return
    
        # حمّل القيمة الحقيقية من الإعدادات
        try:
            def _load_timer():
                from games.models import GameSettings
                s = GameSettings.get_or_create_for_session(self.session)
                if s.auto_host_mode:
                    return max(1, s.auto_host_timer_seconds or 10)
                return max(1, s.buzz_timer_seconds or 3)
            self.buzz_timer = await sync_to_async(_load_timer)()
        except Exception:
            pass
    
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
    
        if self.role == 'contestant':
            await self._send_grid_to_contestant_if_enabled()
    
        logger.info(f"WS connected: session={self.session_id}, role={self.role}, buzz_timer={self.buzz_timer}")


    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass
        logger.info(f"WS disconnected: session={self.session_id}, role={self.role}, code={close_code}")

    # ============================== Receive ================================
    async def receive(self, text_data: str):
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

            # المتسابق: إجابة المقدم الآلي
            if message_type == "auto_host_answer" and self.role == "contestant":
                await self.handle_auto_host_answer(data)
                return
                

            # المقدم أو شاشة العرض: أوامر وضع بدون مقدم
            if self.role in ("host", "display"):
                if message_type == "nohost_letter_select":
                    letter = (data.get('letter') or '').strip()
                    question_type = (data.get('question_type') or 'main').strip()
                    if letter:
                        try:
                            await sync_to_async(cache.set)(
                                f"current_letter_{self.session_id}", letter, timeout=3600
                            )
                            await sync_to_async(cache.set)(
                                f"current_question_type_{self.session_id}", question_type, timeout=3600
                            )

                            await sync_to_async(cache.delete)(
                                f"partial_question_{self.session_id}"
                            )
                        except Exception:
                            pass
                        await self.channel_layer.group_send(self.group_name, {
                            "type": "broadcast_letter_selected",
                            "letter": letter
                        })
                    return
                
                if message_type == "save_partial_question":
                    partial = (data.get('partial') or '').strip()
                    try:
                        await sync_to_async(cache.set)(
                            f"partial_question_{self.session_id}", partial, timeout=3600
                        )
                    except Exception:
                        pass
                    return
                if message_type == "nohost_question_broadcast":
                    await self.channel_layer.group_send(self.group_name, {
                        "type": "broadcast_nohost_question",
                        "letter": data.get("letter"),
                        "question": data.get("question"),
                    })
                    return
                if message_type == "update_scores":
                    await self.handle_update_scores(data)
                    return

            # المقدم فقط: أوامر التحكم
            if self.role in ("host", "display"):
                if message_type == "update_cell_state":
                    await self.handle_update_cell_state(data)
                    return
                if message_type == "buzz_reset":
                    await self.handle_buzz_reset()
                    return
                if message_type == "letter_selected":
                    letter = (data.get('letter') or '').strip()
                    if letter:
                        try:
                            await sync_to_async(cache.set)(
                                f"current_letter_{self.session_id}", letter, timeout=3600
                            )
                        except Exception:
                            pass
                        await self.channel_layer.group_send(self.group_name, {
                            "type": "broadcast_letter_selected",
                            "letter": letter
                        })
                    return
                if message_type == "penalty_start":
                    await self.channel_layer.group_send(self.group_name, {
                        "type": "broadcast_penalty_start",
                        "team": data.get("team"),
                        "team_name": data.get("team_name"),
                        "seconds": data.get("seconds", 10),
                    })
                    return
                if message_type == "penalty_end":
                    await self.channel_layer.group_send(self.group_name, {
                        "type": "broadcast_penalty_end",
                        "team": data.get("team"),
                    })
                    return
                if message_type == "auto_host_reveal":
                    await self.handle_auto_host_reveal(data)
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

        # قفل asyncio يمنع الـ race condition
        buzz_lock = await self._get_buzz_lock()
        async with buzz_lock:
            # تحديث المؤقت من DB عند كل ضغطة — يضمن التزامن
            try:
                def _refresh_timer():
                    from games.models import GameSettings
                    s = GameSettings.get_or_create_for_session(self.session)
                    if s.auto_host_mode:
                        return max(1, s.auto_host_timer_seconds or 10)
                    return max(1, s.buzz_timer_seconds or 3)
                self.buzz_timer = await sync_to_async(_refresh_timer)()
            except Exception:
                pass

            buzz_lock_key = f"buzz_lock_{self.session_id}"
            lock_payload = {
                'name': contestant_name,
                'team': team,
                'timestamp': timestamp,
                'session_id': self.session_id,
                'method': 'WS',
            }
            lock_ttl = self.buzz_timer + 5  # buffer أكبر

            try:
                added = await sync_to_async(cache.add)(buzz_lock_key, lock_payload, timeout=lock_ttl)
            except Exception:
                added = False

            if not added:
                current_buzzer = await sync_to_async(cache.get)(buzz_lock_key) or {}
                await self._reply_contestant(rejected=f'الزر محجوز من {current_buzzer.get("name", "مشارك")}')
                return

        await self.ensure_contestant(self.session, contestant_name, team)

        # جلب مؤقت المقدم الآلي والحرف الحالي
        auto_host_timer = 10
        current_letter = ''
        try:
            def _get_settings_and_letter():
                from games.models import GameSettings
                s = GameSettings.get_or_create_for_session(self.session)
                letter = cache.get(f"current_letter_{self.session_id}") or ''
                question_type = cache.get(f"current_question_type_{self.session_id}") or 'main'
                partial_question = cache.get(f"partial_question_{self.session_id}") or ''
                return s.auto_host_timer_seconds or 10, letter, question_type, partial_question
            auto_host_timer, current_letter, current_question_type, partial_question = await sync_to_async(_get_settings_and_letter)()
        except Exception:
            pass

        await self._reply_contestant(
            confirmed=True,
            name=contestant_name,
            team=team,
            auto_host_timer=auto_host_timer,
            current_letter=current_letter,
            current_question_type=current_question_type,
            partial_question=partial_question
        )

        team_display = await self.get_team_display_name(self.session, team)
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'contestant_name': contestant_name,
            'team': team,
            'team_display': team_display,
            'timestamp': timestamp,
            'action': 'buzz_accepted'
        })

        if self._unlock_task and not self._unlock_task.done():
            self._unlock_task.cancel()
        self._unlock_task = asyncio.create_task(self._auto_unlock_after_timer())

        logger.info(f"INSTANT Buzz: {contestant_name} from {team} in session {self.session_id}, timer={self.buzz_timer}s")

 


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
        cell_index = data.get('cell_index')  # ← هنا قبل الدالة

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
                key = str(cell_index) if cell_index is not None else letter
                progress.cell_states[key] = state

                if not isinstance(progress.used_letters, list):
                    progress.used_letters = []
                if letter not in progress.used_letters:
                    progress.used_letters.append(letter)

                progress.save(update_fields=['cell_states', 'used_letters'])

            await sync_to_async(_update_progress)()
        except Exception as e:
            logger.error(f"DB update error (cell_state) in session {self.session_id}: {e}")

        cell_index = data.get('cell_index')
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_cell_state',
            'letter': letter,
            'state': state,
            'cell_index': cell_index,
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

    async def _auto_unlock_after_timer(self):
        """فتح الزر بعد انتهاء المؤقت المحدد في الإعدادات."""
        try:
            await asyncio.sleep(self.buzz_timer)
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            await sync_to_async(cache.delete)(buzz_lock_key)
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event',
                'action': 'buzz_unlock'
            })
        except asyncio.CancelledError:
            pass  # تم الإلغاء يدوياً — طبيعي
        except Exception as e:
            logger.error(f"Auto unlock error for session {self.session_id}: {e}")

    async def broadcast_settings_update(self, event):
        settings = event.get('settings', {})
    
        # تحديث المؤقت فورياً بدون قراءة DB
        try:
            if settings.get('auto_host_mode'):
                self.buzz_timer = max(1, int(settings.get('auto_host_timer_seconds') or 10))
            elif 'buzz_timer_seconds' in settings:
                self.buzz_timer = max(1, int(settings['buzz_timer_seconds']))
        except (ValueError, TypeError):
            pass
    
        await self.send(text_data=json.dumps({
            'type': 'settings_updated',
            'settings': settings
        }))
    
        if self.role == 'contestant' and settings.get('show_grid_to_contestants'):
            await self._send_grid_to_contestant_if_enabled()


    async def _is_session_expired(self, session: GameSession) -> bool:
        """
        التحقق من انتهاء صلاحية الجلسة:
        - المجاني: ساعة واحدة
        - المدفوع: لا ينتهي أبداً
        """
        if not session.package:
            return False
        
        # المدفوع: لا ينتهي
        if not session.package.is_free:
            return False
        
        # المجاني: ساعة واحدة
        expiry_time = session.created_at + timedelta(hours=1)
        return timezone.now() >= expiry_time

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}


    async def _reply_contestant(self, confirmed: bool = False, name: str = "", team: str = "", rejected: str = "", error: str = "", auto_host_timer: int = 10, current_letter: str = "", current_question_type: str = "main", partial_question: str = ""):
        if confirmed:
            logger.info(f"_reply_contestant: sending buzz_confirmed to {name}, letter={current_letter}, timer={auto_host_timer}")
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed',
                'contestant_name': name,
                'team': team,
                'message': f'تم تسجيل إجابتك يا {name}!',
                'auto_host_timer': auto_host_timer,
                'current_letter': current_letter,
                'current_question_type': current_question_type,
                'partial_question': partial_question,
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

    async def _send_grid_to_contestant_if_enabled(self):
        try:
            def _get_data():
                from games.models import GameSettings, LettersGameProgress
                from games.utils_letters import get_session_order

                settings = GameSettings.get_or_create_for_session(self.session)
                if not settings.show_grid_to_contestants:
                    return None

                letters = get_session_order(self.session.id, self.session.package.is_free) or []
                progress = LettersGameProgress.objects.filter(session=self.session).first()
                cell_states = progress.cell_states if (progress and isinstance(progress.cell_states, dict)) else {}

                return {
                    'show_grid': True,
                    'grid_size': settings.grid_size,
                    'letters': letters,
                    'cell_states': cell_states,
                    'team1_color': settings.team1_color,
                    'team2_color': settings.team2_color,
                    'team1_name': settings.team1_name or self.session.team1_name,
                    'team2_name': settings.team2_name or self.session.team2_name,
                    'team1_score': self.session.team1_score,
                    'team2_score': self.session.team2_score,
                }

            data = await sync_to_async(_get_data)()
            if data:
                await self.send(text_data=json.dumps({
                    'type': 'grid_state',
                    **data
                }))
        except Exception as e:
            logger.error(f'Error sending grid to contestant: {e}')
    

    async def broadcast_penalty_start(self, event):
        if self.role == 'host':
            return
        await self.send(text_data=json.dumps({
            'type': 'penalty_start',
            'team': event.get('team'),
            'team_name': event.get('team_name'),
            'seconds': event.get('seconds', 10),
        }))

    async def broadcast_penalty_end(self, event):
        if self.role == 'host':
            return
        await self.send(text_data=json.dumps({
            'type': 'penalty_end',
            'team': event.get('team'),
        }))

    
    async def broadcast_nohost_question(self, event):
        """بث السؤال لشاشات العرض الأخرى — لا نرسل للمقدم ولا للمتسابقين"""
        if self.role in ('host', 'contestant'):
            return
        await self.send(text_data=json.dumps({
            'type': 'nohost_question',
            'letter': event.get('letter'),
            'question': event.get('question'),
        }))


    async def handle_auto_host_answer(self, data):
        """
        يستقبل إجابة المتسابق ويصححها
        - يتحقق أن هذا المتسابق هو من ضغط الزر (buzz_lock)
        - يجلب السؤال الحالي ويقارن
        - يبث النتيجة
        """
        contestant_name = (data.get('contestant_name') or '').strip()
        team = data.get('team')
        user_answer = (data.get('answer') or '').strip()
        letter = (data.get('letter') or '').strip()
        question_type = data.get('question_type', 'main')

        if not all([contestant_name, team, user_answer, letter]):
            await self._reply_contestant(error='بيانات ناقصة')
            return

        # تحقق أن هذا المتسابق هو صاحب الـ buzz
        buzz_lock_key = f"buzz_lock_{self.session_id}"
        current_buzzer = await sync_to_async(cache.get)(buzz_lock_key)
        if not current_buzzer or current_buzzer.get('name') != contestant_name:
            # انتهى الوقت — نرسل timeout فقط للمتسابق نفسه بدون إزعاج شاشة العرض
            await self.send(text_data=json.dumps({
                'type': 'auto_host_result',
                'result': 'timeout',
                'contestant_name': contestant_name,
            }))
            return

        # جلب السؤال والتحقق
        try:
            def _get_question_and_settings():
                from games.models import LettersGameQuestion, GameSettings
                q = LettersGameQuestion.objects.filter(
                    package=self.session.package,
                    letter=letter,
                    question_type=question_type
                ).first()
                settings = GameSettings.objects.filter(session=self.session).first()
                return q, settings

            question, settings = await sync_to_async(_get_question_and_settings)()

            if not question:
                await self._reply_contestant(error='السؤال غير موجود')
                return

            # التصحيح
            result = check_answer(
                user_answer=user_answer,
                correct_answer=question.answer,
                accepted_answers=question.accepted_answers or [],
                answer_type=question.answer_type,
                smart_correction=settings.auto_host_smart_correction if settings else True
            )

        except Exception as e:
            logger.error(f'auto_host_answer error: {e}')
            await self._reply_contestant(error='حدث خطأ')
            return

        if result['is_correct']:
            await sync_to_async(cache.delete)(buzz_lock_key)
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_auto_host_result',
                'result': 'correct',
                'corrected': result['corrected'],
                'contestant_name': contestant_name,
                'team': team,
                'user_answer': user_answer,
                'correct_answer': question.answer,
                'letter': letter,
            })
        else:
            await sync_to_async(cache.delete)(buzz_lock_key)
            # ألغِ مؤقت الفتح التلقائي عشان ما يقطع العداد في شاشة العرض
            if self._unlock_task and not self._unlock_task.done():
                self._unlock_task.cancel()
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_auto_host_result',
                'result': 'wrong',
                'corrected': False,
                'contestant_name': contestant_name,
                'team': team,
                'user_answer': user_answer,
                'letter': letter,
            })


    async def handle_auto_host_reveal(self, data):
        """المقدم/النظام يكشف الإجابة يدوياً"""
        letter = (data.get('letter') or '').strip()
        question_type = data.get('question_type', 'main')

        try:
            def _get_answer():
                from games.models import LettersGameQuestion
                q = LettersGameQuestion.objects.filter(
                    package=self.session.package,
                    letter=letter,
                    question_type=question_type
                ).first()
                return q.answer if q else ''

            correct_answer = await sync_to_async(_get_answer)()
        except Exception as e:
            logger.error(f'auto_host_reveal error: {e}')
            return

        buzz_lock_key = f"buzz_lock_{self.session_id}"
        await sync_to_async(cache.delete)(buzz_lock_key)

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_auto_host_result',
            'result': 'revealed',
            'correct_answer': correct_answer,
            'letter': letter,
        })


    async def broadcast_auto_host_result(self, event):
        """يبث نتيجة الإجابة — للمتسابق صاحب الإجابة فقط إذا خطأ، للجميع إذا صح"""
        result = event.get('result')
        contestant_name = event.get('contestant_name', '')

        # لو خطأ — نرسل للمتسابق صاحب الإجابة فقط
        if result == 'wrong' and self.role == 'contestant':
            # نتحقق من اسم المتسابق الحالي عبر الـ buzz المؤقت
            # نرسل للجميع لكن الواجهة تتجاهله لو مو صاحبه
            pass

        await self.send(text_data=json.dumps({
            'type': 'auto_host_result',
            'result': result,
            'contestant_name': contestant_name,
            'team': event.get('team', ''),
            'user_answer': event.get('user_answer', ''),
            'correct_answer': event.get('correct_answer', ''),
            'letter': event.get('letter', ''),
            'corrected': event.get('corrected', False),
        }))

    async def _get_buzz_lock(self) -> asyncio.Lock:
        global _buzz_locks_meta
        if _buzz_locks_meta is None:
            _buzz_locks_meta = asyncio.Lock()
        async with _buzz_locks_meta:
            if self.session_id not in _buzz_session_locks:
                _buzz_session_locks[self.session_id] = asyncio.Lock()
            return _buzz_session_locks[self.session_id]


        







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

        self.role = self._parse_qs().get('role', ['viewer'])[0]

        # مؤقت الزر — يُحمّل مرة وتُحدَّث عند تغيير الإعدادات
        self.buzz_timer = 3
        self._unlock_task = None

        try:
            self.session = await self._get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WS connected (images): session={self.session_id}, role={self.role}")

        # حمّل القيمة الحقيقية من الإعدادات
        try:
            def _load_timer():
                from games.models import GameSettings
                s = GameSettings.get_or_create_for_session(self.session)
                return max(1, s.buzz_timer_seconds or 3)
            self.buzz_timer = await sync_to_async(_load_timer)()
        except Exception:
            pass

        self.riddles = []
        try:
            self.riddles = await sync_to_async(lambda: list(
                PictureRiddle.objects.filter(package=self.session.package)
                .order_by('order')
                .values('order', 'image_url', 'hint', 'answer')
            ))()
        except Exception as e:
            logger.error(f'Pics: failed to load riddles for {self.session_id}: {e}')

        self.current_index, self.total = 1, max(1, len(self.riddles) or 1)
        try:
            self.current_index, self.total = await self._ensure_progress_bounds()
        except Exception as e:
            logger.error(f'Pics: ensure progress failed for {self.session_id}: {e}')

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
            if t == 'update_settings':
                await self._handle_update_settings(data.get('settings', {}))
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
        payload = {'name': name, 'team': team, 'timestamp': timestamp,
                'session_id': self.session_id, 'method': 'WS'}
    
        lock_ttl = self.buzz_timer + 2  # buffer
    
        try:
            added = await sync_to_async(cache.add)(key, payload, timeout=lock_ttl)
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
    
        if self._unlock_task and not self._unlock_task.done():
            self._unlock_task.cancel()
        self._unlock_task = asyncio.create_task(self._auto_unlock_after_timer())


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

    async def _auto_unlock_after_timer(self):
        """فتح الزر بعد انتهاء المؤقت."""
        try:
            await asyncio.sleep(self.buzz_timer)
            await sync_to_async(cache.delete)(f'buzz_lock_{self.session_id}')
            await self.channel_layer.group_send(self.group_name, {
                'type': 'broadcast_buzz_event', 'action': 'buzz_unlock'
            })
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f'auto unlock error: {e}')
 


    async def _is_session_expired(self, session: GameSession) -> bool:
        """
        التحقق من انتهاء صلاحية الجلسة:
        - المجاني: ساعة واحدة
        - المدفوع: لا ينتهي أبداً
        """
        if not session.package:
            return False
        
        # المدفوع: لا ينتهي
        if not session.package.is_free:
            return False
        
        # المجاني: ساعة واحدة
        expiry_time = session.created_at + timedelta(hours=1)
        return timezone.now() >= expiry_time

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}


    async def _handle_update_settings(self, settings: dict):
        """يستقبل الإعدادات من المقدم ويبثها لكافة الصفحات"""
        if not settings:
            return
        try:
            def _save():
                from games.models import GameSettings
                s = GameSettings.get_or_create_for_session(self.session)
                if 'team1_name' in settings and settings['team1_name']:
                    s.team1_name = settings['team1_name']
                if 'team2_name' in settings and settings['team2_name']:
                    s.team2_name = settings['team2_name']
                if 'team1_color' in settings and settings['team1_color']:
                    s.team1_color = settings['team1_color']
                if 'team2_color' in settings and settings['team2_color']:
                    s.team2_color = settings['team2_color']
                if 'show_name' in settings:
                    s.show_name = settings['show_name']
                if 'show_subtitle' in settings:
                    s.show_subtitle = settings['show_subtitle']
                s.save()
                return {
                    'team1_name':    s.team1_name,
                    'team2_name':    s.team2_name,
                    'team1_color':   s.team1_color,
                    'team2_color':   s.team2_color,
                    'show_name':     s.show_name or '',
                    'show_subtitle': s.show_subtitle or '',
                }
            saved = await sync_to_async(_save)()
        except Exception as e:
            logger.error(f'Pics settings save error: {e}')
            return

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_settings_update',
            'settings': saved,
        })

    async def broadcast_settings_update(self, event):
        """يبث الإعدادات لجميع المتصلين"""
        settings = event.get('settings', {})

        # تحديث المؤقت فورياً
        if 'buzz_timer_seconds' in settings:
            try:
                self.buzz_timer = max(1, int(settings['buzz_timer_seconds']))
            except (ValueError, TypeError):
                pass

        await self.send(text_data=json.dumps({
            'type': 'settings_updated',
            'settings': settings,
        }))


    async def _reply_contestant(self, confirmed=False, name="", team="", rejected="", error="", auto_host_timer=10, current_letter="", current_question_type="main"):
        if confirmed:
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed',
                'contestant_name': name,
                'team': team,
                'message': f'تم تسجيل إجابتك يا {name}!',
                'auto_host_timer': auto_host_timer,
                'current_letter': current_letter,
                'current_question_type': current_question_type,
            }))
            return
        if rejected:
            await self.send(text_data=json.dumps({'type': 'buzz_rejected', 'message': rejected}))
            return
        if error:
            await self.send(text_data=json.dumps({'type': 'error', 'message': error}))



# === Time Game Consumer (تحدي الوقت) ===
import json
import asyncio
import logging
from datetime import timedelta
import time
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from datetime import timedelta

from games.models import GameSession, TimeRiddle, TimeGameProgress
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

from games.models import GameSession, TimeRiddle, TimeGameProgress

tlogger = logging.getLogger('games')


class TimeGameConsumer(AsyncWebsocketConsumer):
    """
    WebSocket لتحدّي الوقت:
    - host: يتحكم بالصورة (next/prev/set_index) + إدارة المؤقّت (start/pause/reset).
    - display: يستقبل الصورة + حالة المؤقّتين ويعرضها.
    - contestant: يستقبل الصورة + حالة المؤقّتين؛ وعنده زر "جوّبت" يرسل stop&switch.
    منطق الوقت مثل ساعة الشطرنج: side A/B.
    """

    # --------------- Lifecycle ---------------
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"time_session_{self.session_id}"
        self.role = self._parse_qs().get('role', ['viewer'])[0]

        try:
            self.session = await self._get_session()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        if await self._is_session_expired(self.session) or not self.session.is_active:
            await self.close(code=4401)
            return

        # اقبل الاتصال أولًا
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        tlogger.info(f"WS connected (time): session={self.session_id}, role={self.role}")

        # حمّل الألغاز المرتبطة بالحزمة
        self.riddles = []
        try:
            self.riddles = await sync_to_async(lambda: list(
                TimeRiddle.objects.filter(package=self.session.package)
                .order_by('order')
                .values('order', 'image_url', 'hint', 'answer')
            ))()
        except Exception as e:
            tlogger.error(f"time: failed loading riddles for {self.session_id}: {e}")

        # تأكد من وجود progress وضبط الحدود
        try:
            await self._ensure_progress_bounds()
        except Exception as e:
            tlogger.error(f"time: ensure progress failed for {self.session_id}: {e}")

        # أرسل الحالة الأولية (صورة + مؤقتين)
        await self._send_puzzle_state()
        await self._send_timer_state()

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

    # --------------- Receive ---------------
    async def receive(self, text_data: str):
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

        # keep alive
        if t == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        # المتسابق: أوقف وقتي وبدّل للخصم
        if t == 'contestant_stop_and_switch' and self.role == 'contestant':
            side = (data.get('side') or '').upper()  # 'A'|'B'
            name = (data.get('contestant_name') or '').strip()
            await self._handle_contestant_stop_and_switch(side, name)
            return

        # المقدم: تحكّم
        if self.role == 'host':
            if t == 'puzzle_nav':
                await self._handle_nav(data.get('dir'))
                return
            if t == 'puzzle_set_index':
                await self._handle_set_index(data.get('index'))
                return
            if t == 'timer_start':
                side = (data.get('side') or '').upper()
                await self._handle_timer_start(side)
                return
            if t == 'timer_pause':
                await self._handle_timer_pause()
                return
            if t == 'timer_reset':
                seconds_each = int(data.get('seconds_each') or 60)
                start_side = (data.get('start_side') or 'A').upper()
                # أسماء اللاعبين (اختياري)
                a_name = (data.get('player_a_name') or '').strip()
                b_name = (data.get('player_b_name') or '').strip()
                await self._handle_timer_reset(seconds_each, start_side, a_name, b_name)
                return

    # --------------- Handlers: Puzzle ---------------
    async def _handle_nav(self, dir_):
        if dir_ not in ('next', 'prev'):
            return
        total = max(1, len(self.riddles) or 1)

        def _upd():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)
            if dir_ == 'next':
                prog.current_index = min(prog.current_index + 1, total)
            else:
                prog.current_index = max(prog.current_index - 1, 1)
            prog.save(update_fields=['current_index'])
            return prog.current_index

        await sync_to_async(_upd)()
        await self._broadcast_puzzle_state()

    async def _handle_set_index(self, index):
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return
        total = max(1, len(self.riddles) or 1)

        def _upd():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)
            prog.current_index = max(1, min(idx, total))
            prog.save(update_fields=['current_index'])
            return prog.current_index

        await sync_to_async(_upd)()
        await self._broadcast_puzzle_state()

    # --------------- Handlers: Timer Core ---------------
    async def _handle_timer_start(self, side):
        if side not in ('A', 'B'):
            return

        now = timezone.now()

        def _start():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)
            # لو كان يجري، نخصم أولًا
            if prog.is_running and prog.active_side in ('A', 'B') and prog.last_started_at:
                elapsed = (now - prog.last_started_at).total_seconds()
                if prog.active_side == 'A':
                    prog.a_time_left_seconds = max(0, int(prog.a_time_left_seconds - elapsed))
                else:
                    prog.b_time_left_seconds = max(0, int(prog.b_time_left_seconds - elapsed))

            # ابدأ الجانب المطلوب إن بقي وقت
            if side == 'A' and prog.a_time_left_seconds > 0:
                prog.active_side = 'A'; prog.is_running = True; prog.last_started_at = now
            elif side == 'B' and prog.b_time_left_seconds > 0:
                prog.active_side = 'B'; prog.is_running = True; prog.last_started_at = now
            else:
                # لا يوجد وقت متبقٍ لهذا الجانب
                prog.is_running = False
                prog.active_side = None
                prog.last_started_at = None

            prog.save(update_fields=[
                'a_time_left_seconds', 'b_time_left_seconds',
                'active_side', 'is_running', 'last_started_at'
            ])

        await sync_to_async(_start)()
        await self._broadcast_timer_state()

    async def _handle_timer_pause(self):
        now = timezone.now()

        def _pause():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)
            if prog.is_running and prog.active_side in ('A', 'B') and prog.last_started_at:
                elapsed = (now - prog.last_started_at).total_seconds()
                if prog.active_side == 'A':
                    prog.a_time_left_seconds = max(0, int(prog.a_time_left_seconds - elapsed))
                else:
                    prog.b_time_left_seconds = max(0, int(prog.b_time_left_seconds - elapsed))
            prog.is_running = False
            prog.last_started_at = None
            prog.save(update_fields=[
                'a_time_left_seconds', 'b_time_left_seconds',
                'is_running', 'last_started_at'
            ])

        await sync_to_async(_pause)()
        await self._broadcast_timer_state()

    async def _handle_timer_reset(self, seconds_each, start_side, a_name, b_name):
        now = timezone.now()
        seconds_each = max(1, int(seconds_each))
        start_side = 'A' if start_side != 'B' else 'B'

        def _reset():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)
            prog.a_time_left_seconds = seconds_each
            prog.b_time_left_seconds = seconds_each
            prog.player_a_name = a_name or prog.player_a_name or ""
            prog.player_b_name = b_name or prog.player_b_name or ""
            prog.active_side = start_side
            prog.is_running = True
            prog.last_started_at = now
            prog.save(update_fields=[
                'a_time_left_seconds', 'b_time_left_seconds',
                'player_a_name', 'player_b_name',
                'active_side', 'is_running', 'last_started_at'
            ])

        await sync_to_async(_reset)()
        await self._broadcast_timer_state()

    async def _handle_contestant_stop_and_switch(self, side, name):
        """
        المتسابق على الجانب (A/B) يضغط: نخصم وقته منذ آخر تشغيل → نوقفه → نبدّل للخصم ويبدأ من رصيده الحالي.
        """
        if side not in ('A', 'B'):
            await self._reply(error='Side غير صحيح')
            return

        now = timezone.now()

        def _stop_and_switch():
            prog = TimeGameProgress.objects.select_for_update().get(session=self.session)

            # حفظ الاسم اختياريًا (لو أرسله أول مرة)
            if side == 'A' and name and not prog.player_a_name:
                prog.player_a_name = name
            if side == 'B' and name and not prog.player_b_name:
                prog.player_b_name = name

            # يجب أن يكون الدور للـside نفسه وهو يجري
            if not prog.is_running or prog.active_side != side or not prog.last_started_at:
                return False, prog

            # خصم الوقت المنقضي
            elapsed = (now - prog.last_started_at).total_seconds()
            if side == 'A':
                prog.a_time_left_seconds = max(0, int(prog.a_time_left_seconds - elapsed))
            else:
                prog.b_time_left_seconds = max(0, int(prog.b_time_left_seconds - elapsed))

            # إنتهى وقت هذا الجانب؟
            if (side == 'A' and prog.a_time_left_seconds <= 0) or (side == 'B' and prog.b_time_left_seconds <= 0):
                prog.is_running = False
                prog.active_side = None
                prog.last_started_at = None
                prog.save(update_fields=[
                    'a_time_left_seconds','b_time_left_seconds','is_running','active_side','last_started_at',
                    'player_a_name','player_b_name'
                ])
                return True, prog

            # بدّل للخصم وابدأ فورًا من رصيده الحالي
            next_side = 'B' if side == 'A' else 'A'
            # لو الخصم وقته صفر، نوقف اللعبة
            if (next_side == 'A' and prog.a_time_left_seconds <= 0) or (next_side == 'B' and prog.b_time_left_seconds <= 0):
                prog.is_running = False
                prog.active_side = None
                prog.last_started_at = None
            else:
                prog.active_side = next_side
                prog.is_running = True
                prog.last_started_at = now

            prog.save(update_fields=[
                'a_time_left_seconds','b_time_left_seconds','is_running','active_side','last_started_at',
                'player_a_name','player_b_name'
            ])
            return True, prog

        ok, _ = await sync_to_async(_stop_and_switch)()
        if not ok:
            await self._reply(error='الحالة غير صالحة الآن (ليس دورك أو المؤقت متوقف).')
            return

        await self._broadcast_timer_state()

    # --------------- Group broadcasts ---------------
    async def broadcast_puzzle_state(self, event):
        await self.send(text_data=json.dumps({
            'type': 'puzzle_updated',
            'index': event.get('index'),
            'total': event.get('total'),
            'image_url': event.get('image_url'),
            'hint': event.get('hint'),
            'answer': event.get('answer'),
        }))

    async def broadcast_timer_state(self, event):
        await self.send(text_data=json.dumps({
            'type': 'timer_state',
            'active_side': event.get('active_side'),
            'a_left': event.get('a_left'),
            'b_left': event.get('b_left'),
            'is_running': event.get('is_running'),
            'last_started_at': event.get('last_started_at'),
            'player_a_name': event.get('player_a_name') or '',
            'player_b_name': event.get('player_b_name') or '',
        }))

    # --------------- Helpers: puzzle state ---------------
    async def _send_puzzle_state(self):
        idx = await self._get_current_index()
        await self.send(text_data=json.dumps({'type': 'puzzle_updated', **self._state_payload(idx)}))

    async def _broadcast_puzzle_state(self):
        idx = await self._get_current_index()
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_puzzle_state', **self._state_payload(idx)
        })

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
            return TimeGameProgress.objects.filter(session=self.session)\
                   .values_list('current_index', flat=True).first() or 1
        return await sync_to_async(_read)()

    async def _ensure_progress_bounds(self):
        def _ensure():
            obj, _ = TimeGameProgress.objects.get_or_create(
                session=self.session,
                defaults={
                    'current_index': 1,
                    'a_time_left_seconds': 60,
                    'b_time_left_seconds': 60,
                    'active_side': None,
                    'is_running': False,
                    'last_started_at': None
                }
            )
            total = max(1, len(self.riddles) or 1)
            if obj.current_index < 1 or obj.current_index > total:
                obj.current_index = 1
                obj.save(update_fields=['current_index'])
        return await sync_to_async(_ensure)()

    # --------------- Helpers: timer state ---------------
    async def _send_timer_state(self):
        payload = await self._timer_payload()
        await self.send(text_data=json.dumps({'type': 'timer_state', **payload}))

    async def _broadcast_timer_state(self):
        payload = await self._timer_payload()
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_timer_state', **payload
        })

    async def _timer_payload(self):
        def _read():
            p = TimeGameProgress.objects.filter(session=self.session).values(
                'a_time_left_seconds', 'b_time_left_seconds',
                'active_side', 'is_running', 'last_started_at',
                'player_a_name', 'player_b_name'
            ).first()
            return p or {}
        p = await sync_to_async(_read)()

        # نعيد القيم كما هي؛ الواجهات تحدث العرض محليًا باستخدام last_started_at (إن كان جاريًا)
        last_ts = p.get('last_started_at')
        last_iso = last_ts.isoformat() if last_ts else None

        return {
            'active_side': p.get('active_side'),
            'a_left': int(p.get('a_time_left_seconds') or 0),
            'b_left': int(p.get('b_time_left_seconds') or 0),
            'is_running': bool(p.get('is_running')),
            'last_started_at': last_iso,
            'player_a_name': p.get('player_a_name') or '',
            'player_b_name': p.get('player_b_name') or '',
        }

    # --------------- Misc helpers ---------------
    async def _get_session(self):
        return await sync_to_async(
            lambda: GameSession.objects.select_related('package').get(id=self.session_id)
        )()

    async def _is_session_expired(self, session: GameSession) -> bool:
        """
        التحقق من انتهاء صلاحية الجلسة:
        - المجاني: ساعة واحدة
        - المدفوع: لا ينتهي أبداً
        """
        if not session.package:
            return False
        
        # المدفوع: لا ينتهي
        if not session.package.is_free:
            return False
        
        # المجاني: ساعة واحدة
        expiry_time = session.created_at + timedelta(hours=1)
        return timezone.now() >= expiry_time

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}

    async def _reply(self, error=''):
        if error:
            await self.send(text_data=json.dumps({'type': 'error', 'message': error}))




# =========================
#  فاميلي فيود Consumer
# =========================

from games.models import FamilyFeudProgress, FamilyFeudAnswer

class FamilyFeudConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"feud_session_{self.session_id}"
        self.role = self._parse_qs().get('role', ['viewer'])[0]

        try:
            self.session = await sync_to_async(
                lambda: GameSession.objects.select_related('package').get(id=self.session_id)
            )()
        except ObjectDoesNotExist:
            await self.close(code=4404)
            return

        if not self.session.is_active:
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WS connected (feud): session={self.session_id}, role={self.role}")

        # أرسل الحالة الأولية
        await self._send_initial_state()

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

    async def receive(self, text_data: str):
        try:
            data = json.loads(text_data or '{}')
        except json.JSONDecodeError:
            return

        t = data.get('type')

        if t == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        # المقدم فقط
        if self.role == 'host':
            if t == 'reveal_answer':
                await self._handle_reveal_answer(data.get('rank'))
            elif t == 'mark_strike':
                await self._handle_mark_strike(data.get('team'))
            elif t == 'reset_strikes':
                await self._handle_reset_strikes()
            elif t == 'award_points':
                await self._handle_award_points(data.get('team'))
            elif t == 'next_question':
                await self._handle_next_question()
            elif t == 'prev_question':
                await self._handle_prev_question()
            elif t == 'set_question':
                await self._handle_set_question(data.get('index'))
            elif t == 'set_phase':
                await self._handle_set_phase(data.get('phase'))
            elif t == 'set_controlling_team':
                await self._handle_set_controlling_team(data.get('team'))
            elif t == 'set_multiplier':
                await self._handle_set_multiplier(data.get('multiplier'))
            elif t == 'update_scores':
                await self._handle_update_scores(
                    data.get('team1_score'), data.get('team2_score')
                )
            elif t == 'show_question':
                await self._handle_show_question(data.get('show', True))
            elif t == 'buzz_reset':
                await self._handle_buzz_reset()
            elif t == 'update_team_names':
                await self._handle_update_team_names(
                    data.get('team1_name'),
                    data.get('team2_name'),
                    data.get('game_title'),
                )

        # المتسابق
        if self.role == 'contestant' and t == 'contestant_buzz':
            await self._handle_buzz(data)

    # ==================== Handlers ====================

    async def _handle_reveal_answer(self, rank):
        if rank is None:
            return

        def _reveal():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                if rank not in progress.revealed_answers:
                    progress.revealed_answers = progress.revealed_answers + [rank]
                    q = progress.session.package.feud_questions.filter(
                        order=progress.current_question_index
                    ).first()
                    pts = 0
                    if q:
                        ans = q.answers.filter(rank=rank).first()
                        if ans:
                            pts = ans.points * progress.current_multiplier
                    progress.round_points += pts
                    progress.save(update_fields=['revealed_answers', 'round_points'])
                    return progress, pts
                return progress, 0

        progress, pts = await sync_to_async(_reveal)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_answer_revealed',
            'rank': rank,
            'points_added': pts,
            'round_points': progress.round_points,
            'revealed_answers': progress.revealed_answers,
        })

    async def _handle_mark_strike(self, team):
        def _strike():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                if team == 'team1':
                    progress.team1_strikes = min(3, progress.team1_strikes + 1)
                elif team == 'team2':
                    progress.team2_strikes = min(3, progress.team2_strikes + 1)
                progress.save(update_fields=['team1_strikes', 'team2_strikes'])
                return progress

        progress = await sync_to_async(_strike)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_strike',
            'team': team,
            'team1_strikes': progress.team1_strikes,
            'team2_strikes': progress.team2_strikes,
        })

    async def _handle_reset_strikes(self):
        def _reset():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                progress.team1_strikes = 0
                progress.team2_strikes = 0
                progress.save(update_fields=['team1_strikes', 'team2_strikes'])
                return progress

        progress = await sync_to_async(_reset)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_strike',
            'team': None,
            'team1_strikes': 0,
            'team2_strikes': 0,
        })

    async def _handle_award_points(self, team):
        def _award():
            from django.db import transaction
            with transaction.atomic():
                session = GameSession.objects.select_for_update().get(id=self.session_id)
                progress = FamilyFeudProgress.objects.select_for_update().get(session=session)
                pts = progress.round_points
                if team == 'team1':
                    session.team1_score += pts
                elif team == 'team2':
                    session.team2_score += pts
                progress.round_points = 0
                session.save(update_fields=['team1_score', 'team2_score'])
                progress.save(update_fields=['round_points'])
                return session, pts

        session, pts = await sync_to_async(_award)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_score_update',
            'team1_score': session.team1_score,
            'team2_score': session.team2_score,
            'awarded_team': team,
            'awarded_points': pts,
        })

    async def _handle_update_team_names(self, t1_name, t2_name, game_title=None):
        if not t1_name or not t2_name:
            return

        def _save():
            from games.models import GameSettings

            session = GameSession.objects.get(id=self.session_id)
            session.team1_name = t1_name[:50]
            session.team2_name = t2_name[:50]
            session.save(update_fields=['team1_name', 'team2_name'])

            settings = GameSettings.get_or_create_for_session(self.session)

            clean_title = (game_title or '').strip()
            if clean_title and clean_title != 'فاميلي فيود':
                if clean_title.endswith(' فيود'):
                    clean_title = clean_title[:-5].strip()
                settings.show_name = clean_title
                settings.save(update_fields=['show_name'])
                final_title = f'{clean_title} فيود'
            else:
                settings.show_name = ''
                settings.save(update_fields=['show_name'])
                final_title = 'فاميلي فيود'

            return final_title

        final_title = await sync_to_async(_save)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_team_names',
            'team1_name': t1_name,
            'team2_name': t2_name,
            'game_title': final_title,
        })

    async def broadcast_team_names(self, event):
        await self.send(text_data=json.dumps({
            'type': 'team_names_updated',
            'team1_name': event['team1_name'],
            'team2_name': event['team2_name'],
            'game_title': event.get('game_title', 'فاميلي فيود'),
        }))
    async def _handle_next_question(self):
        def _next():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                total = self.session.package.feud_questions.count()
                if progress.current_question_index < total:
                    progress.current_question_index += 1
                    progress.reset_round()
                    progress.save()
                return progress

        progress = await sync_to_async(_next)()
        await self._broadcast_full_state(progress)

    async def _handle_prev_question(self):
        def _prev():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                if progress.current_question_index > 1:
                    progress.current_question_index -= 1
                    progress.reset_round()
                    progress.save()
                return progress

        progress = await sync_to_async(_prev)()
        await self._broadcast_full_state(progress)

    async def _handle_set_question(self, index):
        if index is None:
            return

        def _set():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                total = self.session.package.feud_questions.count()
                idx = max(1, min(int(index), total))
                progress.current_question_index = idx
                progress.reset_round()
                progress.save()
                return progress

        progress = await sync_to_async(_set)()
        await self._broadcast_full_state(progress)

    async def _handle_set_phase(self, phase):
        valid = ['waiting', 'question', 'buzzer', 'team1_turn', 'team2_turn', 'steal', 'award', 'finished']
        if phase not in valid:
            return

        def _set():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                progress.phase = phase
                progress.save(update_fields=['phase'])
                return progress

        progress = await sync_to_async(_set)()
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_phase_change',
            'phase': phase,
        })

    async def _handle_set_controlling_team(self, team):
        def _set():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                progress.controlling_team = team or ''
                progress.save(update_fields=['controlling_team'])
                return progress

        await sync_to_async(_set)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_controlling_team',
            'team': team,
        })

    async def _handle_set_multiplier(self, multiplier):
        try:
            m = int(multiplier)
            if m not in (1, 2, 3):
                return
        except (TypeError, ValueError):
            return

        def _set():
            from django.db import transaction
            with transaction.atomic():
                progress = FamilyFeudProgress.objects.select_for_update().get(session=self.session)
                progress.current_multiplier = m
                progress.save(update_fields=['current_multiplier'])

        await sync_to_async(_set)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_multiplier',
            'multiplier': m,
        })

    async def _handle_update_scores(self, t1, t2):
        try:
            t1 = max(0, int(t1))
            t2 = max(0, int(t2))
        except (TypeError, ValueError):
            return

        def _update():
            from django.db import transaction
            with transaction.atomic():
                session = GameSession.objects.select_for_update().get(id=self.session_id)
                session.team1_score = t1
                session.team2_score = t2
                session.save(update_fields=['team1_score', 'team2_score'])

        await sync_to_async(_update)()

        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_score_update',
            'team1_score': t1,
            'team2_score': t2,
        })

    async def _handle_show_question(self, show):
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_question_visibility',
            'show': bool(show),
        })

    async def _handle_buzz_reset(self):
        buzz_key = f"buzz_lock_feud_{self.session_id}"
        await sync_to_async(cache.delete)(buzz_key)
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'action': 'buzz_reset',
        })

    async def _handle_buzz(self, data):
        name = (data.get('contestant_name') or '').strip()
        team = data.get('team')
        if not name or team not in ('team1', 'team2'):
            return

        buzz_key = f"buzz_lock_feud_{self.session_id}"
        payload = {'name': name, 'team': team}

        # اقرأ المؤقت من cache — نفس منطق Letters
        timer_cache_key = f"buzz_timer_{self.session_id}"
        buzz_timer = await sync_to_async(cache.get)(timer_cache_key)
        if buzz_timer is None:
            try:
                from games.models import GameSettings
                s = GameSettings.get_or_create_for_session(self.session)
                buzz_timer = max(1, s.buzz_timer_seconds or 3)
            except Exception:
                buzz_timer = 3
            await sync_to_async(cache.set)(timer_cache_key, buzz_timer, timeout=600)

        lock_ttl = buzz_timer + 2

        try:
            added = await sync_to_async(cache.add)(buzz_key, payload, timeout=lock_ttl)
        except Exception:
            added = False

        if not added:
            cur = await sync_to_async(cache.get)(buzz_key) or {}
            await self.send(text_data=json.dumps({
                'type': 'buzz_rejected',
                'message': f'الزر محجوز من {cur.get("name", "مشارك")}'
            }))
            return

        await self.send(text_data=json.dumps({
            'type': 'buzz_confirmed',
            'contestant_name': name,
            'team': team,
            'message': f'تم تسجيل إجابتك يا {name}!'
        }))

        team_display = self.session.team1_name if team == 'team1' else self.session.team2_name
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_buzz_event',
            'action': 'buzz_accepted',
            'contestant_name': name,
            'team': team,
            'team_display': team_display,
        })

    # ==================== Group Broadcasts ====================

    async def broadcast_answer_revealed(self, event):
        await self.send(text_data=json.dumps({
            'type': 'answer_revealed',
            'rank': event['rank'],
            'points_added': event['points_added'],
            'round_points': event['round_points'],
            'revealed_answers': event['revealed_answers'],
        }))

    async def broadcast_strike(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'strike_updated',
            'team': event.get('team'),
            'team1_strikes': event['team1_strikes'],
            'team2_strikes': event['team2_strikes'],
        }))

    async def broadcast_score_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event['team1_score'],
            'team2_score': event['team2_score'],
            'awarded_team': event.get('awarded_team'),
            'awarded_points': event.get('awarded_points'),
            'team1_name': event.get('team1_name'),
            'team2_name': event.get('team2_name'),
        }))
    async def broadcast_phase_change(self, event):
        await self.send(text_data=json.dumps({
            'type': 'phase_changed',
            'phase': event['phase'],
        }))

    async def broadcast_controlling_team(self, event):
        await self.send(text_data=json.dumps({
            'type': 'controlling_team_changed',
            'team': event['team'],
        }))

    async def broadcast_multiplier(self, event):
        if self.role == 'contestant':
            return
        await self.send(text_data=json.dumps({
            'type': 'multiplier_changed',
            'multiplier': event['multiplier'],
        }))

    async def broadcast_question_visibility(self, event):
        await self.send(text_data=json.dumps({
            'type': 'question_visibility',
            'show': event['show'],
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
                'start_countdown': True,
            }))
        elif action == 'buzz_reset':
            if self.role == 'contestant':
                return
            await self.send(text_data=json.dumps({'type': 'buzz_reset_by_host'}))

    async def broadcast_full_state(self, event):
        await self.send(text_data=json.dumps({
            'type':             'full_state',
            'question_index':   event['question_index'],
            'question_text':    event.get('question_text', ''),
            'answers':          event.get('answers', []),
            'revealed_answers': event.get('revealed_answers', []),
            'team1_strikes':    event['team1_strikes'],
            'team2_strikes':    event['team2_strikes'],
            'round_points':     event['round_points'],
            'controlling_team': event.get('controlling_team', ''),
            'phase':            event.get('phase', 'waiting'),
            'multiplier':       event.get('multiplier', 1),
            'total_questions':  event.get('total_questions', 0),
            'team1_score':      event.get('team1_score', 0),
            'team2_score':      event.get('team2_score', 0),
            'team1_name':       event.get('team1_name', ''),
            'team2_name':       event.get('team2_name', ''),
            'game_title':       event.get('game_title', 'فاميلي فيود'),
        }))

    # ==================== Helpers ====================

    async def _send_initial_state(self):
        def _get():
            progress = FamilyFeudProgress.objects.filter(session=self.session).first()
            if not progress:
                progress = FamilyFeudProgress.objects.create(
                    session=self.session,
                    current_question_index=1,
                    phase='waiting'
                )
            session = GameSession.objects.select_related('package').get(id=self.session_id)
            questions = session.package.feud_questions.order_by('order')
            total = questions.count()
            if total == 0:
                return None
            q = questions.filter(order=progress.current_question_index).first()
            if not q:
                q = questions.first()
                progress.current_question_index = q.order
                progress.save(update_fields=['current_question_index'])
            answers = []
            if q:
                for ans in q.answers.all().order_by('rank'):
                    answers.append({'rank': ans.rank, 'text': ans.text, 'points': ans.points})
            return {
                'question_index':   progress.current_question_index,
                'question_text':    q.question_text if q else '',
                'answers':          answers,
                'revealed_answers': progress.revealed_answers or [],
                'team1_strikes':    progress.team1_strikes,
                'team2_strikes':    progress.team2_strikes,
                'round_points':     progress.round_points,
                'controlling_team': progress.controlling_team,
                'phase':            progress.phase,
                'multiplier':       progress.current_multiplier,
                'total_questions':  total,
                'team1_score':      session.team1_score,
                'team2_score':      session.team2_score,
                'team1_name':       session.team1_name,
                'team2_name':       session.team2_name,
                'game_title': self._get_game_title(),
            }

        state = await sync_to_async(_get)()
        if state:
            await self.send(text_data=json.dumps({'type': 'full_state', **state}))

    async def _broadcast_full_state(self, progress=None):
        def _get():
            session = GameSession.objects.select_related('package').get(id=self.session_id)
            p = progress or FamilyFeudProgress.objects.get(session=session)
            questions = session.package.feud_questions.order_by('order')
            total = questions.count()
            q = questions.filter(order=p.current_question_index).first()
            if not q and questions.exists():
                q = questions.first()
            answers = []
            if q:
                for ans in q.answers.all().order_by('rank'):
                    answers.append({'rank': ans.rank, 'text': ans.text, 'points': ans.points})
            return {
                'question_index':   p.current_question_index,
                'question_text':    q.question_text if q else '',
                'answers':          answers,
                'revealed_answers': p.revealed_answers or [],
                'team1_strikes':    p.team1_strikes,
                'team2_strikes':    p.team2_strikes,
                'round_points':     p.round_points,
                'controlling_team': p.controlling_team,
                'phase':            p.phase,
                'multiplier':       p.current_multiplier,
                'total_questions':  total,
                'team1_score':      session.team1_score,
                'team2_score':      session.team2_score,
                'team1_name':       session.team1_name,
                'team2_name':       session.team2_name,
                'game_title': self._get_game_title(),
            }

        state = await sync_to_async(_get)()
        await self.channel_layer.group_send(self.group_name, {
            'type': 'broadcast_full_state', **state
        })

    def _parse_qs(self):
        try:
            from urllib.parse import parse_qs
            return parse_qs(self.scope.get('query_string', b'').decode())
        except Exception:
            return {}
        

    def _get_game_title(self):
        try:
            from games.models import GameSettings
            s = GameSettings.objects.filter(session=self.session).first()
            if s and s.show_name:
                custom_name = s.show_name.strip()
                if custom_name:
                    return f'{custom_name} فيود'
        except Exception:
            pass
        return 'فاميلي فيود'