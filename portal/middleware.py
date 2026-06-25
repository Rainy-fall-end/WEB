from django.db import DatabaseError

from .models import AccessLog


SKIPPED_PATH_PREFIXES = ("/static/", "/favicon.ico")


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AccessLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        self.log_request(request, response)
        return response

    def log_request(self, request, response):
        if request.path.startswith(SKIPPED_PATH_PREFIXES):
            return

        try:
            AccessLog.objects.create(
                user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
                ip_address=get_client_ip(request),
                method=request.method[:10],
                path=request.path[:500],
                query_string=request.META.get("QUERY_STRING", ""),
                status_code=getattr(response, "status_code", None),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                referer=request.META.get("HTTP_REFERER", ""),
            )
        except (DatabaseError, ValueError):
            return
