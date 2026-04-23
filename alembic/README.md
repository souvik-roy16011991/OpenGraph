# Alembic migrations

OpenGraph uses Alembic for versioned schema changes. The legacy idempotent
`ALTER TABLE IF NOT EXISTS` bootstrap in `src/infra/db.py::init_db` still
runs on every startup — that path is fine for dev but not safe for
1M-row alterations in prod.

## First-time adoption on an existing database

If Neon already holds the current schema (everything `init_db` creates),
stamp the DB at HEAD so Alembic doesn't try to re-create tables:

```bash
alembic stamp head
```

That writes a row into `alembic_version` and skips over the existing
schema. From there forward, every schema change is a migration.

## Creating a new migration

```bash
alembic revision --autogenerate -m "add_my_column"
# review the generated versions/*.py, tweak the batching / defaults
alembic upgrade head
```

Autogenerate picks up new/changed columns, indexes and constraints from
`src.infra.db_models.Base.metadata`. It will *not* detect:

- Server-side DEFAULT changes that aren't part of the ORM default.
- Data backfills (you have to write them by hand in `upgrade()`).
- Check constraint wording changes.

Always re-read the diff before applying.

## Safe patterns for 1M-row tables

Adding a NOT NULL column with a default:

```python
def upgrade() -> None:
    op.add_column("workspaces", sa.Column("plan_tier", sa.String(16), nullable=True))
    op.execute("UPDATE workspaces SET plan_tier = 'trial' WHERE plan_tier IS NULL")
    op.alter_column("workspaces", "plan_tier", nullable=False)
```

Three small transactions beat one giant ALTER that locks writes for
minutes.

Adding an index on a hot table:

```python
def upgrade() -> None:
    op.create_index(
        "ix_chat_messages_session",
        "chat_messages",
        ["session_id"],
        postgresql_concurrently=True,
    )
```

`CONCURRENTLY` keeps writes unblocked; the trade-off is you can't wrap
it in a transaction (Alembic handles this automatically when the flag
is set).

## Downgrades

```bash
alembic downgrade -1
```

Write `downgrade()` to drop whatever `upgrade()` added. For destructive
migrations (data deletion, column drops) leave a comment making the
point of no return obvious.
