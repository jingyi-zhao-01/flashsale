CREATE SCHEMA IF NOT EXISTS user_service;
CREATE SCHEMA IF NOT EXISTS product_service;
CREATE SCHEMA IF NOT EXISTS order_service;

CREATE TABLE IF NOT EXISTS user_service.users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_service.products (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE IF NOT EXISTS product_service.reservations (
    reservation_id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES product_service.products(id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS reservations_status_expires_at_idx
ON product_service.reservations (status, expires_at, reservation_id);

ALTER TABLE product_service.reservations
ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12, 2) NULL;

CREATE TABLE IF NOT EXISTS order_service.orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_amount NUMERIC(12, 2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payment_status TEXT NOT NULL DEFAULT 'pending',
    idempotency_key TEXT NULL,
    reservation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    items_json JSONB NOT NULL
);

ALTER TABLE order_service.orders
ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE order_service.orders
ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE order_service.orders
ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL;

ALTER TABLE order_service.orders
ADD COLUMN IF NOT EXISTS reservation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_key_idx
ON order_service.orders (idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS orders_pending_created_at_idx
ON order_service.orders (status, created_at, id);

CREATE TABLE IF NOT EXISTS order_service.order_terminalization_tasks (
    task_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES order_service.orders(id) ON DELETE CASCADE,
    reservation_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS order_terminalization_tasks_ready_idx
ON order_service.order_terminalization_tasks (status, available_at, task_id);

CREATE TABLE IF NOT EXISTS order_service.order_terminalization_task_events (
    event_id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES order_service.order_terminalization_tasks(task_id) ON DELETE CASCADE,
    order_id BIGINT NOT NULL REFERENCES order_service.orders(id) ON DELETE CASCADE,
    reservation_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    event_type TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS order_terminalization_task_events_lookup_idx
ON order_service.order_terminalization_task_events (occurred_at, event_type, action, task_id);
