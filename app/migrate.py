from __future__ import annotations

from sqlalchemy import inspect, text


def _add_pg_enum_value(engine, enum_name: str, value: str) -> None:
    if engine.dialect.name != "postgresql":
        return
    stmt = text(
        f"""
        DO $$
        BEGIN
            ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}';
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def ensure_schema(engine) -> None:
    """Lightweight schema upgrades for MVP (adds new columns if missing).

    We intentionally avoid a full migration framework to keep setup minimal.
    Safe for SQLite/Postgres: uses ALTER TABLE ADD COLUMN when needed.
    """

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    if "keys" in tables:
        cols = {c["name"] for c in insp.get_columns("keys")}
        statements: list[str] = []

        if "xui_inbound_id" not in cols:
            statements.append("ALTER TABLE keys ADD COLUMN xui_inbound_id INTEGER")
        if "xui_client_id" not in cols:
            statements.append("ALTER TABLE keys ADD COLUMN xui_client_id VARCHAR(64)")
        if "xui_email" not in cols:
            statements.append("ALTER TABLE keys ADD COLUMN xui_email VARCHAR(128)")
        if "xui_client_json" not in cols:
            statements.append("ALTER TABLE keys ADD COLUMN xui_client_json TEXT")

        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))

    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        statements = []
        if "last_activity_at" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN last_activity_at TIMESTAMP")
        if "last_paid_at" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN last_paid_at TIMESTAMP")
        if "is_frozen" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN is_frozen BOOLEAN DEFAULT FALSE")
        if "current_key_chat_id" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN current_key_chat_id BIGINT")
        if "current_key_message_id" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN current_key_message_id INTEGER")
        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))

    if "keys" in tables:
        cols = {c["name"] for c in insp.get_columns("keys")}
        statements = []
        if "last_config_updated_at" not in cols:
            statements.append("ALTER TABLE keys ADD COLUMN last_config_updated_at TIMESTAMP")
        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))

    if "payments" in tables:
        cols = {c["name"] for c in insp.get_columns("payments")}
        statements = []
        if "processed_at" not in cols:
            statements.append("ALTER TABLE payments ADD COLUMN processed_at TIMESTAMP")
        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))

    _add_pg_enum_value(engine, "paymentprovider", "platega")
