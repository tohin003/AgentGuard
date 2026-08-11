"""Pagination helper.

SPEC §33: "Existing pagination utility exists" — the agent proposing a new
PaginationRepository should be challenged with this file as evidence.
"""

from shop.models import MAX_PAGE_SIZE


def paginate(query, page: int = 1, per_page: int = 20):
    per_page = min(per_page, MAX_PAGE_SIZE)
    return query.limit(per_page).offset((page - 1) * per_page)


def page_metadata(total: int, page: int, per_page: int) -> dict:
    return {"total": total, "page": page, "pages": (total + per_page - 1) // per_page}
