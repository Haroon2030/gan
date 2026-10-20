"""معالج فشل CSRF — HTML لتسجيل الدخول، JSON لطلبات API فقط."""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render


logger = logging.getLogger(__name__)


def _wants_json(request) -> bool:
    """لا تعامل كل POST كـ JSON — وإلا يظهر JSON خام عند فشل دخول النموذج."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    path = (request.path or '').lower()
    if '/api/' in path:
        return True
    accept = (request.headers.get('Accept') or '').lower()
    if 'application/json' in accept and 'text/html' not in accept:
        return True
    content_type = (request.headers.get('Content-Type') or '').lower()
    if 'application/json' in content_type:
        return True
    return False


def csrf_failure(request, reason=''):
    logger.warning(
        'CSRF رُفض: reason=%s path=%s origin=%s host=%s cookie=%s',
        reason,
        request.path,
        request.headers.get('Origin'),
        request.get_host(),
        'csrf-cookie' if request.COOKIES.get(settings.CSRF_COOKIE_NAME) else 'no-cookie',
    )
    message = 'انتهت صلاحية الجلسة أو رمز الحماية. حدّث الصفحة ثم أعد المحاولة.'
    # جدّد رمز CSRF في الاستجابة حتى يعمل النموذج فوراً دون تحديث يدوي
    get_token(request)

    path = (request.path or '').rstrip('/').lower()
    is_login = path.endswith('login')
    is_api = '/api/' in path

    if is_api or (_wants_json(request) and not is_login):
        return JsonResponse({'ok': False, 'error': message}, status=403)

    try:
        return render(
            request,
            'registration/login.html',
            {
                'error': message,
                'next': request.POST.get('next') or request.GET.get('next') or '',
            },
            status=403,
        )
    except Exception:
        return HttpResponseForbidden(message, content_type='text/plain; charset=utf-8')
