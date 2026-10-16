"""Resource-owned migration history; explicit invocation only."""

from alembic import context
from schedence_resource.models import metadata


def configure(**connection):
    context.configure(**connection, target_metadata=metadata, version_table_schema="resource",
        include_schemas=True, include_name=lambda name, kind, parents:
            name == "resource" if kind == "schema" else True)
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS resource")
        context.run_migrations()


if context.is_offline_mode():
    configure(url="postgresql+psycopg://", literal_binds=True)
elif context.config.attributes.get("connection") is not None:
    configure(connection=context.config.attributes["connection"])
else:
    from schedence_db import create_db_engine
    from schedence_db.runtime import run

    async def migrate():
        engine = create_db_engine(direct=True)
        try:
            async with engine.connect() as connection:
                await connection.run_sync(lambda sync: configure(connection=sync))
        finally:
            await engine.dispose()

    run(migrate())
