from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView

from votecentral.mixins import SafeIntegrityMixin

from .forms import NotificationSettingsForm, UserProfileForm


class DashboardProfileView(LoginRequiredMixin, View):
    template_name = 'dashboard/profile.html'

    def get(self, request, *args, **kwargs):
        profile_form = UserProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)
        notification_form = NotificationSettingsForm(instance=request.user)
        active_tab = request.GET.get('tab', 'profile')
        
        context = {
            'profile_form': profile_form,
            'password_form': password_form,
            'notification_form': notification_form,
            'active_tab': active_tab,
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        active_tab = 'profile'
        
        profile_form = UserProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)
        notification_form = NotificationSettingsForm(instance=request.user)
        
        if action == 'profile':
            profile_form = UserProfileForm(request.POST, request.FILES, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Your profile has been updated successfully.')
                return redirect('dashboard:profile')
            active_tab = 'profile'
            
        elif action == 'password':
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password has been changed successfully.')
                return redirect('dashboard:profile')
            active_tab = 'security'
            
        elif action == 'notifications':
            notification_form = NotificationSettingsForm(request.POST, instance=request.user)
            if notification_form.is_valid():
                notification_form.save()
                messages.success(request, 'Your notification settings have been updated.')
                return redirect('dashboard:profile')
            active_tab = 'notifications'
            
        context = {
            'profile_form': profile_form,
            'password_form': password_form,
            'notification_form': notification_form,
            'active_tab': active_tab,
        }
        return render(request, self.template_name, context)


class DashboardNotificationSettingsView(SafeIntegrityMixin, LoginRequiredMixin, UpdateView):
    form_class = NotificationSettingsForm
    template_name = 'dashboard/notification_settings.html'
    success_url = reverse_lazy('dashboard:notification_settings')
    success_message = 'Notification settings updated.'

    def get_object(self, queryset=None):
        return self.request.user


from notifications.models import InAppNotification
from django.core.paginator import Paginator
from events.performance import bump_notification_cache

class DashboardNotificationsView(LoginRequiredMixin, View):
    template_name = 'dashboard/notifications.html'

    def get(self, request, *args, **kwargs):
        notifications_list = InAppNotification.objects.filter(user=request.user)
        paginator = Paginator(notifications_list, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        # Mark displayed notifications as read on view
        unread_on_page = [n for n in page_obj if not n.is_read]
        if unread_on_page:
            InAppNotification.objects.filter(id__in=[n.id for n in unread_on_page]).update(is_read=True)
            bump_notification_cache(request.user.pk)
        
        # Recompute unread count so badge is accurate on this same page load
        refreshed_unread = InAppNotification.objects.filter(user=request.user, is_read=False).count()
            
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'unread_notifications_count': refreshed_unread,
        })


class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        InAppNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        bump_notification_cache(request.user.pk)
        messages.success(request, 'All notifications marked as read.')
        return redirect('dashboard:notifications')


class MarkNotificationReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        notification = get_object_or_404(InAppNotification, pk=kwargs['pk'], user=request.user)
        notification.is_read = True
        notification.save()
        
        # Recompute unread count
        count = InAppNotification.objects.filter(user=request.user, is_read=False).count()
        
        level_class = "bg-vc-blue"
        if notification.level == "success":
            level_class = "bg-green-50 text-green-500 dark:bg-green-950/30 dark:text-green-400"
        elif notification.level == "warning":
            level_class = "bg-amber-50 text-amber-500 dark:bg-amber-950/30 dark:text-amber-400"
        elif notification.level == "danger":
            level_class = "bg-red-50 text-red-500 dark:bg-red-950/30 dark:text-red-400"
        else:
            level_class = "bg-vc-blue-50 text-vc-blue dark:bg-vc-blue-950/30 dark:text-vc-blue-400"
            
        icon_svg = ""
        if notification.level == "success":
            icon_svg = '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
        elif notification.level == "warning":
            icon_svg = '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>'
        elif notification.level == "danger":
            icon_svg = '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
        else:
            icon_svg = '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
            
        badge_html = ""
        if count > 0:
            badge_html = f'<span class="flex h-4 w-4 items-center justify-center rounded-full bg-vc-orange text-[9px] font-extrabold text-white">{count}</span>'
            
        dropdown_header_badge_html = ""
        if count > 0:
            dropdown_header_badge_html = f'<span class="text-[11px] font-bold px-2 py-0.5 rounded-full bg-vc-orange/10 text-vc-orange">{count} new</span>'
            
        response_html = f'''
        <div id="dropdown-notif-{notification.id}" class="py-3 flex gap-3 text-left relative group transition-all duration-300">
            <div class="flex-shrink-0 mt-0.5">
                <span class="flex h-7 w-7 items-center justify-center rounded-full {level_class}">
                    {icon_svg}
                </span>
            </div>
            <div class="flex-1 min-w-0">
                <p class="text-xs font-bold text-vc-dark dark:text-white truncate">
                    {notification.title}
                </p>
                <p class="text-[11px] text-vc-dark-400 dark:text-slate-400 mt-0.5 line-clamp-2">
                    {notification.message}
                </p>
                <p class="text-[9px] text-vc-dark-300 dark:text-slate-500 mt-1 font-medium">
                    Just now (read)
                </p>
            </div>
        </div>
        
        <span id="header-bell-badge" hx-swap-oob="true">
            {badge_html}
        </span>
        <span id="dropdown-header-badge" hx-swap-oob="true">
            {dropdown_header_badge_html}
        </span>
        '''
        return HttpResponse(response_html)
