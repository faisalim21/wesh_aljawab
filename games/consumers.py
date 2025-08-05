import json
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.exceptions import ObjectDoesNotExist
from games.models import GameSession, LettersGameQuestion
from asgiref.sync import sync_to_async


class LettersGameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type')

        if message_type == "select_letter":
            letter = data.get('letter')
            try:
                session = await self.get_session()
                package = session.package

                # جلب السؤال من قاعدة البيانات
                question_obj = await self.get_question(package, letter, 'main')

                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'broadcast_question',
                        'letter': letter,
                        'question': question_obj.question
                    }
                )
            except Exception as e:
                await self.send(text_data=json.dumps({'type': 'error', 'message': str(e)}))

        elif message_type == "buzz":
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_buzz',
                    'name': data.get('name')
                }
            )

        else:
            # أي رسالة أخرى تُعاد كما هي
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'broadcast_update',
                    'payload': data
                }
            )

    async def broadcast_question(self, event):
        await self.send(text_data=json.dumps({
            'type': 'broadcast_question',
            'letter': event['letter'],
            'question': event['question']
        }))

    async def broadcast_buzz(self, event):
        await self.send(text_data=json.dumps({
            'type': 'buzz',
            'name': event['name']
        }))

    async def broadcast_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    # جلب الجلسة من الـ database
    async def get_session(self):
        return await sync_to_async(GameSession.objects.get)(id=self.session_id)

    # جلب السؤال بناءً على الحزمة والحرف والنوع
    async def get_question(self, package, letter, question_type):
        return await sync_to_async(LettersGameQuestion.objects.get)(
            package=package,
            letter=letter,
            question_type=question_type
        )
