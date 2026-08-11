"""Users endpoint — SPEC §33's "/users endpoint exists"."""

from shop.repositories.user import UserRepository
from shop.utils.pagination import paginate


def list_users(session, page: int = 1):
    repo = UserRepository(session)
    return repo.list_all()


def get_user(session, user_id: int):
    return UserRepository(session).get_by_id(user_id)
