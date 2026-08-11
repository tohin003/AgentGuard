"""Orders endpoint."""

from shop.models import Order
from shop.utils.pagination import paginate


def list_orders(session, page: int = 1):
    return paginate(session.query(Order), page=page)
