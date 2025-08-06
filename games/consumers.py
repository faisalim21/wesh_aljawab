# في ملف games/consumers.py - إضافة نظام حجز الزر

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.exceptions import ObjectDoesNotExist
from games.models import GameSession, LettersGameQuestion, Contestant
from asgiref.sync import sync_to_async
from django.core.cache import cache
import logging
from datetime import datetime, timedelta

logger = logging.getLogger('games')

class LettersGameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # تسجيل الاتصال
        logger.info(f"WebSocket connected for session: {self.session_id}")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        
        logger.info(f"WebSocket disconnected for session: {self.session_id}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            logger.info(f"Received message type: {message_type} for session: {self.session_id}")

            if message_type == "select_letter":
                await self.handle_select_letter(data)
                
            elif message_type == "contestant_buzz":
                await self.handle_contestant_buzz(data)
                
            elif message_type == "update_cell_state":
                await self.handle_update_cell_state(data)
                
            elif message_type == "update_scores":
                await self.handle_update_scores(data)
                
            elif message_type == "buzz_reset":
                await self.handle_buzz_reset(data)
                
            else:
                # أي رسالة أخرى تُبث كما هي
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'broadcast_update',
                        'payload': data
                    }
                )
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received for session: {self.session_id}")
            await self.send(text_data=json.dumps({
                'type': 'error', 
                'message': 'بيانات غير صحيحة'
            }))
        except Exception as e:
            logger.error(f"Error processing message for session {self.session_id}: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error', 
                'message': 'حدث خطأ في معالجة الرسالة'
            }))

    async def handle_contestant_buzz(self, data):
        """معالجة ضغط المتسابق مع نظام الحجز"""
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
            # التحقق من صحة الجلسة
            session = await self.get_session()
            
            # مفتاح حجز الزر في Cache
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            
            # التحقق من حالة الزر
            current_buzzer = cache.get(buzz_lock_key)
            
            if current_buzzer:
                # الزر محجوز بالفعل
                logger.info(f"Buzz rejected: Button locked by {current_buzzer['name']} in session {self.session_id}")
                
                await self.send(text_data=json.dumps({
                    'type': 'buzz_rejected',
                    'message': f'الزر محجوز من {current_buzzer["name"]}',
                    'locked_by': current_buzzer['name'],
                    'locked_team': current_buzzer['team']
                }))
                return
            
            # حجز الزر للمتسابق (5 ثوان)
            buzzer_data = {
                'name': contestant_name,
                'team': team,
                'timestamp': timestamp,
                'session_id': self.session_id
            }
            
            cache.set(buzz_lock_key, buzzer_data, timeout=5)  # حجز لمدة 5 ثوان
            
            # تسجيل المتسابق إذا لم يكن مسجلاً
            await self.register_contestant_if_needed(session, contestant_name, team)
            
            # بث ضغطة المتسابق لجميع الصفحات
            buzz_data = {
                'type': 'contestant_buzz',
                'contestant_name': contestant_name,
                'team': team,
                'timestamp': timestamp,
                'session_id': self.session_id,
                'team_display': await self.get_team_display_name(session, team)
            }
            
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_contestant_buzz',
                    **buzz_data
                }
            )
            
            # إرسال تأكيد للمتسابق
            await self.send(text_data=json.dumps({
                'type': 'buzz_confirmed',
                'contestant_name': contestant_name,
                'team': team,
                'message': f'حجزت الزر بنجاح يا {contestant_name}! لديك 3 ثوان'
            }))
            
            logger.info(f"Buzz accepted: {contestant_name} from {team} locked the buzzer in session {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error handling contestant buzz: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'فشل في معالجة الضغطة'
            }))

    async def handle_buzz_reset(self, data):
        """إعادة تعيين حالة الزر"""
        try:
            # إلغاء حجز الزر
            buzz_lock_key = f"buzz_lock_{self.session_id}"
            cache.delete(buzz_lock_key)
            
            # بث إعادة التعيين لجميع الصفحات
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_buzz_reset'
                }
            )
            
            logger.info(f"Buzzer reset for session: {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error resetting buzzer: {e}")

    async def handle_select_letter(self, data):
        """معالجة اختيار الحرف"""
        letter = data.get('letter')
        try:
            session = await self.get_session()
            package = session.package

            # جلب السؤال من قاعدة البيانات
            question_obj = await self.get_question(package, letter, 'main')

            # بث السؤال لجميع المتصلين
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
        """معالجة تحديث حالة الخلية"""
        letter = data.get('letter')
        state = data.get('state')
        
        if letter and state:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_cell_update',
                    'letter': letter,
                    'state': state
                }
            )

    async def handle_update_scores(self, data):
        """معالجة تحديث النقاط"""
        team1_score = data.get('team1_score', 0)
        team2_score = data.get('team2_score', 0)
        
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'broadcast_score_update',
                'team1_score': team1_score,
                'team2_score': team2_score
            }
        )

    # دوال البث للأنواع المختلفة
    async def broadcast_question(self, event):
        await self.send(text_data=json.dumps({
            'type': 'show_question',
            'letter': event['letter'],
            'question': event['question'],
            'answer': event.get('answer', ''),
            'category': event.get('category', '')
        }))

    async def broadcast_contestant_buzz(self, event):
        """بث ضغطة المتسابق لجميع الصفحات"""
        await self.send(text_data=json.dumps({
            'type': 'show_contestant_buzz',
            'contestant_name': event['contestant_name'],
            'team': event['team'],
            'team_display': event.get('team_display', ''),
            'timestamp': event['timestamp']
        }))

    async def broadcast_buzz_reset(self, event):
        """بث إعادة تعيين الزر"""
        await self.send(text_data=json.dumps({
            'type': 'buzz_reset'
        }))

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

    # دوال مساعدة
    async def get_session(self):
        """جلب الجلسة من قاعدة البيانات"""
        return await sync_to_async(GameSession.objects.get)(id=self.session_id)

    async def get_question(self, package, letter, question_type):
        """جلب السؤال بناءً على الحزمة والحرف والنوع"""
        return await sync_to_async(LettersGameQuestion.objects.get)(
            package=package,
            letter=letter,
            question_type=question_type
        )

    async def register_contestant_if_needed(self, session, contestant_name, team):
        """تسجيل المتسابق إذا لم يكن مسجلاً مسبقاً"""
        try:
            # التحقق من وجود المتسابق
            exists = await sync_to_async(Contestant.objects.filter(
                session=session,
                name=contestant_name
            ).exists)()
            
            if not exists:
                # إنشاء متسابق جديد
                await sync_to_async(Contestant.objects.create)(
                    session=session,
                    name=contestant_name,
                    team=team
                )
                logger.info(f"New contestant registered: {contestant_name} in team {team}")
            
        except Exception as e:
            logger.error(f"Error registering contestant: {e}")

    async def get_team_display_name(self, session, team):
        """الحصول على اسم الفريق للعرض"""
        if team == 'team1':
            return session.team1_name
        elif team == 'team2':
            return session.team2_name
        return 'فريق غير معروف'