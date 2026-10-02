# The Chocolate Factory (Mini-MRP)

A small Manufacturing Resource Planning app built with Django + SQLite.
Define products, link finished goods to ingredients with a Bill of Materials,
restock the warehouse, and produce manufacturing orders that update stock atomically.

## Run it locally

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # optional shortcut: Cocoa 5000g, Sugar 2000g, Dark Chocolate (50g + 20g)
python manage.py runserver
```

- Main page: http://127.0.0.1:8000/
- Admin (optional, needs `python manage.py createsuperuser`): http://127.0.0.1:8000/admin/
- Run tests: `python manage.py test`

The app works from an empty database: use "Products & recipes" on the main page to add
ingredients, a finished good and its recipe. `seed_demo` is just a shortcut.

## What you can do on the main page

- **Products & recipes**: add ingredients and finished goods; add/update/remove recipe lines.
- **Remove / restore products**: stop using an ingredient or finished good (history is kept).
- **Add stock / restock**: add received ingredients to the warehouse.
- **Import finished goods**: add finished goods that were bought in rather than produced here.
- **Manufacturing orders**: create an order, then press **Produce**.
- **Stock log**: every stock change (restock, ingredients used, chocolate produced).

## Data model

- **Product**: `name`, `kind` (ingredient / finished), `stock`
- **BomLine**: `product` (finished good) -> `ingredient` with a `quantity` per 1 unit.
  One row per ingredient, so each recipe supports any quantities. Unique per (product, ingredient).
- **ManufacturingOrder**: `product`, `quantity`, `status` (draft / done), timestamps
- **StockMovement**: a log line for every stock change (`product`, signed `change`, `reason`:
  restock, consumed, produced, imported, written off)

`Product` also has `is_active`; see below.

## How integrity is handled

`factory/services.py::produce()` runs inside `transaction.atomic`:
it locks the order and ingredient rows, checks every ingredient, subtracts ingredients,
adds finished goods, writes the stock log and marks the order done. Any exception rolls
everything back, so you never lose sugar without gaining chocolate. Stock updates use `F()`
expressions, and stock fields are `PositiveIntegerField`, so the database itself rejects negative stock.

## Removing products and importing stock

**Removing is a soft delete.** Pressing Remove sets `is_active = False`; the row stays in the
database, so old orders, recipes and stock-log lines that point at it remain valid
(foreign keys are `PROTECT`, so a real delete would be refused or would erase history).
Removed products disappear from the page and all dropdowns and appear under "Removed products",
where they can be restored (with 0 stock).

- Remaining stock is **written off**: a negative `written_off` line in the stock log and stock set to 0.
- An ingredient still used in an active recipe **cannot** be removed until it is taken out of the recipe.
- Removing a finished good **cancels its draft orders** (status `cancelled`); produced orders are untouched.

**Importing finished goods** works like restocking, but for finished products that are bought in
instead of manufactured. It adds to the product's stock inside a transaction and logs an `imported`
line. It does not touch ingredients or recipes.

## Error handling

If stock is insufficient (e.g. 1,000 chocolates with 10g of sugar), nothing changes and the
UI shows exactly what is short, e.g. `Sugar: need 20000, have 10`. Orders already produced
cannot be produced twice, and products with no recipe are rejected.

## Assumptions

- Quantities are whole numbers (grams for ingredients, units for finished goods).
- A recipe is defined per 1 unit of finished good; order needs = recipe quantity x order quantity.
- A failed attempt leaves the order in "draft" so it can be retried after restocking.
- No login or user roles (demo scope); anyone with access to the page can produce and restock.
- SQLite ignores row locks (`select_for_update`), but the code is written to work correctly
  on PostgreSQL/MySQL, where concurrent production matters.
