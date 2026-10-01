from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orders/<int:order_id>/produce/", views.produce_order, name="produce_order"),
    path("restock/", views.restock_stock, name="restock"),
    path("products/add/", views.add_product, name="add_product"),
    path("recipes/save/", views.save_recipe_line, name="save_recipe_line"),
    path("recipes/<int:line_id>/delete/", views.delete_recipe_line, name="delete_recipe_line"),
]
