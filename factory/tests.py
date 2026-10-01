from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import BomLine, ManufacturingOrder, Product, StockMovement
from .services import (
    InsufficientStockError,
    InvalidRestockError,
    OrderAlreadyDoneError,
    create_product,
    produce,
    restock,
    set_recipe_line,
)


class FactoryTestCase(TestCase):
    def setUp(self):
        self.cocoa = Product.objects.create(name="Cocoa", kind="ingredient", stock=5000)
        self.sugar = Product.objects.create(name="Sugar", kind="ingredient", stock=2000)
        self.dark = Product.objects.create(name="Dark Chocolate", kind="finished", stock=0)
        BomLine.objects.create(product=self.dark, ingredient=self.cocoa, quantity=50)
        BomLine.objects.create(product=self.dark, ingredient=self.sugar, quantity=20)

    def stock(self, p):
        p.refresh_from_db()
        return p.stock


class ProduceTests(FactoryTestCase):
    def test_successful_production_updates_all_stock(self):
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=10)
        produce(order.pk)
        self.assertEqual(self.stock(self.cocoa), 4500)
        self.assertEqual(self.stock(self.sugar), 1800)
        self.assertEqual(self.stock(self.dark), 10)
        order.refresh_from_db()
        self.assertEqual(order.status, "done")

    def test_production_writes_stock_log(self):
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=10)
        produce(order.pk)
        changes = {m.product.name: m.change for m in StockMovement.objects.all()}
        self.assertEqual(changes, {"Cocoa": -500, "Sugar": -200, "Dark Chocolate": 10})

    def test_insufficient_stock_changes_nothing(self):
        self.sugar.stock = 10
        self.sugar.save()
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=1000)
        with self.assertRaises(InsufficientStockError):
            produce(order.pk)
        self.assertEqual(self.stock(self.cocoa), 5000)
        self.assertEqual(self.stock(self.sugar), 10)
        self.assertEqual(self.stock(self.dark), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")

    def test_cannot_produce_twice(self):
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=1)
        produce(order.pk)
        with self.assertRaises(OrderAlreadyDoneError):
            produce(order.pk)
        self.assertEqual(self.stock(self.dark), 1)

    def test_crash_midway_rolls_back_stock_and_log(self):
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=10)
        with mock.patch.object(ManufacturingOrder, "save", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                produce(order.pk)
        self.assertEqual(self.stock(self.cocoa), 5000)
        self.assertEqual(self.stock(self.sugar), 2000)
        self.assertEqual(self.stock(self.dark), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_database_blocks_negative_stock(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.sugar.pk).update(stock=-5)


class RestockTests(FactoryTestCase):
    def test_restock_adds_stock_and_logs_it(self):
        restock(self.sugar.pk, 500)
        self.assertEqual(self.stock(self.sugar), 2500)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.change, movement.reason), (500, "restock"))

    def test_restock_rejects_zero_or_negative(self):
        for bad in (0, -5):
            with self.assertRaises(InvalidRestockError):
                restock(self.sugar.pk, bad)
        self.assertEqual(self.stock(self.sugar), 2000)


class PageTests(FactoryTestCase):
    def test_dashboard_loads(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Add stock / restock")

    def test_restock_from_the_page(self):
        response = self.client.post(reverse("restock"), {"product": self.sugar.pk, "amount": 300})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(self.stock(self.sugar), 2300)

    def test_restock_with_bad_amount_changes_nothing(self):
        self.client.post(reverse("restock"), {"product": self.sugar.pk, "amount": -10})
        self.assertEqual(self.stock(self.sugar), 2000)

    def test_produce_from_the_page_shows_error_when_short(self):
        self.sugar.stock = 10
        self.sugar.save()
        order = ManufacturingOrder.objects.create(product=self.dark, quantity=1000)
        response = self.client.post(reverse("produce_order", args=[order.pk]), follow=True)
        self.assertContains(response, "Not enough stock")
        self.assertEqual(self.stock(self.cocoa), 5000)


class ProductAndRecipeTests(FactoryTestCase):
    def test_create_product_with_stock_is_logged(self):
        milk = create_product("Milk powder", "ingredient", 300)
        self.assertEqual(milk.stock, 300)
        self.assertEqual(StockMovement.objects.get().change, 300)

    def test_set_recipe_line_adds_then_updates(self):
        milk = create_product("Milk powder", "ingredient", 0)
        line, created = set_recipe_line(self.dark.pk, milk.pk, 30)
        self.assertTrue(created)
        line, created = set_recipe_line(self.dark.pk, milk.pk, 45)
        self.assertFalse(created)
        self.assertEqual(BomLine.objects.get(product=self.dark, ingredient=milk).quantity, 45)

    def test_add_product_from_the_page(self):
        response = self.client.post(reverse("add_product"), {"name": "Milk powder", "kind": "ingredient", "stock": 100})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(Product.objects.get(name="Milk powder").stock, 100)

    def test_duplicate_product_name_is_rejected(self):
        self.client.post(reverse("add_product"), {"name": "Sugar", "kind": "ingredient", "stock": 5})
        self.assertEqual(Product.objects.filter(name="Sugar").count(), 1)

    def test_recipe_forms_from_the_page(self):
        self.client.post(reverse("save_recipe_line"), {"product": self.dark.pk, "ingredient": self.sugar.pk, "quantity": 25})
        self.assertEqual(BomLine.objects.get(product=self.dark, ingredient=self.sugar).quantity, 25)
        line = BomLine.objects.get(product=self.dark, ingredient=self.sugar)
        self.client.post(reverse("delete_recipe_line", args=[line.pk]))
        self.assertFalse(BomLine.objects.filter(pk=line.pk).exists())

    def test_empty_database_shows_welcome_message(self):
        BomLine.objects.all().delete()
        Product.objects.all().delete()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "The warehouse is empty")
