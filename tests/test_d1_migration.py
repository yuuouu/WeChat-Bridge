from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_d1_analytics.sql"


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    return connection


def test_migration_creates_expected_tables():
    connection = _database()
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    assert [row[0] for row in rows] == [
        "daily_counters",
        "legacy_telemetry",
        "telemetry_daily",
        "telemetry_events",
    ]


def test_daily_heartbeat_primary_key_deduplicates_retries():
    connection = _database()
    statement = """
        INSERT INTO telemetry_daily (day, daily_id, week, weekly_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(day, daily_id) DO NOTHING
    """
    values = ("2026-10-07", "daily", "2026-W41", "weekly")
    connection.execute(statement, values)
    connection.execute(statement, values)
    assert connection.execute("SELECT COUNT(*) FROM telemetry_daily").fetchone()[0] == 1


def test_daily_counter_upsert_is_additive():
    connection = _database()
    statement = """
        INSERT INTO daily_counters (day, counter_type, total)
        VALUES (?, ?, 1)
        ON CONFLICT(day, counter_type)
        DO UPDATE SET total = total + 1
    """
    connection.execute(statement, ("2026-10-07", "ping"))
    connection.execute(statement, ("2026-10-07", "ping"))
    assert (
        connection.execute(
            "SELECT total FROM daily_counters WHERE day = ? AND counter_type = ?",
            ("2026-10-07", "ping"),
        ).fetchone()[0]
        == 2
    )


def test_worker_uses_d1_for_telemetry_and_stats(tmp_path):
    node = which("node")
    if node is None:
        return

    worker_path = tmp_path / "worker.mjs"
    worker_path.write_text(
        (ROOT / "docs" / "assets" / "cf-worker-dl-proxy.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script_path = tmp_path / "verify.mjs"
    script_path.write_text(
        f"""
        import worker from {worker_path.as_uri()!r};

        const writes = [];
        class Statement {{
          constructor(sql) {{ this.sql = sql; this.params = []; }}
          bind(...params) {{ this.params = params; return this; }}
          async run() {{
            writes.push({{ sql: this.sql, params: this.params }});
            return {{ results: [] }};
          }}
        }}
        const db = {{
          prepare(sql) {{ return new Statement(sql); }},
          async batch(statements) {{
            if (statements.length === 5) {{
              return [
                {{ results: [] }}, {{ results: [] }}, {{ results: [] }},
                {{ results: [] }}, {{ results: [{{ total: 0 }}] }},
              ];
            }}
            return statements.map(() => ({{ results: [] }}));
          }},
        }};

        const payload = {{
          schema: 2,
          event: 'heartbeat',
          daily_id: 'a'.repeat(32),
          weekly_id: 'b'.repeat(32),
          v: '1.4.0',
          os: 'linux',
          features: ['docker'],
        }};
        const telemetry = await worker.fetch(new Request('https://example.test/telemetry', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }}), {{ ANALYTICS_DB: db }}, {{}});
        if (telemetry.status !== 200) throw new Error(`telemetry status ${{telemetry.status}}`);
        if (writes.length !== 1 || !writes[0].sql.includes('telemetry_daily')) {{
          throw new Error('heartbeat was not written to D1');
        }}

        const missing = await worker.fetch(new Request('https://example.test/telemetry', {{
          method: 'POST', body: JSON.stringify(payload),
        }}), {{}}, {{}});
        if (missing.status !== 503) throw new Error(`missing binding status ${{missing.status}}`);

        const stats = await worker.fetch(new Request('https://example.test/stats', {{
          headers: {{ Authorization: 'Bearer secret' }},
        }}), {{ ANALYTICS_DB: db, STATS_TOKEN: 'secret' }}, {{}});
        if (stats.status !== 200) throw new Error(`stats status ${{stats.status}}`);
        const statsBody = await stats.json();
        if (Object.keys(statsBody.daily).length !== 7) throw new Error('stats did not return seven days');
        """,
        encoding="utf-8",
    )
    subprocess.run([node, str(script_path)], check=True, capture_output=True, text=True)
