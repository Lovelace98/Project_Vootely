from __future__ import annotations

import logging

import json

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.template.loader import render_to_string

from events.models import Event
from events.performance import build_tally_fast

logger = logging.getLogger(__name__)


class ElectionTallyConsumer(WebsocketConsumer):
    def connect(self):
        try:
            slug = self.scope['url_route']['kwargs']['slug']
            self.event = Event.objects.filter(
                kind=Event.Kind.ELECTION
            ).only('pk', 'slug').get(slug=slug)
            self.room_group_name = f'election_tally_{self.event.pk}'
            async_to_sync(self.channel_layer.group_add)(
                self.room_group_name, self.channel_name
            )
            self.accept()
            self._send_tally()
            logger.info('Election WS connected: slug=%s pk=%s', slug, self.event.pk)
        except Exception as e:
            logger.error('Election WS connect error: %s', e, exc_info=True)
            self.close()

    def disconnect(self, close_code):
        try:
            async_to_sync(self.channel_layer.group_discard)(
                self.room_group_name, self.channel_name
            )
        except Exception:
            pass

    def tally_updated(self, event):
        try:
            self._send_tally()
        except Exception as e:
            logger.error('Election WS tally_updated error: %s', e, exc_info=True)

    def _send_tally(self):
        try:
            event = Event.objects.get(pk=self.event.pk)
        except Event.DoesNotExist:
            logger.warning('Election WS: event pk=%s gone, closing', self.event.pk)
            self.close()
            return
        results_visible = event.results_are_public()
        if results_visible:
            tally = build_tally_fast(event)
            html = render_to_string('elections/_tally.html', {
                'tally': tally,
                'event': event,
            })
        else:
            html = render_to_string('elections/_tally.html', {
                'tally': None,
                'event': event,
            })
        self.send(text_data=json.dumps({
            'type': 'tally_update',
            'html': html,
        }))
