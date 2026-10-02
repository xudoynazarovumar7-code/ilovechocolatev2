from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orders/<int:order_id>/produce/", views.produce_order, name="produce_order"),
    path("restock/", views.restock_stock, name="restock"),
    path("import/", views.import_stock, name="import_stock"),
    path("products/add/", views.add_product, name="add_product"),
    path("products/<int:product_id>/remove/", views.remove_product_view, name="remove_product"),
    path("products/<int:product_id>/restore/", views.restore_product_view, name="restore_product"),
    path("recipes/save/", views.save_recipe_line, name="save_recipe_line"),
    path("recipes/<int:line_id>/delete/", views.delete_recipe_line, name="delete_recipe_line"),
]
