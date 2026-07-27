"""Simple data shapes shared by the shop modules."""

from dataclasses import dataclass


@dataclass
class Product:
    """One catalog entry.  Price is in rubles."""

    product_id: str
    name: str
    price: float
    stock: int
