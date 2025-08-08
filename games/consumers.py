# games/consumers.py

import json
import asyncio
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.core.cache import cache

from games.models import GameSession, LettersGameQuestion, Contestant

logger = logging.getLogger('games')


class LettersGameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(f"WebSocket connected for session: {self.session_id}")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info(f"WebSocket disconnected for session: {self.session_id}")

        async def receive(self, text_data):
            try:
                data = json.loads(text_data)
                message_type = data.get('type')

                logger.info(f"Received message type: {message_type} for session: {self.session_id}")

                if message_type == "contestant_buzz":
                    await self.handle_contestant_buzz(data)
                elif message_type == "select_letter":
                    await self.handle_select_letter(data)
                elif message_type == "update_cell_state":
                    await self.handle_update_cell_state(data)
                elif message_type == "update_scores":
                    await self.handle_update_scores(data)
                elif message_type == "buzz_reset":
                    await self.handle_buzz_reset(data)
                elif message_type == "ping":
                    # رد اختياري
                    await self.send(text_data=json.dumps({"type": "pong"}))
                else:
                    # تجاهل أي نوع غير معروف (لا تفك القفل بالخطأ)
                    logger.debug(f"Ignored unknown message type: {message_type}")

            except json.JSONDecodeError:
                ...


    # -------------------------
    # Handlers
    # -------------------------
    async def handle_contestant_buzz(self, data):
        """أول متسابق يضغط يحجز الزر 5 ثواني، ويظهر اسمه للجميع، ويبدأ مؤثر 3 ثواني ثم نفك القفل تلقائياً."""
        contestant_name = data.get("contestant_name")
        team = data.get("team")
        timestamp = data.get("timestamp")

        if not contestant_name or not team:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'اسم المتسابق والفريق مطلوبان'
            }))
            return

        try:
            session = await self.get_session()
            buzz_lock_key = f"buzz_lock_{self.session_id}"

            current_buzzer = cache.get(buzz_lock_key)
            if current_buzzer:
                # الزر محجوز بالفعل
                await self.send(text_data=json.dumps({
                    'type': 'buzz_rejected',
                    'message': f'الزر محجوز من {current_buzzer["name"]}',
                    'locked_by': current_buzzer['name']
                }))
                return

            # حجز لمدة 5 ثواني
            cache.set(buzz_lock_key, {
                'name': contestant_name,
                'team': team,
                'timestamp': timestamp
            }, timeout=5)

            # بلغ الجميع إن الزر انقفل ومن حجزه
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_buzz_lock',
                    'message': f'{contestant_name} حجز الزر',
                    'locked_by': contestant_name,
                    'team': team
                }
            )

            # سجّل المتسابق إن لم يكن موجود
            await self.register_contestant_if_needed(session, contestant_name, team)

            # أكد للمتسابق صاحب الضغطة
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed',
                'contestant_name': contestant_name,
                'team': team,
                'message': f'تم تسجيل إجابتك يا {contestant_name}!'
            }))

            # بث الاسم للفريقين (شاشة العرض + المقدم)
            team_display = await self.get_team_display_name(session, team)
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_contestant_buzz',
                    'contestant_name': contestant_name,
                    'team': team,
                    'team_display': team_display,
                    'timestamp': timestamp
                }
            )

            # فك القفل بصرياً بعد 3 ثواني + حذف الكي من الكاش
            asyncio.create_task(self._auto_unlock_visual())

            logger.info(f"Buzz accepted: {contestant_name} from {team} in session {self.session_id}")

        except Exception as e:
            logger.error(f"Error handling contestant buzz: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'فشل في معالجة الضغطة'
            }))

    async def handle_buzz_reset(self):
        """إعادة تعيين القفل يدوياً من المقدم (أو أي عميل مخوّل)."""
        try:
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)

            # بلّغ الجميع يمسحوا الواجهة ويظهر القفل متاح
            await self.channel_layer.group_send(self.group_name, {'type': 'broadcast_buzz_reset'})
            await self.channel_layer.group_send(
                self.group_name,
                {'type': 'broadcast_buzz_unlock', 'message': 'تم فك قفل الزر'}
            )
            logger.info(f"Buzzer reset for session: {self.session_id}")
        except Exception as e:
            logger.error(f"Error resetting buzzer: {e}")

    async def handle_select_letter(self, data):
        letter = data.get('letter')
        if not letter:
            return
        try:
            session = await self.get_session()
            package = session.package
            question_obj = await self.get_question(package, letter, 'main')

            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_question',
                    'letter': letter,
                    'question': question_obj.question,
                    'answer': question_obj.answer,
                    'category': question_obj.category
                }
            )
            logger.info(f"Question sent for letter {letter} in session {self.session_id}")
        except Exception as e:
            logger.error(f"Error handling select_letter: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'خطأ في جلب السؤال: {str(e)}'
            }))

    async def handle_update_cell_state(self, data):
        letter = data.get('letter')
        state = data.get('state')
        if letter and state:
            await self.channel_layer.group_send(
                self.group_name,
                {'type': 'broadcast_cell_update', 'letter': letter, 'state': state}
            )

    async def handle_update_scores(self, data):
        team1_score = data.get('team1_score', 0)
        team2_score = data.get('team2_score', 0)
        await self.channel_layer.group_send(
            self.group_name,
            {'type': 'broadcast_score_update', 'team1_score': team1_score, 'team2_score': team2_score}
        )

    # -------------------------
    # Broadcasters (to clients)
    # -------------------------
    async def broadcast_question(self, event):
        await self.send(text_data=json.dumps({
            'type': 'show_question',
            'letter': event['letter'],
            'question': event['question'],
            'answer': event.get('answer', ''),
            'category': event.get('category', '')
        }))

    async def broadcast_contestant_buzz(self, event):
        await self.send(text_data=json.dumps({
            'type': 'show_contestant_buzz',
            'contestant_name': event['contestant_name'],
            'team': event['team'],
            'team_display': event.get('team_display', ''),
            'timestamp': event.get('timestamp')
        }))

    async def broadcast_buzz_lock(self, event):
        await self.send(text_data=json.dumps({
            'type': 'buzz_lock',
            'message': event.get('message', ''),
            'locked_by': event.get('locked_by', ''),
            'team': event.get('team', '')
        }))

    async def broadcast_buzz_unlock(self, event):
        await self.send(text_data=json.dumps({
            'type': 'buzz_unlock',
            'message': event.get('message', 'الزر متاح للضغط')
        }))

    async def broadcast_buzz_reset(self, event):
        await self.send(text_data=json.dumps({'type': 'buzz_reset'}))

    async def broadcast_cell_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'cell_state_updated',
            'letter': event['letter'],
            'state': event['state']
        }))

    async def broadcast_score_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'scores_updated',
            'team1_score': event['team1_score'],
            'team2_score': event['team2_score']
        }))

    async def broadcast_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    # -------------------------
    # Helpers
    # -------------------------
    async def get_session(self):
        return await sync_to_async(GameSession.objects.get)(id=self.session_id)

    async def get_question(self, package, letter, question_type):
        return await sync_to_async(LettersGameQuestion.objects.get)(
            package=package, letter=letter, question_type=question_type
        )

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
        """يفتح القفل بعد 3 ثواني تلقائياً ويبث الحدث، ويمسح مفتاح القفل من الكاش."""
        try:
            await asyncio.sleep(3)
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)
            await self.channel_layer.group_send(
                self.group_name,
                {'type': 'broadcast_buzz_unlock', 'message': 'انتهى الوقت'}
            )
        except Exception as e:
            logger.error(f"auto_unlock_visual error for session {self.session_id}: {e}")
