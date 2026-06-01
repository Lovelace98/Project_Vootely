from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'event_type',
        'channel',
        'recipient_email',
        'recipient_phone',
        'provider',
        'status',
        'queued_at',
        'sent_at',
        'attempt_count',
    )
    list_filter = ('event_type', 'channel', 'provider', 'status')
    search_fields = (
        'recipient_email',
        'recipient_phone',
        'recipient_name',
        'dedupe_key',
        'payment_attempt__gateway_reference',
        'withdrawal_request__payout_reference',
        'event__title',
    )
    readonly_fields = (
        'channel',
        'event_type',
        'status',
        'recipient_email',
        'recipient_phone',
        'recipient_name',
        'event',
        'payment_attempt',
        'withdrawal_request',
        'subject',
        'body_text',
        'body_html',
        'dedupe_key',
        'provider',
        'provider_status',
        'provider_payload',
        'provider_error_code',
        'provider_message_id',
        'failure_reason',
        'attempt_count',
        'queued_at',
        'sent_at',
        'last_attempt_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
