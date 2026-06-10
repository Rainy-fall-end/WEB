from django.contrib import admin

from .models import SearchCache, SearchLog, SearchSource, VipSubscription


@admin.register(SearchSource)
class SearchSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "base_url", "enabled", "adapter_path")
    list_filter = ("enabled",)
    search_fields = ("name", "slug", "base_url")


@admin.register(VipSubscription)
class VipSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_name", "active_until", "search_limit_per_query", "can_view_prices", "is_active")
    list_filter = ("plan_name", "can_view_prices")
    search_fields = ("user__username", "user__email")


@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display = ("keyword", "user", "source", "result_count", "status", "created_at")
    list_filter = ("status", "source", "created_at")
    search_fields = ("keyword", "user__username", "error")
    readonly_fields = ("created_at",)


@admin.register(SearchCache)
class SearchCacheAdmin(admin.ModelAdmin):
    list_display = ("keyword", "cache_key", "expires_at", "created_at", "updated_at", "is_expired")
    list_filter = ("expires_at", "created_at")
    search_fields = ("keyword", "cache_key")
    readonly_fields = ("cache_key", "payload", "created_at", "updated_at")
