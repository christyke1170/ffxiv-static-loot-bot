"""Neutral current-resource balances and legacy-resource translation helpers."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Character, CharacterKind, Static, V2ResourceBalance

BOOK_KEYS = tuple(f"BOOK_FLOOR_{number}" for number in range(1, 5))
COFFER_KEYS = frozenset(
    {
        "ACCESSORY_COFFER",
        "HEAD_COFFER",
        "GLOVES_COFFER",
        "BOOTS_COFFER",
        "CHEST_COFFER",
        "PANTS_COFFER",
        "WEAPON_COFFER",
    }
)
MATERIAL_KEYS = frozenset({"ACCESSORY_GLAZE", "ARMOR_TWINE"})
WEAPON_KEYS = frozenset({"WEAPON_TOMESTONE", "WEAPON_AUGMENT"})
SUPPORTED_RESOURCE_KEYS = frozenset((*BOOK_KEYS, *COFFER_KEYS, *MATERIAL_KEYS, *WEAPON_KEYS))


def validate_resource_key(resource_key: str) -> str:
    key = resource_key.strip().upper()
    if key not in SUPPORTED_RESOURCE_KEYS:
        raise ValueError(f"Unsupported neutral resource key: {resource_key}.")
    return key


def validate_quantity(quantity: int) -> int:
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        raise ValueError("Neutral resource balances must be nonnegative whole numbers.")
    return quantity


def current_balance(
    session, static_id: int, recipient_id: int, resource_key: str
) -> V2ResourceBalance | None:
    key = validate_resource_key(resource_key)
    return session.scalar(
        select(V2ResourceBalance)
        .execution_options(populate_existing=True)
        .where(
            V2ResourceBalance.static_id == static_id,
            V2ResourceBalance.recipient_id == recipient_id,
            V2ResourceBalance.resource_key == key,
        )
    )


def set_current_balance(
    session, static: Static, character: Character, resource_key: str, quantity: int
):
    key = validate_resource_key(resource_key)
    quantity = validate_quantity(quantity)
    _require_character(static, character)
    if key in BOOK_KEYS and character.kind is not CharacterKind.MAIN:
        raise ValueError("Books are tracked for Mains only.")
    row = current_balance(session, static.id, character.id, key)
    if row is None:
        row = V2ResourceBalance(
            static_id=static.id,
            recipient_id=character.id,
            resource_key=key,
            quantity=quantity,
        )
        session.add(row)
    else:
        row.quantity = quantity
    session.flush()
    return row


def adjust_current_balance(
    session, static_id: int, recipient_id: int, resource_key: str, delta: int
):
    key = validate_resource_key(resource_key)
    if not isinstance(delta, int) or isinstance(delta, bool):
        raise ValueError("Neutral resource balance changes must be whole numbers.")
    if key in BOOK_KEYS:
        character = session.get(Character, recipient_id)
        if character is None or character.kind is not CharacterKind.MAIN:
            raise ValueError("Books are tracked for Mains only.")
    dialect = session.get_bind().dialect.name
    if dialect in {"postgresql", "sqlite"}:
        if delta < 0:
            result = session.execute(
                update(V2ResourceBalance)
                .where(
                    V2ResourceBalance.static_id == static_id,
                    V2ResourceBalance.recipient_id == recipient_id,
                    V2ResourceBalance.resource_key == key,
                    V2ResourceBalance.quantity + delta >= 0,
                )
                .values(quantity=V2ResourceBalance.quantity + delta)
            )
            if result.rowcount != 1:
                raise ValueError(f"Neutral {key} balance cannot become negative.")
        else:
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
            conflict_scope = (
                {"index_where": V2ResourceBalance.static_id.is_not(None)}
                if dialect == "postgresql"
                else {}
            )
            session.execute(
                insert(V2ResourceBalance)
                .values(
                    static_id=static_id, recipient_id=recipient_id, resource_key=key, quantity=0
                )
                .on_conflict_do_nothing(
                    index_elements=["static_id", "recipient_id", "resource_key"],
                    **conflict_scope,
                )
            )
            session.execute(
                update(V2ResourceBalance)
                .where(
                    V2ResourceBalance.static_id == static_id,
                    V2ResourceBalance.recipient_id == recipient_id,
                    V2ResourceBalance.resource_key == key,
                )
                .values(quantity=V2ResourceBalance.quantity + delta)
            )
        session.flush()
        return current_balance(session, static_id, recipient_id, key)
    row = current_balance(session, static_id, recipient_id, key)
    if row is None:
        if delta < 0:
            raise ValueError(f"No current {key} resource balance is available.")
        row = V2ResourceBalance(
            static_id=static_id,
            recipient_id=recipient_id,
            resource_key=key,
            quantity=0,
        )
        session.add(row)
    if row.quantity + delta < 0:
        raise ValueError(f"Neutral {key} balance cannot become negative.")
    row.quantity += delta
    session.flush()
    return row


def current_balances(session, static_id: int, recipient_id: int) -> dict[str, V2ResourceBalance]:
    return {
        row.resource_key: row
        for row in session.scalars(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
            )
        )
    }


def _require_character(static: Static, character: Character) -> None:
    if character.static_member is None or character.static_member.static_id != static.id:
        raise ValueError("Character is not in the selected static.")
