import json
from channels.generic.websocket import AsyncWebsocketConsumer

class LettersGameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f"letters_session_{self.session_id}"

        # انضم للمجموعة
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

        # إعادة إرسال نفس البيانات للمجموعة
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'broadcast_update',
                'payload': data
            }
        )

    async def broadcast_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))
