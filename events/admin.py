from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'owner',
        'kind',
        'status',
        'vote_price',
        'currency',
        'start_at',
        'end_at',
    )
    list_filter = ('kind', 'status', 'currency', 'is_public')
    search_fields = ('title', 'owner__email', 'slug')
    prepopulated_fields = {'slug': ('title',)}
