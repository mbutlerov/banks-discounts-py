"""Add stable merchant identities, shared rules and explicit location membership.

Legacy source observations and URLs are retained. Data normalization is an
explicit, idempotent operation rather than inference during schema migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261005_merchant_membership"
down_revision = "20261003_offers_and_ingestion"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.add_column("merchant_locations", sa.Column("identity_key", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_merchant_locations_identity", "merchant_locations", ["merchant_group_id", "identity_key"],
    )
    op.create_table(
        "merchant_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(500), nullable=False),
        sa.Column("merchant_group_id", sa.Integer(), sa.ForeignKey("merchant_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_jsonb", JSONB, nullable=True),
        *timestamps(),
        sa.UniqueConstraint("namespace", "source_key", name="uq_merchant_aliases_source"),
    )
    op.create_index("ix_merchant_aliases_merchant_group_id", "merchant_aliases", ["merchant_group_id"])
    op.create_table(
        "merchant_location_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(500), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("merchant_locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_jsonb", JSONB, nullable=True),
        *timestamps(),
        sa.UniqueConstraint("namespace", "source_key", name="uq_merchant_location_aliases_source"),
    )
    op.create_index("ix_merchant_location_aliases_location_id", "merchant_location_aliases", ["location_id"])
    op.create_table(
        "merchant_offer_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_group_id", sa.Integer(), sa.ForeignKey("merchant_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bank_id", sa.Integer(), sa.ForeignKey("banks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_key", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("data_jsonb", JSONB, nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "merchant_group_id", "bank_id", "context_key", "fingerprint", name="uq_merchant_offer_rules_identity",
        ),
    )
    op.create_index("ix_merchant_offer_rules_merchant_group_id", "merchant_offer_rules", ["merchant_group_id"])
    op.create_index("ix_merchant_offer_rules_bank_id", "merchant_offer_rules", ["bank_id"])
    op.add_column("promotion_offers", sa.Column("merchant_group_id", sa.Integer(), nullable=True))
    op.add_column("promotion_offers", sa.Column("rule_id", sa.Integer(), nullable=True))
    op.add_column("promotion_offers", sa.Column("location_scope", sa.String(20), nullable=False, server_default="unknown"))
    op.create_foreign_key(
        "fk_promotion_offers_merchant_group", "promotion_offers", "merchant_groups",
        ["merchant_group_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_promotion_offers_rule", "promotion_offers", "merchant_offer_rules",
        ["rule_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_promotion_offers_merchant_group_id", "promotion_offers", ["merchant_group_id"])
    op.create_index("ix_promotion_offers_rule_id", "promotion_offers", ["rule_id"])
    op.create_check_constraint(
        "ck_promotion_offers_location_scope", "promotion_offers",
        "location_scope IN ('unknown', 'specified', 'all')",
    )
    op.create_table(
        "offer_locations",
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("promotion_offers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("merchant_locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("publication", sa.String(20), nullable=False, server_default="confirmed"),
        sa.Column("data_jsonb", JSONB, nullable=False),
        *timestamps(),
        sa.CheckConstraint("publication IN ('confirmed', 'pending', 'retired')", name="ck_offer_locations_publication"),
    )
    op.create_index("ix_offer_locations_location_id", "offer_locations", ["location_id"])


def downgrade():
    op.drop_table("offer_locations")
    op.drop_constraint("ck_promotion_offers_location_scope", "promotion_offers", type_="check")
    op.drop_index("ix_promotion_offers_rule_id", table_name="promotion_offers")
    op.drop_index("ix_promotion_offers_merchant_group_id", table_name="promotion_offers")
    op.drop_constraint("fk_promotion_offers_rule", "promotion_offers", type_="foreignkey")
    op.drop_constraint("fk_promotion_offers_merchant_group", "promotion_offers", type_="foreignkey")
    for column in ("location_scope", "rule_id", "merchant_group_id"):
        op.drop_column("promotion_offers", column)
    op.drop_table("merchant_offer_rules")
    op.drop_table("merchant_location_aliases")
    op.drop_table("merchant_aliases")
    op.drop_constraint("uq_merchant_locations_identity", "merchant_locations", type_="unique")
    op.drop_column("merchant_locations", "identity_key")
