"""User persistence.

Deliberately does NOT define get_active_users() — SPEC §14's worked example depends on
that method being absent from the repository.
"""

from shop.models import User


class UserRepository:
    def __init__(self, session):
        self.session = session

    def get_by_id(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def list_all(self, limit: int = 50, offset: int = 0) -> list[User]:
        return self.session.query(User).limit(limit).offset(offset).all()

    def count(self) -> int:
        return self.session.query(User).count()
