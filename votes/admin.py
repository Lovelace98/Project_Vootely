from django.contrib import admin

from .models import VotePurchase


@admin.register(VotePurchase)
class VotePurchaseAdmin(admin.ModelAdmin):
    list_display = (
        'payment_reference',
        'event',
        'nominee',
        'quantity',
        'amount_paid',
        'paid_at',
    )
    list_filter = ('currency', 'event')
    search_fields = (
        'payment_reference',
        'voter_name',
        'voter_email',
        'nominee__name',
        'event__title',
    )
