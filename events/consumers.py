from __future__ import annotations

import logging

import json

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.template.loader import render_to_string

from events.models import Event
from events.performance import build_event_leaderboard

logger = logging.getLogger(__name__)


class LeaderboardConsumer(WebsocketConsumer):
    def connect(self):
        try:
            slug = self.scope['url_route']['kwargs']['slug']
            self.event = Event.objects.filter(
                kind=Event.Kind.PAID_COMPETITION
            ).only('pk', 'slug', 'show_leaderboard').get(slug=slug)
            self.room_group_name = f'leaderboard_{self.event.pk}'
            async_to_sync(self.channel_layer.group_add)(
                self.room_group_name, self.channel_name
            )
            self.accept()
            self._send_leaderboard()
            logger.info('Leaderboard WS connected: slug=%s pk=%s', slug, self.event.pk)
        except Exception as e:
            logger.error('Leaderboard WS connect error: %s', e, exc_info=True)
            self.close()

    def disconnect(self, close_code):
        try:
            async_to_sync(self.channel_layer.group_discard)(
                self.room_group_name, self.channel_name
            )
        except Exception:
            pass

    def leaderboard_updated(self, event):
        try:
            self._send_leaderboard()
        except Exception as e:
            logger.error('Leaderboard WS leaderboard_updated error: %s', e, exc_info=True)

    def _send_leaderboard(self):
        try:
            event = Event.objects.get(pk=self.event.pk)
        except Event.DoesNotExist:
            logger.warning('Leaderboard WS: event pk=%s gone, closing', self.event.pk)
            self.close()
            return
        if event.show_leaderboard:
            leaderboard = build_event_leaderboard(event)
            html = render_to_string('events/_leaderboard.html', {
                'leaderboard': leaderboard,
                'event': event,
            })
        else:
            html = render_to_string('events/_leaderboard.html', {
                'leaderboard': None,
                'event': event,
                'leaderboard_hidden': True,
            })
        self.send(text_data=json.dumps({
            'type': 'leaderboard_update',
            'html': html,
        }))
