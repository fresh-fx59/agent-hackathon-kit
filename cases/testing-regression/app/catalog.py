"""Product catalog: an in-memory product list with stock lookups."""

from app.models import Product


class CatalogError(KeyError):
    """Unknown product id."""


PRODUCTS = {
    "SKU-1": Product("SKU-1", "Keyboard", 2490.0, 12),
    "SKU-2": Product("SKU-2", "Mouse", 990.0, 30),
    "SKU-3": Product("SKU-3", "Monitor", 18990.0, 5),
    "SKU-4": Product("SKU-4", "USB cable", 290.0, 100),
    "SKU-5": Product("SKU-5", "Headset", 4990.0, 8),
    "SKU-6": Product("SKU-6", "Webcam", 3490.0, 0),
}


def get_product(product_id):
    """Return the Product or raise CatalogError."""
    try:
        return PRODUCTS[product_id]
    except KeyError:
        raise CatalogError("no such product: %s" % product_id)


def list_products():
    """All products, ordered by id."""
    return sorted(PRODUCTS.values(), key=lambda p: p.product_id)


def search(query):
    """Case-insensitive substring search over product names."""
    needle = str(query).strip().lower()
    if not needle:
        return []
    return [p for p in list_products() if needle in p.name.lower()]


def in_stock(product_id, quantity):
    """True when at least `quantity` units are available."""
    return get_product(product_id).stock >= quantity
