"""initial

Revision ID: b8a7ff77da5a
Revises: 20260511_legacy_baseline
Create Date: 2026-05-12 02:21:12.268857

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8a7ff77da5a'
down_revision: Union[str, Sequence[str], None] = "20260511_legacy_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guard adoption of unversioned databases where this column already exists.
    if context.is_offline_mode() or "discount_percentage" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("promotions")}:
        op.add_column('promotions', sa.Column('discount_percentage', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise RuntimeError("The historical discount column may contain adopted data; downgrading below b8a7ff77da5a requires restoring a verified backup.")
