"""Add static-owned job BiS ownership without removing legacy selections."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "n4h8j0k2l6m9"
down_revision: str | Sequence[str] | None = "m3g7h9c1d5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    static_column = sa.Column("static_id", sa.Integer(), sa.ForeignKey("statics.id"))
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("bis_sets") as batch:
            batch.add_column(static_column)
    finally:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    connection = op.get_bind()
    conflicts = connection.execute(
        sa.text(
            """
            SELECT sm.static_id, b.job_id
            FROM character_bis_selections selection
            JOIN characters character ON character.id = selection.character_id
            JOIN static_members sm ON sm.id = character.static_member_id
            JOIN bis_sets b ON b.id = selection.bis_set_id
            GROUP BY sm.static_id, b.job_id
            HAVING COUNT(DISTINCT b.id) > 1
            """
        )
    ).fetchall()
    conflicts += connection.execute(
        sa.text(
            """
            SELECT b.id, COUNT(DISTINCT sm.static_id)
            FROM character_bis_selections selection
            JOIN characters character ON character.id = selection.character_id
            JOIN static_members sm ON sm.id = character.static_member_id
            JOIN bis_sets b ON b.id = selection.bis_set_id
            GROUP BY b.id
            HAVING COUNT(DISTINCT sm.static_id) > 1
            """
        )
    ).fetchall()
    if conflicts:
        formatted = ", ".join(
            f"static={static_id}, job={job_id}" for static_id, job_id in conflicts
        )
        raise RuntimeError(
            "Static/job BiS migration is ambiguous; resolve conflicting selected configurations "
            f"before upgrading ({formatted})."
        )

    connection.execute(
        sa.text(
            """
            UPDATE bis_sets
            SET static_id = (
                SELECT sm.static_id
                FROM character_bis_selections selection
                JOIN characters character ON character.id = selection.character_id
                JOIN static_members sm ON sm.id = character.static_member_id
                WHERE selection.bis_set_id = bis_sets.id
                LIMIT 1
            )
            WHERE id IN (SELECT bis_set_id FROM character_bis_selections)
            """
        )
    )
    op.create_index(
        "uq_bis_sets_active_static_job",
        "bis_sets",
        ["static_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("static_id IS NOT NULL AND active = true"),
        sqlite_where=sa.text("static_id IS NOT NULL AND active = 1"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if "uq_bis_sets_active_static_job" in {
        index["name"] for index in inspect(connection).get_indexes("bis_sets")
    }:
        op.drop_index("uq_bis_sets_active_static_job", table_name="bis_sets")
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("bis_sets") as batch:
            batch.drop_column("static_id")
    finally:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
