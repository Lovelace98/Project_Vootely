from django.contrib import admin

from .models import PaymentAttempt


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = (
        'gateway_reference',
        'event',
        'nominee',
        'status',
        'amount',
        'vote_quantity',
        'initiated_at',
    )
    list_filter = ('status', 'gateway', 'currency')
    search_fields = (
        'gateway_reference',
        'voter_name',
        'voter_email',
        'event__title',
        'nominee__name',
    )
