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


def ensure_terminalization_tables(cur: Any) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_terminalization_tasks (
            task_id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            reservation_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS order_terminalization_tasks_ready_idx
        ON order_terminalization_tasks (status, available_at, task_id)
        """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_terminalization_task_events (
            event_id BIGSERIAL PRIMARY KEY,
            task_id BIGINT NOT NULL REFERENCES order_terminalization_tasks(task_id) ON DELETE CASCADE,
            order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            reservation_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            event_type TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS order_terminalization_task_events_lookup_idx
        ON order_terminalization_task_events (occurred_at, event_type, action, task_id)
        """)
