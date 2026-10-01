from django.db import models


class Product(models.Model):
    """Anything we keep in the warehouse: raw ingredients or finished goods."""

    class Kind(models.TextChoices):
        INGREDIENT = "ingredient", "Ingredient"
        FINISHED = "finished", "Finished good"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    # Ingredients are counted in grams, finished goods in units.
    # PositiveIntegerField also adds a database-level "must be >= 0" check,
    # so negative stock is impossible even if the app code has a bug.
    stock = models.PositiveIntegerField(default=0, help_text="Stock on hand")

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


class BomLine(models.Model):
    """One row of a recipe: 'to make 1 unit of <product>, use <quantity> of <ingredient>'."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="bom_lines",
        limit_choices_to={"kind": Product.Kind.FINISHED},
    )
    ingredient = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="used_in",
        limit_choices_to={"kind": Product.Kind.INGREDIENT},
    )
    quantity = models.PositiveIntegerField(help_text="Amount of ingredient per 1 unit of product")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "ingredient"], name="unique_ingredient_per_product"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="bom_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.product}: {self.quantity} x {self.ingredient}"


class ManufacturingOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DONE = "done", "Done"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="manufacturing_orders",
        limit_choices_to={"kind": Product.Kind.FINISHED},
    )
    quantity = models.PositiveIntegerField(help_text="How many units to produce")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    produced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="mo_quantity_positive"),
        ]

    def __str__(self):
        return f"MO #{self.pk}: {self.quantity} x {self.product}"


class StockMovement(models.Model):
    """One line in the stock log. Every change to stock writes one of these.

    `change` is signed: +500 means 500 came in, -200 means 200 went out.
    """

    class Reason(models.TextChoices):
        RESTOCK = "restock", "Restock"
        CONSUMED = "consumed", "Used in production"
        PRODUCED = "produced", "Produced"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    change = models.IntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    order = models.ForeignKey(
        ManufacturingOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(change=0), name="movement_change_not_zero"),
        ]

    def __str__(self):
        return f"{self.change:+d} {self.product} ({self.get_reason_display()})"
