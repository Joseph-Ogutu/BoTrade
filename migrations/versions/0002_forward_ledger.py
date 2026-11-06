"""forward test ledger (Amendment 5): append-only shadow outcomes and discards

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# No colon characters in this SQL: Alembic passes strings through SQLAlchemy text(), which reads ":name" as a bind.
SQL = """
CREATE TABLE shadow_outcomes (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  variant text NOT NULL,
  strategy_id text NOT NULL,
  strategy_version text NOT NULL,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('CALL', 'PUT')),
  signal_epoch bigint NOT NULL,
  entry_epoch bigint NOT NULL,
  exit_epoch bigint NOT NULL,
  entry_quote numeric NOT NULL,
  exit_quote numeric NOT NULL,
  won boolean NOT NULL,
  CONSTRAINT shadow_outcomes_order CHECK (signal_epoch < entry_epoch AND entry_epoch < exit_epoch),
  CONSTRAINT shadow_outcomes_unique UNIQUE (variant, signal_epoch)
);
CREATE INDEX idx_shadow_outcomes_variant ON shadow_outcomes (variant, id);

CREATE TABLE shadow_discards (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  variant text NOT NULL,
  signal_epoch bigint NOT NULL,
  reason text NOT NULL CHECK (reason IN ('gap', 'disconnect', 'restart'))
);
CREATE INDEX idx_shadow_discards_variant ON shadow_discards (variant);

CREATE FUNCTION forward_ledger_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'the forward test ledger is append-only (Amendment 5)'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER shadow_outcomes_no_update BEFORE UPDATE OR DELETE ON shadow_outcomes
  FOR EACH ROW EXECUTE FUNCTION forward_ledger_immutable();
CREATE TRIGGER shadow_outcomes_no_truncate BEFORE TRUNCATE ON shadow_outcomes
  FOR EACH STATEMENT EXECUTE FUNCTION forward_ledger_immutable();
CREATE TRIGGER shadow_discards_no_update BEFORE UPDATE OR DELETE ON shadow_discards
  FOR EACH ROW EXECUTE FUNCTION forward_ledger_immutable();
CREATE TRIGGER shadow_discards_no_truncate BEFORE TRUNCATE ON shadow_discards
  FOR EACH STATEMENT EXECUTE FUNCTION forward_ledger_immutable();
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    # DROP TABLE is a deliberate schema change and is not blocked by the row and truncate triggers.
    op.execute("DROP TABLE IF EXISTS shadow_discards CASCADE")
    op.execute("DROP TABLE IF EXISTS shadow_outcomes CASCADE")
    op.execute("DROP FUNCTION IF EXISTS forward_ledger_immutable() CASCADE")
