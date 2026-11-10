import logging
from django.conf import settings
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseServerError,
)
from django.shortcuts import render

logger = logging.getLogger(__name__)


def page_not_found_view(request, exception=None):
    """Custom 404 handler returning minimalist branded page."""
    context = {
        'request': request,
        'status_code': 404,
        'title': 'Page Not Found',
    }
    return render(request, '404.html', context, status=404)


def permission_denied_view(request, exception=None):
    """Custom 403 handler returning minimalist branded page."""
    context = {
        'request': request,
        'status_code': 403,
        'title': 'Access Restricted',
        'exception_message': str(exception) if exception and settings.DEBUG else None,
    }
    return render(request, '403.html', context, status=403)


def bad_request_view(request, exception=None):
    """Custom 400 handler returning minimalist branded page."""
    context = {
        'request': request,
        'status_code': 400,
        'title': 'Invalid Request',
    }
    return render(request, '400.html', context, status=400)


def server_error_view(request):
    """
    Custom 500 handler returning self-contained branded page.
    Does not rely on database queries or context processors to guarantee resilience.
    """
    try:
        return render(request, '500.html', {'status_code': 500}, status=500)
    except Exception as exc:
        logger.exception("Failed to render custom 500 template: %s", exc)
        return HttpResponseServerError(
            "<h1>500 - Server Error</h1><p>Something went wrong on our end. Please try again shortly.</p>",
            content_type="text/html",
        )


def csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure handler replacing Django's default 403 plain box.
    Provides clear, helpful instructions and a 1-click retry.
    """
    logger.warning("CSRF failure at %s: reason=%s", request.path, reason)
    context = {
        'request': request,
        'status_code': 403,
        'title': 'Session Expired',
        'reason': reason if settings.DEBUG else None,
    }
    return render(request, '403_csrf.html', context, status=403)


def error_preview_view(request, code):
    """
    Staff / development preview endpoint to view error templates directly.
    Accessible at /errors/<code: 404, 403, csrf, 500, 400, 429>/
    """
    code_str = str(code).lower()
    if code_str == '404':
        return page_not_found_view(request)
    elif code_str == '403':
        return permission_denied_view(request)
    elif code_str in ('csrf', '403_csrf'):
        return csrf_failure_view(request, reason="Previewing CSRF failure view")
    elif code_str == '500':
        return server_error_view(request)
    elif code_str == '400':
        return bad_request_view(request)
    elif code_str == '429':
        return render(request, '429.html', {'status_code': 429, 'title': 'Too Many Requests'}, status=429)
    else:
        return page_not_found_view(request)

