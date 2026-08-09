-- PrismPipe cold store (also auto-applied by PostgresStorage)
CREATE TABLE IF NOT EXISTS prismpipe_kv (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS prismpipe_kv_key_prefix_idx
    ON prismpipe_kv (key text_pattern_ops);
