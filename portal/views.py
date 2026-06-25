from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import AccessLog
from .services import get_user_plan, search_all_sources


def get_access_stats():
    return {
        "total_visits": AccessLog.objects.count(),
        "unique_visitors": AccessLog.objects.exclude(ip_address__isnull=True).values("ip_address").distinct().count(),
    }


def home(request):
    keyword = request.GET.get("q", "").strip()

    return render(
        request,
        "portal/index.html",
        {
            "keyword": keyword,
            "plan": get_user_plan(request.user),
            "access_stats": get_access_stats(),
        },
    )


@require_GET
def search_api(request):
    keyword = request.GET.get("q", "").strip()
    if not keyword:
        return JsonResponse({"error": "Missing query parameter: q"}, status=400, json_dumps_params={"ensure_ascii": False})

    try:
        payload = search_all_sources(keyword, request.user)
        return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})
    except Exception as exc:
        return JsonResponse(
            {"keyword": keyword, "error": str(exc), "results": [], "count": 0},
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )
