import asyncio
import copy
import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import SearchCache, SearchLog, SearchSource, VipSubscription


CACHE_TTL = timedelta(days=7)


def get_user_plan(user):
    if not user.is_authenticated:
        return {
            "is_vip": False,
            "limit": settings.SEARCH_DEFAULT_LIMIT,
            "can_view_prices": True,
            "label": "Guest",
        }

    try:
        subscription = user.vipsubscription
    except VipSubscription.DoesNotExist:
        return {
            "is_vip": False,
            "limit": settings.SEARCH_DEFAULT_LIMIT,
            "can_view_prices": True,
            "label": "Free",
        }

    if not subscription.is_active:
        return {
            "is_vip": False,
            "limit": settings.SEARCH_DEFAULT_LIMIT,
            "can_view_prices": True,
            "label": "Expired",
        }

    return {
        "is_vip": True,
        "limit": subscription.search_limit_per_query,
        "can_view_prices": subscription.can_view_prices,
        "label": subscription.plan_name,
    }


def build_cache_key(keyword, sources, limit, can_view_prices):
    source_signature = [
        {
            "slug": source.slug,
            "base_url": source.base_url,
            "adapter_path": source.adapter_path,
        }
        for source in sources
    ]
    raw_key = {
        "keyword": keyword.strip().lower(),
        "sources": source_signature,
        "limit": limit,
        "can_view_prices": can_view_prices,
        "version": 3,
    }
    encoded = json.dumps(raw_key, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def attach_runtime_fields(payload, plan, cache_hit, expires_at=None):
    response = copy.deepcopy(payload)
    response["plan"] = plan
    response["cache"] = {
        "hit": cache_hit,
        "ttl_seconds": int(CACHE_TTL.total_seconds()),
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    return response


def search_all_sources(keyword, user=None, requested_limit=None):
    SearchSource.objects.get_or_create(
        slug="tk55tk",
        defaults={
            "name": "TK55TK",
            "base_url": "https://www.tk55tk.com/",
            "adapter_path": "search_tk55tk.search_tk55tk",
        },
    )
    plan = get_user_plan(user)
    limit = min(requested_limit or settings.SEARCH_DEFAULT_LIMIT, plan["limit"])
    enabled_sources = list(SearchSource.objects.filter(enabled=True).order_by("name"))
    cache_key = build_cache_key(keyword, enabled_sources, limit, plan["can_view_prices"])
    cached = SearchCache.objects.filter(cache_key=cache_key, expires_at__gt=timezone.now()).first()
    if cached:
        SearchLog.objects.create(
            user=user if user and user.is_authenticated else None,
            keyword=keyword,
            source=None,
            result_count=cached.payload.get("count", 0),
            status="ok",
        )
        return attach_runtime_fields(cached.payload, plan, True, cached.expires_at)

    combined_results = []
    source_summaries = []
    errors = []

    for source in enabled_sources:
        try:
            adapter = import_string(source.adapter_path)
            payload = asyncio.run(
                adapter(
                    keyword=keyword,
                    url=source.base_url,
                    limit=limit,
                    headless=True,
                    fetch_prices=plan["can_view_prices"],
                    allow_config_price_override=False,
                )
            )
            results = payload.get("results", [])
            for result in results:
                result["source"] = {
                    "name": source.name,
                    "slug": source.slug,
                }
            combined_results.extend(results)
            source_summaries.append(
                {
                    "name": source.name,
                    "slug": source.slug,
                    "count": len(results),
                }
            )
            SearchLog.objects.create(
                user=user if user and user.is_authenticated else None,
                keyword=keyword,
                source=source,
                result_count=len(results),
                status="ok",
            )
        except Exception as exc:
            errors.append({"source": source.slug, "error": str(exc)})
            SearchLog.objects.create(
                user=user if user and user.is_authenticated else None,
                keyword=keyword,
                source=source,
                result_count=0,
                status="error",
                error=str(exc),
            )

    if not combined_results and errors:
        raise RuntimeError("; ".join(f"{item['source']}: {item['error']}" for item in errors))

    combined_results = combined_results[:limit]
    payload = {
        "keyword": keyword,
        "count": len(combined_results),
        "exchange_rate": {
            "rmb": 1,
            "tongbao": 100,
        },
        "sources": source_summaries,
        "source": source_summaries[0] if source_summaries else None,
        "results": combined_results,
        "errors": errors,
    }
    expires_at = timezone.now() + CACHE_TTL
    SearchCache.objects.update_or_create(
        cache_key=cache_key,
        defaults={
            "keyword": keyword,
            "payload": payload,
            "expires_at": expires_at,
        },
    )
    return attach_runtime_fields(payload, plan, False, expires_at)
