from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import AutocompleteSelectFilter

from votecentral.admin_utils import ExportCsvMixin, status_badge
from .models import CompetitionCategory, NominationSubmission, Nominee


@admin.register(CompetitionCategory)
class CompetitionCategoryAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'event', 'display_order', 'active_display', 'created_at')
    list_filter = ('is_active', ('event', AutocompleteSelectFilter))
    search_fields = ('name', 'slug', 'event__title', 'event__slug')
    list_select_related = ('event',)
    autocomplete_fields = ('event',)
    actions = ExportCsvMixin.actions

    @admin.display(description='Active', ordering='is_active')
    def active_display(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


@admin.register(Nominee)
class NomineeAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'event', 'category', 'code', 'display_order', 'active_display', 'created_at')
    list_filter = ('is_active', ('event', AutocompleteSelectFilter), ('category', AutocompleteSelectFilter))
    search_fields = ('name', 'code', 'email', 'phone_number', 'event__title', 'event__slug', 'category__name')
    list_select_related = ('event', 'category')
    autocomplete_fields = ('event', 'category')
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('name',)}
    actions = ExportCsvMixin.actions

    @admin.display(description='Active', ordering='is_active')
    def active_display(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


@admin.register(NominationSubmission)
class NominationSubmissionAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'event', 'category', 'status_display', 'email', 'phone_number', 'created_at')
    list_filter = ('status', ('event', AutocompleteSelectFilter), ('category', AutocompleteSelectFilter))
    search_fields = ('name', 'email', 'phone_number', 'event__title', 'category__name')
    list_select_related = ('event', 'category', 'approved_nominee')
    autocomplete_fields = ('event', 'category', 'approved_nominee')
    actions = ExportCsvMixin.actions

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())
