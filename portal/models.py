from django.conf import settings
from django.db import models
from django.utils import timezone


class SearchSource(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    base_url = models.URLField()
    enabled = models.BooleanField(default=True)
    adapter_path = models.CharField(
        max_length=160,
        default="search_tk55tk.search_tk55tk",
        help_text="Python import path used by the search dispatcher.",
    )

    def __str__(self):
        return self.name


class VipSubscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    plan_name = models.CharField(max_length=80, default="VIP")
    active_until = models.DateTimeField(null=True, blank=True)
    search_limit_per_query = models.PositiveIntegerField(default=10)
    can_view_prices = models.BooleanField(default=True)

    @property
    def is_active(self):
        return self.active_until is None or self.active_until >= timezone.now()

    def __str__(self):
        status = "active" if self.is_active else "expired"
        return f"{self.user} - {self.plan_name} ({status})"


class SearchLog(models.Model):
    STATUS_CHOICES = [
        ("ok", "OK"),
        ("error", "Error"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    keyword = models.CharField(max_length=200)
    source = models.ForeignKey(SearchSource, null=True, blank=True, on_delete=models.SET_NULL)
    result_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ok")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.keyword} ({self.status})"


class SearchCache(models.Model):
    cache_key = models.CharField(max_length=64, unique=True)
    keyword = models.CharField(max_length=200)
    payload = models.JSONField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def __str__(self):
        return f"{self.keyword} expires at {self.expires_at:%Y-%m-%d %H:%M:%S}"


class AccessLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    query_string = models.TextField(blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    referer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["ip_address"]),
        ]

    def __str__(self):
        return f"{self.method} {self.path} from {self.ip_address or 'unknown'}"
