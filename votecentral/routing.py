from django.urls import path

from events.consumers import LeaderboardConsumer
from elections.consumers import ElectionTallyConsumer

websocket_urlpatterns = [
    path('ws/events/<slug:slug>/leaderboard/', LeaderboardConsumer.as_asgi()),
    path('ws/elections/<slug:slug>/tally/', ElectionTallyConsumer.as_asgi()),
]
