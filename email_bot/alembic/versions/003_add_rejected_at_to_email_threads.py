"""Add rejected_at to email_threads for rejection cooldown

Revision ID: 003
Revises: 002
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_threads",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_threads", "rejected_at")
