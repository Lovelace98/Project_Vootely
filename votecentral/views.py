import logging
import sys
from django.conf import settings
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseServerError,
)
from django.shortcuts import render

logger = logging.getLogger('votecentral.errors')


def _get_request_metadata(request):
    """
    Safely extract request diagnostics for error logging without triggering 
    accidental exceptions or unhandled attribute lookups.
    """
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '') if hasattr(request, 'META') else ''
    if forwarded_for:
        client_ip = forwarded_for.split(',')[0].strip()
    elif hasattr(request, 'META'):
        client_ip = request.META.get('REMOTE_ADDR', 'unknown')
    else:
        client_ip = 'unknown'

    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False):
        user_info = f"id={user.pk} username={getattr(user, 'username', 'unknown')}"
    else:
        user_info = "anonymous"

    meta = getattr(request, 'META', {})
    return {
        'ip': client_ip,
        'user': user_info,
        'method': getattr(request, 'method', 'UNKNOWN'),
        'path': getattr(request, 'path', 'unknown'),
        'referer': meta.get('HTTP_REFERER', 'none'),
        'user_agent': meta.get('HTTP_USER_AGENT', 'unknown'),
        'origin': meta.get('HTTP_ORIGIN', 'none'),
        'host': request.get_host() if hasattr(request, 'get_host') else meta.get('HTTP_HOST', 'unknown'),
    }


def page_not_found_view(request, exception=None):
    """Custom 404 handler returning minimalist branded page and logging error details."""
    meta = _get_request_metadata(request)
    reason = str(exception) if exception else "Route does not exist"
    logger.warning(
        "HTTP 404 Not Found: %s [%s] from %s (user: %s) | referer: %s | error: %s",
        meta['path'],
        meta['method'],
        meta['ip'],
        meta['user'],
        meta['referer'],
        reason,
    )
    context = {
        'request': request,
        'status_code': 404,
        'title': 'Page Not Found',
    }
    return render(request, '404.html', context, status=404)


def permission_denied_view(request, exception=None):
    """Custom 403 handler returning minimalist branded page and logging error details."""
    meta = _get_request_metadata(request)
    reason = str(exception) if exception else "Permission denied"
    logger.warning(
        "HTTP 403 Forbidden: %s [%s] from %s (user: %s) | referer: %s | reason: %s",
        meta['path'],
        meta['method'],
        meta['ip'],
        meta['user'],
        meta['referer'],
        reason,
    )
    context = {
        'request': request,
        'status_code': 403,
        'title': 'Access Restricted',
        'exception_message': reason if settings.DEBUG else None,
    }
    return render(request, '403.html', context, status=403)


def bad_request_view(request, exception=None):
    """Custom 400 handler returning minimalist branded page and logging error details."""
    meta = _get_request_metadata(request)
    reason = str(exception) if exception else "Malformed or invalid request syntax"
    logger.warning(
        "HTTP 400 Bad Request: %s [%s] from %s (user: %s) | referer: %s | error: %s",
        meta['path'],
        meta['method'],
        meta['ip'],
        meta['user'],
        meta['referer'],
        reason,
    )
    context = {
        'request': request,
        'status_code': 400,
        'title': 'Invalid Request',
    }
    return render(request, '400.html', context, status=400)


def server_error_view(request):
    """
    Custom 500 handler returning self-contained branded page.
    Captures sys.exc_info() and logs full exception and traceback to server logs.
    Does not rely on database queries or context processors to guarantee resilience.
    """
    meta = _get_request_metadata(request)
    exc_type, exc_value, exc_traceback = sys.exc_info()

    if exc_type is not None:
        logger.error(
            "HTTP 500 Internal Server Error: %s [%s] from %s (user: %s) | referer: %s | UA: %s | error: %s: %s",
            meta['path'],
            meta['method'],
            meta['ip'],
            meta['user'],
            meta['referer'],
            meta['user_agent'],
            exc_type.__name__,
            exc_value,
            exc_info=(exc_type, exc_value, exc_traceback),
        )
    else:
        logger.error(
            "HTTP 500 Internal Server Error (no active exception in sys.exc_info): %s [%s] from %s (user: %s) | referer: %s | UA: %s",
            meta['path'],
            meta['method'],
            meta['ip'],
            meta['user'],
            meta['referer'],
            meta['user_agent'],
        )

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
    Logs comprehensive request, session, and cookie diagnostic details to server logs.
    Provides clear, helpful instructions and a 1-click retry.
    """
    meta = _get_request_metadata(request)
    csrf_cookie_name = getattr(settings, 'CSRF_COOKIE_NAME', 'csrftoken')
    session_cookie_name = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')
    cookies = getattr(request, 'COOKIES', {})
    has_csrf_cookie = csrf_cookie_name in cookies
    has_session_cookie = session_cookie_name in cookies

    logger.warning(
        "HTTP 403 CSRF Failure: %s [%s] from %s (user: %s) | "
        "reason: %s | origin: %s | host: %s | referer: %s | "
        "cookies_present: {csrf: %s, session: %s} | UA: %s",
        meta['path'],
        meta['method'],
        meta['ip'],
        meta['user'],
        reason or "Token missing or incorrect",
        meta['origin'],
        meta['host'],
        meta['referer'],
        has_csrf_cookie,
        has_session_cookie,
        meta['user_agent'],
    )
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
        meta = _get_request_metadata(request)
        logger.warning(
            "HTTP 429 Rate Limit rendered: %s [%s] from %s (user: %s)",
            meta['path'],
            meta['method'],
            meta['ip'],
            meta['user'],
        )
        return render(request, '429.html', {'status_code': 429, 'title': 'Too Many Requests'}, status=429)
    else:
        return page_not_found_view(request)

