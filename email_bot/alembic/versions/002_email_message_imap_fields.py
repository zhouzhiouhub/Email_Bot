"""IMAP metadata on inbound messages (read/unread + recovery)

Revision ID: 002
Revises: 001
Create Date: 2026-03-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("imap_uid", sa.Integer(), nullable=True))
    op.add_column("email_messages", sa.Column("imap_folder", sa.String(255), nullable=True))
    op.add_column("email_messages", sa.Column("received_inbox", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("email_messages", "received_inbox")
    op.drop_column("email_messages", "imap_folder")
    op.drop_column("email_messages", "imap_uid")
