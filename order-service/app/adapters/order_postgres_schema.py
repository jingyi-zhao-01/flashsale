from typing import Any


def ensure_order_tables(cur: Any) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            total_amount NUMERIC(12, 2) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_status TEXT NOT NULL DEFAULT 'pending',
            idempotency_key TEXT NULL,
            reservation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            items_json JSONB NOT NULL
        )
        """)
    cur.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'
        """)
    cur.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending'
        """)
    cur.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL
        """)
    cur.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS reservation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
        """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_key_idx
        ON orders (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS orders_pending_created_at_idx
        ON orders (status, created_at, id)
        """)
