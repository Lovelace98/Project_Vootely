from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Ticket, TicketCheckIn, TicketProvisionalEntry, TicketPurchase, TicketScannerPass, TicketType


@admin.register(TicketType)
class TicketTypeAdmin(ModelAdmin):
    list_display = ('name', 'event', 'price', 'quantity_available', 'quantity_sold_display', 'is_active')
    list_filter = ('is_active', 'event')
    search_fields = ('name', 'event__title')
    list_select_related = ('event',)

    @admin.display(description='Sold')
    def quantity_sold_display(self, obj):
        return obj.quantity_sold


@admin.register(TicketPurchase)
class TicketPurchaseAdmin(ModelAdmin):
    list_display = ('gateway_reference', 'event', 'ticket_type', 'buyer_email', 'quantity', 'amount', 'status', 'initiated_at')
    list_filter = ('status', 'gateway', 'currency', 'event')
    search_fields = ('gateway_reference', 'buyer_name', 'buyer_email', 'buyer_phone', 'event__title')
    list_select_related = ('event', 'ticket_type')
    readonly_fields = ('gateway_reference', 'gateway_response', 'webhook_payload')


@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    list_display = ('code', 'event', 'ticket_type', 'status', 'used_at', 'checked_in_by')
    list_filter = ('status', 'event', 'ticket_type')
    search_fields = ('code', 'purchase__gateway_reference', 'purchase__buyer_email', 'event__title')
    list_select_related = ('event', 'ticket_type', 'purchase', 'checked_in_by')


@admin.register(TicketCheckIn)
class TicketCheckInAdmin(ModelAdmin):
    list_display = ('ticket', 'event', 'status_before', 'status_after', 'checked_in_by', 'scanner_gate_name', 'scanned_at')
    list_filter = ('status_before', 'status_after', 'event', 'scanner_gate_name')
    search_fields = ('ticket__code', 'event__title', 'scanner_gate_name', 'scanner_staff_label', 'message')
    list_select_related = ('ticket', 'event', 'checked_in_by', 'scanner_pass')


@admin.register(TicketScannerPass)
class TicketScannerPassAdmin(ModelAdmin):
    list_display = ('gate_name', 'staff_label', 'event', 'status', 'allow_provisional_entry', 'expires_at', 'activated_at', 'revoked_at')
    list_filter = ('status', 'allow_provisional_entry', 'event', 'gate_name')
    search_fields = ('gate_name', 'staff_label', 'event__title', 'token')
    list_select_related = ('event', 'created_by')
    readonly_fields = ('token', 'device_session_key', 'device_user_agent', 'device_ip', 'activated_at', 'created_at', 'updated_at')


@admin.register(TicketProvisionalEntry)
class TicketProvisionalEntryAdmin(ModelAdmin):
    list_display = ('ticket_code', 'event', 'status', 'result', 'gate_name', 'staff_label', 'synced_at', 'created_at')
    list_filter = ('status', 'result', 'event', 'gate_name')
    search_fields = ('ticket_code', 'client_attempt_id', 'event__title', 'gate_name', 'staff_label', 'message')
    list_select_related = ('event', 'ticket', 'scanner_pass', 'checked_in_by', 'final_checkin')
    readonly_fields = ('client_attempt_id', 'cached_ticket_snapshot', 'created_at', 'updated_at')
