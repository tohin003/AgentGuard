"""Domain models."""

from dataclasses import dataclass

MAX_PAGE_SIZE = 100


@dataclass
class User:
    id: int
    email: str
    is_active: bool = True

    def display_name(self) -> str:
        return self.email.split("@")[0]


@dataclass
class Order:
    id: int
    user_id: int
    total_cents: int
