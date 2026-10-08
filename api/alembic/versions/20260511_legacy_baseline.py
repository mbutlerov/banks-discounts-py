"""Frozen legacy schema, before the original discount-percentage migration.

Existing databases are adopted table by table without replacing their data.
No application model is imported: future ORM changes cannot alter this baseline.
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260511_legacy_baseline"
down_revision = None
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def identity():
    return sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)


def slug(size):
    return sa.Column("slug", sa.String(size), nullable=False)


def active():
    return sa.Column("is_active", sa.Boolean(), nullable=False)


def definitions():
    return [
        ("banks", [identity(), slug(100), sa.Column("name", sa.String(150), nullable=False), sa.Column("website_url", sa.String(500)), sa.Column("country_code", sa.String(2), nullable=False), active(), *timestamps()], ["slug"]),
        ("card_brands", [identity(), slug(100), sa.Column("name", sa.String(100), nullable=False), *timestamps()], ["slug"]),
        ("categories", [identity(), slug(100), sa.Column("name", sa.String(150), nullable=False), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL")), active(), *timestamps()], ["slug", "parent_id"]),
        ("merchant_groups", [identity(), slug(150), sa.Column("name", sa.String(150), nullable=False), sa.Column("legal_name", sa.String(200)), sa.Column("website_url", sa.String(500)), active(), sa.Column("metadata_jsonb", JSONB), *timestamps()], ["slug"]),
        ("merchant_locations", [identity(), sa.Column("merchant_group_id", sa.Integer(), sa.ForeignKey("merchant_groups.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(150), nullable=False), sa.Column("address", sa.String(255)), sa.Column("city", sa.String(100)), sa.Column("state_region", sa.String(100)), sa.Column("lat", sa.Numeric(10, 7)), sa.Column("lng", sa.Numeric(10, 7)), active(), sa.Column("metadata_jsonb", JSONB), *timestamps()], ["merchant_group_id"]),
        ("card_products", [identity(), sa.Column("bank_id", sa.Integer(), sa.ForeignKey("banks.id", ondelete="CASCADE"), nullable=False), sa.Column("card_brand_id", sa.Integer(), sa.ForeignKey("card_brands.id", ondelete="SET NULL")), sa.Column("name", sa.String(150), nullable=False), sa.Column("product_type", sa.String(50), nullable=False), sa.Column("segment", sa.String(100)), active(), sa.Column("metadata_jsonb", JSONB), *timestamps()], ["bank_id", "card_brand_id"]),
        ("campaigns", [identity(), sa.Column("bank_id", sa.Integer(), sa.ForeignKey("banks.id", ondelete="CASCADE"), nullable=False), slug(150), sa.Column("name", sa.String(150), nullable=False), sa.Column("description", sa.Text()), sa.Column("start_date", sa.Date()), sa.Column("end_date", sa.Date()), sa.Column("campaign_type", sa.String(50)), active(), sa.Column("metadata_jsonb", JSONB), *timestamps()], ["slug", "bank_id"]),
        ("promotions", [identity(), sa.Column("bank_id", sa.Integer(), sa.ForeignKey("banks.id", ondelete="CASCADE"), nullable=False), sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="SET NULL")), sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL")), slug(180), sa.Column("title", sa.String(180), nullable=False), sa.Column("short_description", sa.String(255)), sa.Column("description", sa.Text()), sa.Column("benefit_type", sa.String(50)), sa.Column("mechanic_type", sa.String(50)), sa.Column("start_date", sa.Date()), sa.Column("end_date", sa.Date()), sa.Column("status", sa.String(50), nullable=False), sa.Column("applies_to_all_locations", sa.Boolean(), nullable=False), sa.Column("is_cumulative", sa.Boolean()), sa.Column("terms_summary", sa.Text()), sa.Column("metadata_jsonb", JSONB), *timestamps()], ["slug", "bank_id", "campaign_id", "category_id"]),
    ]


def upgrade():
    existing = set() if context.is_offline_mode() else set(sa.inspect(op.get_bind()).get_table_names())
    for name, columns, indices in definitions():
        if name in existing:
            continue
        op.create_table(name, *columns)
        for column in indices:
            op.create_index(f"ix_{name}_{column}", name, [column], unique=column == "slug")


def downgrade():
    # These tables may have been adopted, not created by this revision. Dropping
    # them would destroy the original database. Restore a backup for this step.
    raise RuntimeError("Legacy baseline downgrade is disabled to preserve adopted data; restore a verified backup instead.")
