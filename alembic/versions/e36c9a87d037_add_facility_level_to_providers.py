"""add facility_level to providers

Revision ID: e36c9a87d037
Revises: ff70692b3b75
Create Date: 2026-03-07 12:21:54.151020

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e36c9a87d037"
down_revision: Union[str, Sequence[str], None] = "ff70692b3b75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

facility_level_enum = sa.Enum(
    "LEVEL_2", "LEVEL_3", "LEVEL_4", "LEVEL_5", "LEVEL_6", name="facility_level_enum"
)


def upgrade() -> None:
    facility_level_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "providers", sa.Column("facility_level", facility_level_enum, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("providers", "facility_level")
    facility_level_enum.drop(op.get_bind(), checkfirst=True)
