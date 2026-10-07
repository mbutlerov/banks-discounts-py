"""Add canonical variants, occurrence index and ingestion audit without backfill inference."""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261003_offers_and_ingestion"
down_revision = "b8a7ff77da5a"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    columns = set() if offline else {c["name"] for c in inspector.get_columns("promotions")}
    for column in [
        sa.Column("source_key", sa.String(500)),
        sa.Column("publication", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", sa.String(36)),
    ]:
        if column.name not in columns:
            op.add_column("promotions", column)
    constraints = set() if offline else {c["name"] for c in inspector.get_unique_constraints("promotions")}
    if "uq_promotions_bank_source_key" not in constraints:
        op.create_unique_constraint("uq_promotions_bank_source_key", "promotions", ["bank_id", "source_key"])
    tables = set() if offline else set(inspector.get_table_names())
    if "scrape_runs" not in tables:
        op.create_table("scrape_runs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("bank_slug", sa.String(100), nullable=False), sa.Column("state", sa.String(20), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("counters_jsonb", JSONB, nullable=False), sa.Column("errors_jsonb", JSONB, nullable=False), sa.Column("adapter_version", sa.String(100)), sa.Column("pid", sa.Integer()))
        op.create_index("ix_scrape_runs_bank_slug", "scrape_runs", ["bank_slug"])
    if "source_documents" not in tables:
        op.create_table("source_documents", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("run_id", sa.String(36), sa.ForeignKey("scrape_runs.id", ondelete="SET NULL")), sa.Column("bank_slug", sa.String(100), nullable=False), sa.Column("url", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("mime_type", sa.String(150)), sa.Column("http_status", sa.Integer()), sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("snapshot_path", sa.Text(), nullable=False), sa.Column("text_path", sa.Text()), sa.Column("parser_version", sa.String(100)))
        for col in ("run_id", "bank_slug", "content_hash"):
            op.create_index(f"ix_source_documents_{col}", "source_documents", [col])
    if "promotion_offers" not in tables:
        op.create_table("promotion_offers", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("promotion_id", sa.Integer(), sa.ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False), sa.Column("source_key", sa.String(500)), sa.Column("key", sa.String(240), nullable=False), sa.Column("data_jsonb", JSONB, nullable=False), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("publication", sa.String(20), nullable=False, server_default="pending"), sa.Column("coverage_from", sa.Date()), sa.Column("coverage_until", sa.Date()), sa.Column("occurrences_version", sa.Integer()), *timestamps(), sa.UniqueConstraint("promotion_id", "key", name="uq_promotion_offers_key"))
        op.create_index("ix_promotion_offers_promotion_id", "promotion_offers", ["promotion_id"])
        op.create_index("ix_promotion_offers_publication", "promotion_offers", ["publication"])
    if "offer_occurrences" not in tables:
        op.create_table("offer_occurrences", sa.Column("offer_id", sa.Integer(), sa.ForeignKey("promotion_offers.id", ondelete="CASCADE"), primary_key=True), sa.Column("applies_on", sa.Date(), primary_key=True), sa.Column("rules_version", sa.Integer(), nullable=False))
        op.create_index("ix_offer_occurrences_date_offer", "offer_occurrences", ["applies_on", "offer_id"])
    if "promotion_overrides" not in tables:
        op.create_table("promotion_overrides", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("promotion_id", sa.Integer(), sa.ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False), sa.Column("offer_key", sa.String(240)), sa.Column("patch_jsonb", JSONB, nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("source_hash", sa.String(64)), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), *timestamps())
        op.create_index("ix_promotion_overrides_promotion_id", "promotion_overrides", ["promotion_id"])


def downgrade():
    for name in ("promotion_overrides", "offer_occurrences", "promotion_offers", "source_documents", "scrape_runs"):
        op.drop_table(name)
    op.drop_constraint("uq_promotions_bank_source_key", "promotions", type_="unique")
    for name in ("last_run_id", "last_verified_at", "last_seen_at", "publication", "source_key"):
        op.drop_column("promotions", name)
