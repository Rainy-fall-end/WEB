from django.core.management.base import BaseCommand

from portal.models import SearchSource
from portal.services import DEFAULT_SOURCES


class Command(BaseCommand):
    help = "Create or update built-in search sources and enable them."

    def handle(self, *args, **options):
        for source in DEFAULT_SOURCES:
            obj, created = SearchSource.objects.update_or_create(
                slug=source["slug"],
                defaults={
                    "name": source["name"],
                    "base_url": source["base_url"],
                    "adapter_path": source["adapter_path"],
                    "enabled": True,
                },
            )
            action = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f"{action}: {obj.slug} -> {obj.adapter_path}"))

        enabled = SearchSource.objects.filter(enabled=True).order_by("name")
        names = ", ".join(f"{source.name}({source.slug})" for source in enabled)
        self.stdout.write(f"enabled sources: {names or 'none'}")
