from django.contrib import admin

from .models import BomLine, ManufacturingOrder, Product, StockMovement


class BomLineInline(admin.TabularInline):
    model = BomLine
    fk_name = "product"
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "stock", "is_active"]
    list_filter = ["kind", "is_active"]
    inlines = [BomLineInline]


@admin.register(ManufacturingOrder)
class ManufacturingOrderAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "quantity", "status", "created_at"]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["created_at", "product", "change", "reason"]
    list_filter = ["reason", "product"]
