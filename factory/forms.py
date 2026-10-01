from django import forms

from .models import ManufacturingOrder, Product


class ManufacturingOrderForm(forms.ModelForm):
    class Meta:
        model = ManufacturingOrder
        fields = ["product", "quantity"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(kind=Product.Kind.FINISHED)

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity < 1:
            raise forms.ValidationError("Quantity must be at least 1.")
        return quantity


class RestockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    amount = forms.IntegerField(
        min_value=1,
        label="Amount received",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(kind=Product.Kind.INGREDIENT)


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "kind", "stock"]
        labels = {"kind": "Type", "stock": "Starting stock"}
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Milk powder"}),
            "kind": forms.Select(attrs={"class": "form-select"}),
            "stock": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        }


class RecipeLineForm(forms.Form):
    """Add an ingredient to a recipe, or change its quantity if it is already there."""

    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        label="Finished product",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ingredient = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.IntegerField(
        min_value=1,
        label="Amount per 1 unit",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(kind=Product.Kind.FINISHED)
        self.fields["ingredient"].queryset = Product.objects.filter(kind=Product.Kind.INGREDIENT)
