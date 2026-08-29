"""Allow new weekly reclears to use neutral floor and tier state.

Historical weeks retain their tier and floor foreign keys.  A neutral week uses
``raid_tier_id IS NULL`` and logical floor numbers instead.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w6r0t4y8u2i5"
down_revision: str | Sequence[str] | None = "v5q9s3u7w1y4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "SELECT static_id, week_start, COUNT(*) AS count "
            "FROM split_weeks GROUP BY static_id, week_start HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce one weekly reclear per Static/reset period; duplicate conflicts: "
            + ", ".join(f"static={row[0]}, period={row[1]}, count={row[2]}" for row in duplicate)
        )
    if connection.dialect.name == "sqlite":
        op.create_table(
            "reclear_week_floors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "reclear_week_id", sa.Integer(), sa.ForeignKey("split_weeks.id"), nullable=False
            ),
            sa.Column("floor_number", sa.Integer(), nullable=False),
            sa.UniqueConstraint("reclear_week_id", "floor_number"),
            sa.CheckConstraint(
                "floor_number BETWEEN 1 AND 4", name="valid_reclear_week_floor_number"
            ),
        )
        connection.execute(
            sa.text(
                "INSERT INTO reclear_week_floors (reclear_week_id, floor_number) "
                "SELECT w.id, n.floor_number FROM split_weeks w CROSS JOIN "
                "(SELECT 1 AS floor_number UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4) n "
                "WHERE w.raid_tier_id IS NULL"
            )
        )
        with op.batch_alter_table("split_weeks") as batch:
            batch.alter_column("raid_tier_id", existing_type=sa.Integer(), nullable=True)
        with op.batch_alter_table("weekly_lockouts") as batch:
            batch.add_column(sa.Column("floor_number", sa.Integer(), nullable=True))
            batch.alter_column("raid_floor_id", existing_type=sa.Integer(), nullable=True)
            batch.create_check_constraint(
                "valid_weekly_floor_number", "floor_number IS NULL OR floor_number BETWEEN 1 AND 4"
            )
            batch.create_check_constraint(
                "weekly_lockout_exactly_one_floor_identity",
                "(raid_floor_id IS NULL) != (floor_number IS NULL)",
            )
        op.create_index(
            "uq_weekly_lockout_neutral_floor",
            "weekly_lockouts",
            ["character_id", "floor_number", "week_start"],
            unique=True,
            sqlite_where=sa.text("floor_number IS NOT NULL"),
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )
        with op.batch_alter_table("reclear_floor_completions") as batch:
            batch.add_column(sa.Column("floor_number", sa.Integer(), nullable=True))
            batch.alter_column("raid_floor_id", existing_type=sa.Integer(), nullable=True)
            batch.create_check_constraint(
                "valid_completion_floor_number",
                "floor_number IS NULL OR floor_number BETWEEN 1 AND 4",
            )
            batch.create_check_constraint(
                "completion_exactly_one_floor_identity",
                "(raid_floor_id IS NULL) != (floor_number IS NULL)",
            )
        op.create_index(
            "uq_reclear_completion_neutral_floor",
            "reclear_floor_completions",
            ["reclear_week_id", "reclear_group_id", "floor_number"],
            unique=True,
            sqlite_where=sa.text("floor_number IS NOT NULL"),
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )
    else:
        op.create_table(
            "reclear_week_floors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "reclear_week_id", sa.Integer(), sa.ForeignKey("split_weeks.id"), nullable=False
            ),
            sa.Column("floor_number", sa.Integer(), nullable=False),
            sa.UniqueConstraint("reclear_week_id", "floor_number"),
            sa.CheckConstraint(
                "floor_number BETWEEN 1 AND 4", name="valid_reclear_week_floor_number"
            ),
        )
        connection.execute(
            sa.text(
                "INSERT INTO reclear_week_floors (reclear_week_id, floor_number) "
                "SELECT w.id, n.floor_number FROM split_weeks w CROSS JOIN "
                "(SELECT 1 AS floor_number UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4) n "
                "WHERE w.raid_tier_id IS NULL"
            )
        )
        op.alter_column("split_weeks", "raid_tier_id", existing_type=sa.Integer(), nullable=True)
        op.add_column("weekly_lockouts", sa.Column("floor_number", sa.Integer(), nullable=True))
        op.alter_column(
            "weekly_lockouts", "raid_floor_id", existing_type=sa.Integer(), nullable=True
        )
        op.create_check_constraint(
            "valid_weekly_floor_number",
            "weekly_lockouts",
            "floor_number IS NULL OR floor_number BETWEEN 1 AND 4",
        )
        op.create_index(
            "uq_weekly_lockout_neutral_floor",
            "weekly_lockouts",
            ["character_id", "floor_number", "week_start"],
            unique=True,
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )
        op.create_check_constraint(
            "weekly_lockout_exactly_one_floor_identity",
            "weekly_lockouts",
            "(raid_floor_id IS NULL) != (floor_number IS NULL)",
        )
        op.add_column(
            "reclear_floor_completions", sa.Column("floor_number", sa.Integer(), nullable=True)
        )
        op.alter_column(
            "reclear_floor_completions", "raid_floor_id", existing_type=sa.Integer(), nullable=True
        )
        op.create_check_constraint(
            "valid_completion_floor_number",
            "reclear_floor_completions",
            "floor_number IS NULL OR floor_number BETWEEN 1 AND 4",
        )
        op.create_check_constraint(
            "completion_exactly_one_floor_identity",
            "reclear_floor_completions",
            "(raid_floor_id IS NULL) != (floor_number IS NULL)",
        )
        op.create_index(
            "uq_reclear_completion_neutral_floor",
            "reclear_floor_completions",
            ["reclear_week_id", "reclear_group_id", "floor_number"],
            unique=True,
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )


def downgrade() -> None:
    connection = op.get_bind()
    neutral_weeks = connection.execute(
        sa.text("SELECT COUNT(*) FROM split_weeks WHERE raid_tier_id IS NULL")
    ).scalar_one()
    neutral_completions = connection.execute(
        sa.text("SELECT COUNT(*) FROM reclear_floor_completions WHERE floor_number IS NOT NULL")
    ).scalar_one()
    neutral_lockouts = connection.execute(
        sa.text("SELECT COUNT(*) FROM weekly_lockouts WHERE floor_number IS NOT NULL")
    ).scalar_one()
    if neutral_weeks or neutral_completions or neutral_lockouts:
        raise RuntimeError(
            "Cannot downgrade neutral weekly state while neutral-only rows exist "
            f"(weeks={neutral_weeks}, completions={neutral_completions}, lockouts={neutral_lockouts}); "
            "restoring a required tier would fabricate historical configuration."
        )
    if connection.dialect.name == "sqlite":
        op.drop_table("reclear_week_floors")
        connection.exec_driver_sql("DROP INDEX IF EXISTS uq_reclear_completion_neutral_floor")
        connection.exec_driver_sql("DROP INDEX IF EXISTS uq_weekly_lockout_neutral_floor")
        with op.batch_alter_table("reclear_floor_completions") as batch:
            batch.drop_constraint("completion_exactly_one_floor_identity", type_="check")
            batch.drop_constraint("valid_completion_floor_number", type_="check")
            batch.alter_column("raid_floor_id", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("floor_number")
        with op.batch_alter_table("weekly_lockouts") as batch:
            batch.drop_constraint("weekly_lockout_exactly_one_floor_identity", type_="check")
            batch.drop_constraint("valid_weekly_floor_number", type_="check")
            batch.alter_column("raid_floor_id", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("floor_number")
        with op.batch_alter_table("split_weeks") as batch:
            batch.alter_column("raid_tier_id", existing_type=sa.Integer(), nullable=False)
    else:
        op.drop_table("reclear_week_floors")
        op.drop_index("uq_reclear_completion_neutral_floor", table_name="reclear_floor_completions")
        op.drop_index("uq_weekly_lockout_neutral_floor", table_name="weekly_lockouts")
        op.drop_constraint(
            "completion_exactly_one_floor_identity", "reclear_floor_completions", type_="check"
        )
        op.drop_constraint(
            "valid_completion_floor_number", "reclear_floor_completions", type_="check"
        )
        op.alter_column(
            "reclear_floor_completions", "raid_floor_id", existing_type=sa.Integer(), nullable=False
        )
        op.drop_column("reclear_floor_completions", "floor_number")
        op.drop_constraint(
            "weekly_lockout_exactly_one_floor_identity", "weekly_lockouts", type_="check"
        )
        op.drop_constraint("valid_weekly_floor_number", "weekly_lockouts", type_="check")
        op.alter_column(
            "weekly_lockouts", "raid_floor_id", existing_type=sa.Integer(), nullable=False
        )
        op.drop_column("weekly_lockouts", "floor_number")
        op.alter_column("split_weeks", "raid_tier_id", existing_type=sa.Integer(), nullable=False)
