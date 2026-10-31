"""initial schema from HANDBOOK section 8

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATES = ("INTENT_CREATED", "RISK_REJECTED", "RISK_APPROVED", "PROPOSAL_REQUESTED", "PROPOSAL_REJECTED",
          "BUY_SENT", "OPEN", "BUY_UNKNOWN", "NOT_PLACED", "MANUAL_REVIEW", "SETTLED", "SOLD")
STATE_LIST = ", ".join(f"'{s}'" for s in STATES)

# No colon characters in this SQL: Alembic passes strings through SQLAlchemy text(), which reads ":name" as a bind.
SQL = f"""
CREATE TABLE ticks (
  symbol      text        NOT NULL,
  epoch       bigint      NOT NULL,
  quote       numeric     NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, epoch)
);

CREATE TABLE candles (
  symbol text NOT NULL, granularity_s int NOT NULL, open_epoch bigint NOT NULL,
  o numeric NOT NULL, h numeric NOT NULL, l numeric NOT NULL, c numeric NOT NULL,
  PRIMARY KEY (symbol, granularity_s, open_epoch)
);

CREATE TABLE config_versions (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL CHECK (kind IN ('risk', 'strategy', 'system')),
  content jsonb NOT NULL,
  content_hash text NOT NULL UNIQUE
);

CREATE TABLE signals (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  strategy_id text NOT NULL, strategy_version text NOT NULL,
  symbol text NOT NULL, direction text NOT NULL,
  features jsonb NOT NULL,
  data_cutoff_epoch bigint NOT NULL
);

CREATE TABLE order_intents (
  id uuid PRIMARY KEY,
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  signal_id uuid REFERENCES signals(id),
  mode text NOT NULL CHECK (mode IN ('demo','real')),
  account_id text NOT NULL,
  symbol text NOT NULL, contract_type text NOT NULL,
  stake numeric NOT NULL CHECK (stake > 0),
  duration int, duration_unit text,
  state text NOT NULL CHECK (state IN ({STATE_LIST}))
);
-- Real mode is unreachable in this build. Removing this constraint needs a deliberate migration (HANDBOOK 11.4).
ALTER TABLE order_intents ADD CONSTRAINT order_intents_demo_only CHECK (mode = 'demo');

CREATE TABLE risk_decisions (
  id bigserial PRIMARY KEY,
  intent_id uuid NOT NULL UNIQUE REFERENCES order_intents(id),
  decided_at timestamptz NOT NULL DEFAULT now(),
  approved boolean NOT NULL,
  rule_results jsonb NOT NULL,
  state_snapshot jsonb NOT NULL,
  config_version_id bigint NOT NULL REFERENCES config_versions(id)
);

CREATE TABLE orders (
  id uuid PRIMARY KEY,
  intent_id uuid NOT NULL UNIQUE REFERENCES order_intents(id),
  deriv_contract_id bigint UNIQUE,
  deriv_transaction_id bigint,
  buy_price numeric, payout numeric,
  state text NOT NULL CHECK (state IN ({STATE_LIST})),
  opened_at timestamptz, settled_at timestamptz,
  profit numeric,
  mode text NOT NULL, account_id text NOT NULL
);
ALTER TABLE orders ADD CONSTRAINT orders_demo_only CHECK (mode = 'demo');
CREATE INDEX idx_orders_state ON orders (state);

-- Implied by the ER diagram (orders to order_events). Every state transition is recorded here.
CREATE TABLE order_events (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  intent_id uuid NOT NULL REFERENCES order_intents(id),
  from_state text,
  to_state text NOT NULL,
  event text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX idx_order_events_intent ON order_events (intent_id, id);

CREATE TABLE audit_events (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  event_type text NOT NULL,
  ref_id text,
  req_id bigint,
  payload jsonb NOT NULL
);
CREATE INDEX idx_audit_events_ref ON audit_events (ref_id);

CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
-- Row triggers do not fire for TRUNCATE; without this a TRUNCATE would silently empty the audit log.
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable();

CREATE TABLE balance_snapshots (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  mode text NOT NULL, account_id text NOT NULL, balance numeric NOT NULL, currency text NOT NULL
);

CREATE TABLE reconciliation_runs (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  trigger text NOT NULL CHECK (trigger IN ('periodic', 'reconnect', 'startup', 'manual')),
  local_open int, remote_open int,
  status text NOT NULL CHECK (status IN ('clean', 'discrepancy')),
  discrepancies jsonb
);

CREATE TABLE kill_switch_events (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  action text NOT NULL CHECK (action IN ('engage','release')),
  source text NOT NULL, reason text
);

CREATE TABLE backtest_runs (
  id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
  strategy_id text NOT NULL, strategy_version text NOT NULL,
  params jsonb NOT NULL, data_range jsonb NOT NULL,
  split text NOT NULL CHECK (split IN ('train', 'validation', 'holdout', 'forward', 'simulated')),
  payout_model jsonb NOT NULL,
  git_commit text NOT NULL,
  metrics jsonb NOT NULL
);

CREATE TABLE backtest_trades (
  run_id uuid NOT NULL REFERENCES backtest_runs(id),
  seq int NOT NULL, entry_epoch bigint NOT NULL, exit_epoch bigint NOT NULL,
  direction text NOT NULL, stake numeric NOT NULL, win boolean NOT NULL, profit numeric NOT NULL,
  PRIMARY KEY (run_id, seq)
);
"""

TABLES = ("backtest_trades", "backtest_runs", "kill_switch_events", "reconciliation_runs", "balance_snapshots",
          "audit_events", "order_events", "orders", "risk_decisions", "order_intents", "signals", "config_versions",
          "candles", "ticks")


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    # DROP TABLE is not blocked by the row and truncate triggers; it is a deliberate schema change.
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable() CASCADE")
