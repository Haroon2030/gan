from django.conf import settings
from django.shortcuts import resolve_url
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponseRedirect, JsonResponse


class SafeSessionMiddleware(SessionMiddleware):
    """يحوّل انقطاع الجلسة أثناء الطلب (دخول/خروج متزامن) إلى ردّ واضح بدل خطأ 400.

    يحدث حين تُحذف الجلسة بتسجيل دخول أو خروج بينما طلبات الخلفية ما زالت تعمل.
    """

    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            wants_json = (
                'application/json' in request.headers.get('Accept', '')
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            )
            if wants_json:
                return JsonResponse(
                    {'ok': False, 'error': 'انتهت الجلسة؛ أعد تحميل الصفحة.'},
                    status=401,
                )
            return HttpResponseRedirect(resolve_url(settings.LOGIN_URL))
