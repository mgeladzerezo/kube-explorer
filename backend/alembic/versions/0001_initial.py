"""diagnoses and eval runs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnoses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("namespace", sa.String(253), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("top_category", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_diagnoses_namespace", "diagnoses", ["namespace"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.String(64), nullable=False),
        sa.Column("expected_category", sa.String(64), nullable=False),
        sa.Column("predicted_category", sa.String(64), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("diagnosis", sa.JSON(), nullable=True),
    )
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("diagnoses")
