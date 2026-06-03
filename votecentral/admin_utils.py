import csv
import json

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html


def serialize_admin_value(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def csv_export_response(model, queryset):
    opts = model._meta
    filename = f'{opts.app_label}_{opts.model_name}_{timezone.now():%Y%m%d%H%M%S}.csv'
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    fields = opts.concrete_fields
    headers = []
    for field in fields:
        headers.append(field.name)
        if getattr(field, 'remote_field', None) and field.attname != field.name:
            headers.append(field.attname)

    writer = csv.writer(response)
    writer.writerow(headers)
    for obj in queryset.iterator():
        row = []
        for field in fields:
            value = field.value_from_object(obj)
            if getattr(field, 'remote_field', None):
                related = getattr(obj, field.name, None)
                row.append(serialize_admin_value(related))
                if field.attname != field.name:
                    row.append(serialize_admin_value(value))
            else:
                row.append(serialize_admin_value(value))
        writer.writerow(row)
    return response


@admin.action(description='Export selected rows as CSV')
def export_selected_as_csv(modeladmin, request, queryset):
    if not request.user.is_staff:
        raise PermissionDenied
    return csv_export_response(modeladmin.model, queryset)


class ExportCsvMixin:
    change_list_template = 'admin/export_change_list.html'
    actions = (export_selected_as_csv,)

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        export_url = path(
            'export/',
            self.admin_site.admin_view(self.export_changelist_view),
            name=f'{opts.app_label}_{opts.model_name}_export',
        )
        return [export_url] + urls

    def export_changelist_view(self, request):
        if not request.user.is_staff or not self.has_view_or_change_permission(request):
            raise PermissionDenied
        changelist = self.get_changelist_instance(request)
        return csv_export_response(self.model, changelist.get_queryset(request))


class ReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(
            f'{self.opts.app_label}.view_{self.opts.model_name}'
        )

    def has_delete_permission(self, request, obj=None):
        return False


def status_badge(value, label=None):
    variants = {
        'active': ('#dcfce7', '#166534'),
        'approved': ('#dcfce7', '#166534'),
        'certified': ('#dcfce7', '#166534'),
        'completed': ('#dcfce7', '#166534'),
        'eligible': ('#dcfce7', '#166534'),
        'open': ('#dcfce7', '#166534'),
        'paid': ('#dcfce7', '#166534'),
        'published': ('#dcfce7', '#166534'),
        'sent': ('#dcfce7', '#166534'),
        'success': ('#dcfce7', '#166534'),
        'closed': ('#e0f2fe', '#075985'),
        'tallied': ('#e0f2fe', '#075985'),
        'processing': ('#dbeafe', '#1d4ed8'),
        'queued': ('#fef9c3', '#854d0e'),
        'pending': ('#fef9c3', '#854d0e'),
        'payment_pending': ('#fef9c3', '#854d0e'),
        'initialized': ('#fef9c3', '#854d0e'),
        'draft': ('#f1f5f9', '#475569'),
        'configured': ('#f1f5f9', '#475569'),
        'failed': ('#fee2e2', '#991b1b'),
        'cancelled': ('#fee2e2', '#991b1b'),
        'rejected': ('#fee2e2', '#991b1b'),
        'revoked': ('#fee2e2', '#991b1b'),
        'danger': ('#fee2e2', '#991b1b'),
    }
    background, color = variants.get(str(value), ('#f1f5f9', '#334155'))
    return format_html(
        '<span style="display:inline-flex;align-items:center;border-radius:999px;'
        'background:{};color:{};padding:2px 8px;font-size:12px;font-weight:600;">{}</span>',
        background,
        color,
        label or str(value).replace('_', ' ').title(),
    )


def run_guarded_action(modeladmin, request, queryset, callback, success_label):
    completed = 0
    errors = []
    for obj in queryset:
        try:
            callback(obj)
        except ValidationError as exc:
            errors.extend(exc.messages)
        except Exception as exc:
            errors.append(str(exc))
        else:
            completed += 1

    if completed:
        modeladmin.message_user(
            request,
            f'{completed} {success_label}.',
            messages.SUCCESS,
        )
    for error in errors[:5]:
        modeladmin.message_user(request, error, messages.ERROR)
    if len(errors) > 5:
        modeladmin.message_user(
            request,
            f'{len(errors) - 5} additional errors were omitted.',
            messages.WARNING,
        )
