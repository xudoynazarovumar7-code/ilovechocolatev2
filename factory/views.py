from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import ManufacturingOrderForm, ProductForm, RecipeLineForm, RestockForm
from .models import BomLine, ManufacturingOrder, Product, StockMovement
from .services import InvalidRestockError, ProductionError, create_product, produce, restock, set_recipe_line


def dashboard(request):
    """Main page: stock levels, restock form, new-order form, orders and stock log."""
    if request.method == "POST":
        form = ManufacturingOrderForm(request.POST)
        if form.is_valid():
            order = form.save()
            messages.success(request, f"Created {order}. Press Produce when ready.")
            return redirect("dashboard")
    else:
        form = ManufacturingOrderForm()

    context = {
        "form": form,
        "restock_form": RestockForm(),
        "product_form": ProductForm(),
        "recipe_form": RecipeLineForm(),
        "recipes": Product.objects.filter(kind=Product.Kind.FINISHED).prefetch_related("bom_lines__ingredient"),
        "has_products": Product.objects.exists(),
        "ingredients": Product.objects.filter(kind=Product.Kind.INGREDIENT),
        "finished_goods": Product.objects.filter(kind=Product.Kind.FINISHED),
        "orders": ManufacturingOrder.objects.select_related("product")[:20],
        "movements": StockMovement.objects.select_related("product")[:15],
    }
    return render(request, "factory/dashboard.html", context)


@require_POST
def produce_order(request, order_id):
    try:
        order = produce(order_id)
    except ProductionError as error:
        messages.error(request, str(error))
    except ManufacturingOrder.DoesNotExist:
        messages.error(request, "That order does not exist.")
    else:
        messages.success(request, f"Produced {order.quantity} x {order.product}.")
    return redirect("dashboard")


@require_POST
def restock_stock(request):
    form = RestockForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Restock failed: " + " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("dashboard")
    try:
        product = restock(form.cleaned_data["product"].pk, form.cleaned_data["amount"])
    except InvalidRestockError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request, f"Added {form.cleaned_data['amount']} to {product.name}. New stock: {product.stock}."
        )
    return redirect("dashboard")


def _form_errors(form):
    return " ".join(f"{field}: {error}" for field, errs in form.errors.items() for error in errs)


@require_POST
def add_product(request):
    form = ProductForm(request.POST)
    if form.is_valid():
        product = create_product(**form.cleaned_data)
        messages.success(request, f"Added {product.name} ({product.get_kind_display()}).")
    else:
        messages.error(request, "Could not add product. " + _form_errors(form))
    return redirect("dashboard")


@require_POST
def save_recipe_line(request):
    form = RecipeLineForm(request.POST)
    if form.is_valid():
        line, created = set_recipe_line(
            form.cleaned_data["product"].pk, form.cleaned_data["ingredient"].pk, form.cleaned_data["quantity"]
        )
        verb = "Added" if created else "Updated"
        messages.success(request, f"{verb} recipe: {line}.")
    else:
        messages.error(request, "Could not save recipe line. " + _form_errors(form))
    return redirect("dashboard")


@require_POST
def delete_recipe_line(request, line_id):
    deleted, _ = BomLine.objects.filter(pk=line_id).delete()
    if deleted:
        messages.success(request, "Removed ingredient from recipe.")
    return redirect("dashboard")
