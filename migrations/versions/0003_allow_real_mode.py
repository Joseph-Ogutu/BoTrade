"""allow mode 'real' in order_intents and orders (real-account path, 2026-11-06)

Revision ID: 0003
Revises: 0002

This is the deliberate migration that HANDBOOK 11.4 and migration 0001 call for. It removes ONE of the layers that kept real mode
unreachable (the database CHECK mode = 'demo'); the other layers (build lock, owner approval file, signed checklist, endpoint allowlist,
real-only OTP provider, connect-time account verification, risk rule 2, the executor's pre-buy re-check, hard real limits) remain.
The column CHECK (mode IN ('demo','real')) from 0001 stays, so any other value is still refused.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE order_intents DROP CONSTRAINT IF EXISTS order_intents_demo_only")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_demo_only")


def downgrade() -> None:
    # Refuses to go back while any real row exists: dropping real history by accident is not an option.
    op.execute("ALTER TABLE order_intents ADD CONSTRAINT order_intents_demo_only CHECK (mode = 'demo')")
    op.execute("ALTER TABLE orders ADD CONSTRAINT orders_demo_only CHECK (mode = 'demo')")
