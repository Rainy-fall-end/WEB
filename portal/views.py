import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .services import get_user_plan, search_all_sources


def home(request):
    keyword = request.GET.get("q", "").strip()
    payload = None
    error = ""

    if keyword:
        try:
            payload = search_all_sources(keyword, request.user)
        except Exception as exc:
            error = str(exc)

    return render(
        request,
        "portal/index.html",
        {
            "keyword": keyword,
            "payload": payload,
            "payload_json": json.dumps(payload, ensure_ascii=False, indent=2) if payload else "",
            "error": error,
            "plan": get_user_plan(request.user),
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
