"""Offline mode is credential-free; online mode is explicitly invoked only."""

from alembic import context

from schedence_teaching.persistence.models import Base


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == "teaching"
    if type_ == "table":
        # Durable queue constraints are maintained in frozen revision SQL. Its
        # separate runtime mapping must never be autogen-dropped as "unknown".
        return parent_names.get("schema_name") == "teaching" and name not in {
            "generation_jobs", "generation_job_attempts"}
    return True


def configure(**connection):
    context.configure(**connection, target_metadata=Base.metadata,
        version_table_schema="teaching", include_schemas=True,
        include_name=include_name, compare_type=True)
    with context.begin_transaction():
        # The Alembic version table needs the owned schema before revision one.
        context.execute("CREATE SCHEMA IF NOT EXISTS teaching")
        context.run_migrations()


if context.is_offline_mode():
    # Dialect only. No environment reads, passwords, hostname or database access.
    configure(url="postgresql+psycopg://", literal_binds=True, dialect_opts={"paramstyle": "named"})
else:
    supplied_connection = context.config.attributes.get("connection")
    # Never reached by the offline checks used in Step 12B.1.
    from schedence_db import create_db_engine
    from schedence_db.runtime import run

    async def migrate():
        engine = create_db_engine(direct=True)
        try:
            async with engine.connect() as connection:
                await connection.run_sync(lambda sync_connection: configure(connection=sync_connection))
        finally:
            await engine.dispose()

    if supplied_connection is not None:
        configure(connection=supplied_connection)
    else:
        run(migrate())
