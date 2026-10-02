from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import BomLine, ManufacturingOrder, Product, StockMovement
from .services import (
    InsufficientStockError,
    InvalidStockError,
    OrderAlreadyDoneError,
    OrderCancelledError,
    ProductionError,
    ProductRemovalError,
    create_product,
    import_finished_goods,
    produce,
    remove_product,
    restock,
    restore_product,
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
            with self.assertRaises(InvalidStockError):
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


class ImportTests(FactoryTestCase):
    def test_import_adds_finished_goods_and_logs_it(self):
        import_finished_goods(self.dark.pk, 40)
        self.assertEqual(self.stock(self.dark), 40)
        self.assertEqual(self.stock(self.cocoa), 5000)  # ingredients untouched
        movement = StockMovement.objects.get()
        self.assertEqual((movement.change, movement.reason), (40, "imported"))

    def test_import_rejects_ingredients_and_bad_amounts(self):
        with self.assertRaises(InvalidStockError):
            import_finished_goods(self.sugar.pk, 10)
        with self.assertRaises(InvalidStockError):
            import_finished_goods(self.dark.pk, 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_restock_rejects_finished_goods(self):
        with self.assertRaises(InvalidStockError):
            restock(self.dark.pk, 10)

    def test_import_from_the_page(self):
        response = self.client.post(reverse("import_stock"), {"product": self.dark.pk, "amount": 25})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(self.stock(self.dark), 25)


class RemoveProductTests(FactoryTestCase):
    def test_removing_unused_ingredient_with_stock_writes_it_off(self):
        salt = create_product("Salt", "ingredient", 300)
        result = remove_product(salt.pk)
        salt.refresh_from_db()
        self.assertFalse(salt.is_active)
        self.assertEqual(salt.stock, 0)
        self.assertEqual(result.written_off, 300)
        log = StockMovement.objects.filter(product=salt).order_by("id")
        self.assertEqual([(m.change, m.reason) for m in log], [(300, "restock"), (-300, "written_off")])

    def test_row_is_kept_not_deleted(self):
        salt = create_product("Salt", "ingredient", 0)
        remove_product(salt.pk)
        self.assertTrue(Product.objects.filter(pk=salt.pk).exists())
        self.assertFalse(Product.active.filter(pk=salt.pk).exists())

    def test_ingredient_in_an_active_recipe_cannot_be_removed(self):
        with self.assertRaises(ProductRemovalError) as ctx:
            remove_product(self.sugar.pk)
        self.assertIn("Dark Chocolate", str(ctx.exception))
        self.assertEqual(self.stock(self.sugar), 2000)
        self.assertTrue(Product.active.filter(pk=self.sugar.pk).exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_removing_finished_good_cancels_drafts_and_writes_off_stock(self):
        import_finished_goods(self.dark.pk, 12)
        draft = ManufacturingOrder.objects.create(product=self.dark, quantity=5)
        done = ManufacturingOrder.objects.create(product=self.dark, quantity=1)
        produce(done.pk)
        result = remove_product(self.dark.pk)
        self.assertEqual(result.cancelled_orders, 1)
        self.assertEqual(result.written_off, 13)
        draft.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual(draft.status, "cancelled")
        self.assertEqual(done.status, "done")  # history untouched
        self.assertEqual(self.stock(self.dark), 0)

    def test_cancelled_order_cannot_be_produced(self):
        draft = ManufacturingOrder.objects.create(product=self.dark, quantity=5)
        remove_product(self.dark.pk)
        with self.assertRaises(OrderCancelledError):
            produce(draft.pk)
        self.assertEqual(self.stock(self.cocoa), 5000)

    def test_removed_ingredient_blocks_production_after_restoring_finished_good(self):
        order_product = self.dark
        remove_product(self.dark.pk)       # finished good out first
        remove_product(self.sugar.pk)      # now sugar is free to go
        restore_product(self.dark.pk)      # recipe still lists the removed sugar
        order = ManufacturingOrder.objects.create(product=order_product, quantity=1)
        with self.assertRaises(ProductionError):
            produce(order.pk)
        self.assertEqual(self.stock(self.cocoa), 5000)

    def test_cannot_remove_twice_or_restore_active(self):
        salt = create_product("Salt", "ingredient", 0)
        remove_product(salt.pk)
        with self.assertRaises(ProductRemovalError):
            remove_product(salt.pk)
        restore_product(salt.pk)
        with self.assertRaises(ProductRemovalError):
            restore_product(salt.pk)

    def test_restore_brings_product_back_with_zero_stock(self):
        salt = create_product("Salt", "ingredient", 50)
        remove_product(salt.pk)
        restore_product(salt.pk)
        salt.refresh_from_db()
        self.assertTrue(salt.is_active)
        self.assertEqual(salt.stock, 0)

    def test_removed_products_disappear_from_forms_and_page(self):
        salt = create_product("Salt", "ingredient", 10)
        remove_product(salt.pk)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, 'value="%d"' % salt.pk)  # not offered in any dropdown
        self.assertContains(response, "Removed products")
        with self.assertRaises(InvalidStockError):
            restock(salt.pk, 5)

    def test_re_adding_a_removed_name_points_to_restore(self):
        salt = create_product("Salt", "ingredient", 0)
        remove_product(salt.pk)
        response = self.client.post(
            reverse("add_product"), {"name": "Salt", "kind": "ingredient", "stock": 0}, follow=True
        )
        self.assertContains(response, "was removed earlier")
        self.assertEqual(Product.objects.filter(name="Salt").count(), 1)

    def test_remove_and_restore_from_the_page(self):
        salt = create_product("Salt", "ingredient", 20)
        self.client.post(reverse("remove_product", args=[salt.pk]))
        salt.refresh_from_db()
        self.assertFalse(salt.is_active)
        self.client.post(reverse("restore_product", args=[salt.pk]))
        salt.refresh_from_db()
        self.assertTrue(salt.is_active)

    def test_blocked_removal_shows_message_on_the_page(self):
        response = self.client.post(reverse("remove_product", args=[self.sugar.pk]), follow=True)
        self.assertContains(response, "Remove it from the recipe first")
