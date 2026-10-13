"""Alembic environment for the ``identity`` schema only.

* URL: ``schedence_db.load_settings().get_url(direct=True)`` - Neon's direct
  endpoint when DIRECT_DATABASE_URL is set, otherwise DATABASE_URL. Passed as a
  SQLAlchemy URL object, never written to alembic.ini.
* ``alembic_version`` lives in ``identity``, so this service's revision history
  never collides with other services (e.g. ``examinations.alembic_version``).
* Autogenerate only ever looks at the ``identity`` schema.
* Synchronous psycopg engine: no event-loop changes are needed on Windows.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text

from alembic import context
from app.models import SCHEMA, Base
from schedence_db import load_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    return name == SCHEMA if type_ == "schema" else True


MIGRATION_OPTIONS = dict(
    target_metadata=target_metadata,
    version_table_schema=SCHEMA,
    include_schemas=True,
    include_name=include_name,
    compare_type=True,
)


def run_migrations_offline() -> None:
    context.configure(url=load_settings().get_url(direct=True), literal_binds=True, **MIGRATION_OPTIONS)
    with context.begin_transaction():
        context.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(load_settings().get_url(direct=True), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            # The version table lives in this schema, so it must exist before Alembic touches it.
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            connection.commit()
            context.configure(connection=connection, **MIGRATION_OPTIONS)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
