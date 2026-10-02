"""Business rules live here, separate from the web layer (views)."""

from collections import namedtuple

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import BomLine, ManufacturingOrder, Product, StockMovement


# ---------------------------------------------------------------- errors
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


class OrderCancelledError(ProductionError):
    pass


class ProductRemovedError(ProductionError):
    pass


class InvalidStockError(Exception):
    """Bad amount, or the wrong kind of product, for a restock/import."""


class ProductRemovalError(Exception):
    """A product cannot be removed (or restored) right now."""


# ------------------------------------------------------------ production
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
    if order.status == ManufacturingOrder.Status.CANCELLED:
        raise OrderCancelledError("This order was cancelled because its product was removed.")
    if not order.product.is_active:
        raise ProductRemovedError(f"'{order.product}' has been removed.")

    bom_lines = list(order.product.bom_lines.select_related("ingredient"))
    if not bom_lines:
        raise NoRecipeError(f"'{order.product}' has no Bill of Materials yet.")
    for line in bom_lines:
        if not line.ingredient.is_active:
            raise ProductRemovedError(f"Ingredient '{line.ingredient}' has been removed. Update the recipe first.")

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


# --------------------------------------------------------------- stock in
def _add_stock(product_id, amount, expected_kind, reason):
    """Shared by restock (ingredients) and import (finished goods)."""
    if amount < 1:
        raise InvalidStockError("Amount must be at least 1.")
    product = Product.objects.select_for_update().get(pk=product_id)
    if not product.is_active:
        raise InvalidStockError(f"'{product}' has been removed.")
    if product.kind != expected_kind:
        raise InvalidStockError(f"'{product}' is a {product.get_kind_display().lower()}; wrong kind for this action.")
    Product.objects.filter(pk=product.pk).update(stock=F("stock") + amount)
    StockMovement.objects.create(product=product, change=amount, reason=reason)
    product.refresh_from_db()
    return product


@transaction.atomic
def restock(product_id, amount):
    """Add received ingredients to the warehouse and record it in the stock log."""
    return _add_stock(product_id, amount, Product.Kind.INGREDIENT, StockMovement.Reason.RESTOCK)


@transaction.atomic
def import_finished_goods(product_id, amount):
    """Add finished goods that were bought in (not produced here) and record it in the stock log."""
    return _add_stock(product_id, amount, Product.Kind.FINISHED, StockMovement.Reason.IMPORTED)


# ------------------------------------------------- products and recipes
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


RemovalResult = namedtuple("RemovalResult", "product written_off cancelled_orders")


@transaction.atomic
def remove_product(product_id):
    """Stop using a product. The row is kept (is_active=False) so history stays valid.

    - An ingredient that is still in an active recipe cannot be removed.
    - Draft orders for a removed finished good are cancelled.
    - Any stock still on the shelf is written off: a negative line in the stock log,
      and the product's stock goes to 0.
    """
    product = Product.objects.select_for_update().get(pk=product_id)
    if not product.is_active:
        raise ProductRemovalError(f"'{product}' is already removed.")

    cancelled = 0
    if product.kind == Product.Kind.INGREDIENT:
        users = BomLine.objects.filter(ingredient=product, product__is_active=True).select_related("product")
        if users:
            names = ", ".join(sorted({line.product.name for line in users}))
            raise ProductRemovalError(
                f"'{product}' is still used in the recipe of {names}. Remove it from the recipe first."
            )
    else:
        cancelled = ManufacturingOrder.objects.filter(
            product=product, status=ManufacturingOrder.Status.DRAFT
        ).update(status=ManufacturingOrder.Status.CANCELLED)

    written_off = product.stock
    if written_off > 0:
        StockMovement.objects.create(
            product=product, change=-written_off, reason=StockMovement.Reason.WRITTEN_OFF
        )
    product.stock = 0
    product.is_active = False
    product.save(update_fields=["stock", "is_active"])
    return RemovalResult(product, written_off, cancelled)


@transaction.atomic
def restore_product(product_id):
    """Bring a removed product back (with 0 stock). Cancelled orders stay cancelled."""
    product = Product.objects.select_for_update().get(pk=product_id)
    if product.is_active:
        raise ProductRemovalError(f"'{product}' is not removed.")
    product.is_active = True
    product.save(update_fields=["is_active"])
    return product
