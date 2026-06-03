from django.conf import settings
from django.contrib import admin, messages
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)

from votecentral.admin_utils import ExportCsvMixin, status_badge
from .models import InAppNotification, Notification
from .services import dispatch_notification


@admin.register(Notification)
class NotificationAdmin(ExportCsvMixin, ModelAdmin):
    list_display = (
        'event_type',
        'channel',
        'recipient_email',
        'recipient_phone',
        'provider',
        'status_display',
        'queued_at',
        'sent_at',
        'attempt_count',
    )
    list_filter_submit = True
    list_filter = (
        ('event_type', ChoicesDropdownFilter),
        ('channel', ChoicesDropdownFilter),
        ('status', ChoicesDropdownFilter),
        ('attempt_count', RangeNumericFilter),
        ('queued_at', RangeDateTimeFilter),
        ('event', AutocompleteSelectFilter),
        'provider',
    )
    search_fields = (
        'recipient_email',
        'recipient_phone',
        'recipient_name',
        'dedupe_key',
        'subject',
        'provider_status',
        'provider_message_id',
        'provider_error_code',
        'failure_reason',
        'payment_attempt__gateway_reference',
        'withdrawal_request__payout_reference',
        'event__title',
        'event__slug',
    )
    list_select_related = ('event', 'payment_attempt', 'withdrawal_request')
    autocomplete_fields = ('event', 'payment_attempt', 'withdrawal_request')
    date_hierarchy = 'queued_at'
    actions = ExportCsvMixin.actions + ('retry_failed_notifications',)
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

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    @admin.action(description='Retry selected failed notifications')
    def retry_failed_notifications(self, request, queryset):
        retry_limit = settings.NOTIFICATION_RETRY_LIMIT
        retried = 0
        skipped = 0
        for notification in queryset.filter(status=Notification.Status.FAILED):
            if notification.attempt_count >= retry_limit:
                skipped += 1
                continue
            notification.status = Notification.Status.QUEUED
            notification.save(update_fields=['status'])
            dispatch_notification(notification.pk)
            retried += 1
        if retried:
            self.message_user(request, f'{retried} failed notifications queued for retry.', messages.SUCCESS)
        if skipped:
            self.message_user(request, f'{skipped} notifications were at the retry limit.', messages.WARNING)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InAppNotification)
class InAppNotificationAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('title', 'user', 'level_display', 'is_read', 'event', 'payment_attempt', 'withdrawal_request', 'created_at')
    list_filter = (
        ('level', ChoicesDropdownFilter),
        'is_read',
        ('created_at', RangeDateTimeFilter),
        ('user', AutocompleteSelectFilter),
        ('event', AutocompleteSelectFilter),
    )
    search_fields = (
        'title',
        'message',
        'user__email',
        'event__title',
        'event__slug',
        'nominee__name',
        'payment_attempt__gateway_reference',
        'withdrawal_request__payout_reference',
    )
    list_select_related = ('user', 'event', 'nominee', 'payment_attempt', 'withdrawal_request')
    autocomplete_fields = ('user', 'event', 'nominee', 'payment_attempt', 'withdrawal_request')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)
    actions = ExportCsvMixin.actions

    @admin.display(description='Level', ordering='level')
    def level_display(self, obj):
        return status_badge(obj.level, obj.get_level_display())
