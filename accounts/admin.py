from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin

from votecentral.admin_utils import ExportCsvMixin, status_badge
from .models import User


@admin.register(User)
class UserAdmin(ExportCsvMixin, BaseUserAdmin, ModelAdmin):
    ordering = ('email',)
    date_hierarchy = 'date_joined'
    list_display = (
        'email',
        'first_name',
        'last_name',
        'phone_number',
        'organizer_type',
        'sms_opt_in',
        'email_opt_in',
        'staff_badge',
        'active_badge',
        'date_joined',
    )
    list_filter = (
        'organizer_type',
        'sms_opt_in',
        'email_opt_in',
        'marketing_opt_in',
        'is_staff',
        'is_active',
        'date_joined',
    )
    search_fields = ('email', 'first_name', 'last_name', 'phone_number')
    actions = ExportCsvMixin.actions
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (
            'Personal info',
            {
                'fields': (
                    'first_name',
                    'last_name',
                    'phone_number',
                    'avatar',
                    'organizer_type',
                    'referral_source',
                    'sms_opt_in',
                    'email_opt_in',
                    'marketing_opt_in',
                )
            },
        ),
        (
            'Permissions',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                )
            },
        ),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'email',
                    'password1',
                    'password2',
                    'phone_number',
                    'organizer_type',
                    'sms_opt_in',
                    'email_opt_in',
                    'marketing_opt_in',
                    'is_staff',
                    'is_active',
                ),
            },
        ),
    )

    @admin.display(description='Staff', ordering='is_staff')
    def staff_badge(self, obj):
        return status_badge('active' if obj.is_staff else 'draft', 'Staff' if obj.is_staff else 'Organizer')

    @admin.display(description='Active', ordering='is_active')
    def active_badge(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    search_fields = ('name',)
