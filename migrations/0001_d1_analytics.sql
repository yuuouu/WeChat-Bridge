CREATE TABLE IF NOT EXISTS telemetry_daily (
    day TEXT NOT NULL,
    daily_id TEXT NOT NULL,
    week TEXT NOT NULL,
    weekly_id TEXT NOT NULL,
    version TEXT,
    os TEXT,
    arch TEXT,
    python_version TEXT,
    deploy_mode TEXT,
    uptime_bucket TEXT,
    accounts_bucket TEXT,
    plugins_bucket TEXT,
    ai_provider TEXT,
    webhook_enabled TEXT,
    features TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (day, daily_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS telemetry_events (
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    day TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (event_type, event_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS legacy_telemetry (
    event_id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    version TEXT,
    previous_version TEXT,
    os TEXT,
    arch TEXT,
    python_version TEXT,
    deploy_mode TEXT,
    uptime_days TEXT,
    accounts TEXT,
    ai_provider TEXT,
    plugins_count TEXT,
    webhook_enabled TEXT,
    features TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_counters (
    day TEXT NOT NULL,
    counter_type TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0 CHECK (total >= 0),
    PRIMARY KEY (day, counter_type)
) WITHOUT ROWID;
