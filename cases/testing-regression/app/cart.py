"""Shopping cart: line items with quantity validation.

Quantities are validated on every add; the catalog is consulted only for
product existence.  Stock is checked later, at checkout time.
"""

from app import catalog

# A single order line may not exceed this many units.
MAX_QUANTITY_PER_LINE = 99


class CartError(ValueError):
    """Invalid cart operation (bad quantity, unknown product, ...)."""


class Cart:
    """A per-customer cart: product_id -> quantity."""

    def __init__(self):
        self.items = {}

    def add(self, product_id, quantity):
        """Add `quantity` units of a product; merges with an existing line.

        Returns the new quantity of the line.
        """
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise CartError("quantity must be an integer")
        if quantity < 1:
            raise CartError("quantity must be at least 1")
        product = catalog.get_product(product_id)  # raises CatalogError
        new_quantity = self.items.get(product_id, 0) + quantity
        if new_quantity > MAX_QUANTITY_PER_LINE:
            raise CartError("no more than %d units of %s per order"
                            % (MAX_QUANTITY_PER_LINE, product.product_id))
        self.items[product_id] = new_quantity
        return new_quantity

    def remove(self, product_id):
        """Drop a line from the cart entirely."""
        if product_id not in self.items:
            raise CartError("product %s is not in the cart" % product_id)
        del self.items[product_id]

    def total_items(self):
        """Total number of units across all lines."""
        return sum(self.items.values())

    def subtotal(self):
        """Sum of price * quantity over all lines, in rubles."""
        total = 0.0
        for product_id, quantity in self.items.items():
            total += catalog.get_product(product_id).price * quantity
        return round(total, 2)
