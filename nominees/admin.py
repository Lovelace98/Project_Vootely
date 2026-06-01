from django.contrib import admin

from .models import Nominee


@admin.register(Nominee)
class NomineeAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'code', 'display_order', 'is_active')
    list_filter = ('is_active', 'event')
    search_fields = ('name', 'code', 'event__title')
