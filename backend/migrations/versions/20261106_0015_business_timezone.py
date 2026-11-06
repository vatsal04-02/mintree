"""Business-timezone SQL functions (Asia/Kolkata)

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The zone is written out here on purpose: a migration is a frozen snapshot. tests/test_business_clock.py
    # asserts it equals app.core.clock.BUSINESS_TZ_NAME.
    op.execute("""CREATE FUNCTION business_date(ts timestamptz) RETURNS date
                  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date $$""")
    op.execute("""CREATE FUNCTION business_today() RETURNS date
                  LANGUAGE sql STABLE PARALLEL SAFE AS $$ SELECT business_date(now()) $$""")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS business_today()")
    op.execute("DROP FUNCTION IF EXISTS business_date(timestamptz)")
