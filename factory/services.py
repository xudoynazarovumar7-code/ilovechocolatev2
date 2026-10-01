"""Business rules live here, separate from the web layer (views)."""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import BomLine, ManufacturingOrder, Product, StockMovement


class ProductionError(Exception):
    """Base class for problems that stop an order from being produced."""


class InsufficientStockError(ProductionError):
    def __init__(self, shortages):
        # shortages: list of (ingredient_name, needed, available)
        self.shortages = shortages
        details = "; ".join(
            f"{name}: need {needed}, have {available}" for name, needed, available in shortages
        )
        super().__init__(f"Not enough stock. {details}")


class NoRecipeError(ProductionError):
    pass


class OrderAlreadyDoneError(ProductionError):
    pass


class InvalidRestockError(Exception):
    pass


@transaction.atomic
def produce(order_id):
    """Produce a manufacturing order. All or nothing.

    Inside one transaction we:
      1. lock the order and the ingredient rows (so two people can't use the same sugar),
      2. check every ingredient is available,
      3. subtract ingredients, add finished goods, write the stock log, mark the order done.

    If anything raises, the transaction rolls back and no stock changes.
    """
    order = ManufacturingOrder.objects.select_for_update().select_related("product").get(pk=order_id)

    if order.status == ManufacturingOrder.Status.DONE:
        raise OrderAlreadyDoneError("This order has already been produced.")

    bom_lines = list(order.product.bom_lines.select_related("ingredient"))
    if not bom_lines:
        raise NoRecipeError(f"'{order.product}' has no Bill of Materials yet.")

    # Lock ingredient rows in a fixed order to avoid deadlocks.
    ingredient_ids = sorted(line.ingredient_id for line in bom_lines)
    locked = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=ingredient_ids)}

    shortages = []
    for line in bom_lines:
        needed = line.quantity * order.quantity
        available = locked[line.ingredient_id].stock
        if available < needed:
            shortages.append((line.ingredient.name, needed, available))
    if shortages:
        raise InsufficientStockError(shortages)

    for line in bom_lines:
        used = line.quantity * order.quantity
        Product.objects.filter(pk=line.ingredient_id).update(stock=F("stock") - used)
        StockMovement.objects.create(
            product=line.ingredient, change=-used, reason=StockMovement.Reason.CONSUMED, order=order
        )
    Product.objects.filter(pk=order.product_id).update(stock=F("stock") + order.quantity)
    StockMovement.objects.create(
        product=order.product, change=order.quantity, reason=StockMovement.Reason.PRODUCED, order=order
    )

    order.status = ManufacturingOrder.Status.DONE
    order.produced_at = timezone.now()
    order.save(update_fields=["status", "produced_at"])
    return order


@transaction.atomic
def restock(product_id, amount):
    """Add `amount` to a product's stock and record it in the stock log."""
    if amount < 1:
        raise InvalidRestockError("Amount must be at least 1.")
    product = Product.objects.select_for_update().get(pk=product_id)
    Product.objects.filter(pk=product.pk).update(stock=F("stock") + amount)
    StockMovement.objects.create(
        product=product, change=amount, reason=StockMovement.Reason.RESTOCK
    )
    product.refresh_from_db()
    return product


@transaction.atomic
def create_product(name, kind, stock=0):
    """Create a product. Any starting stock is written to the stock log too."""
    product = Product.objects.create(name=name, kind=kind, stock=stock)
    if stock > 0:
        StockMovement.objects.create(product=product, change=stock, reason=StockMovement.Reason.RESTOCK)
    return product


def set_recipe_line(product_id, ingredient_id, quantity):
    """Add an ingredient to a recipe, or update its quantity. Returns (line, created)."""
    if quantity < 1:
        raise ValueError("Quantity must be at least 1.")
    return BomLine.objects.update_or_create(
        product_id=product_id, ingredient_id=ingredient_id, defaults={"quantity": quantity}
    )
