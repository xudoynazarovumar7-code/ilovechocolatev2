from django.core.management.base import BaseCommand

from factory.models import BomLine, Product


class Command(BaseCommand):
    help = "Create demo data: Cocoa, Sugar, Dark Chocolate and its recipe."

    def handle(self, *args, **options):
        cocoa, _ = Product.objects.get_or_create(name="Cocoa", defaults={"kind": "ingredient", "stock": 5000})
        sugar, _ = Product.objects.get_or_create(name="Sugar", defaults={"kind": "ingredient", "stock": 2000})
        dark, _ = Product.objects.get_or_create(name="Dark Chocolate", defaults={"kind": "finished", "stock": 0})
        BomLine.objects.get_or_create(product=dark, ingredient=cocoa, defaults={"quantity": 50})
        BomLine.objects.get_or_create(product=dark, ingredient=sugar, defaults={"quantity": 20})
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
