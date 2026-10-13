"""Alembic environment for the Examination Service.

* Only the ``examinations`` schema is ever inspected or changed.
* ``alembic_version`` lives in ``examinations``, so this service's revision
  history never collides with other services.
* The schema itself is NOT created here. It must exist before the first
  migration (it is created once, manually, after review; see the service README).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from schedence_db import load_settings

from app.db import seed_models  # noqa: F401  (registers the tables on Base.metadata)
from app.db.base import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Default: DIRECT_DATABASE_URL (else DATABASE_URL). `alembic -x pooled=true ...`
# uses the pooled DATABASE_URL instead, e.g. while no direct URL is configured.
use_pooled = context.get_x_argument(as_dictionary=True).get("pooled", "").lower() == "true"
url = load_settings().get_url(direct=not use_pooled)


def include_name(name, type_, parent_names):
    # Autogenerate only ever looks at this service's own schema.
    return name == SCHEMA if type_ == "schema" else True


migration_options = dict(
    target_metadata=target_metadata,
    version_table_schema=SCHEMA,
    include_schemas=True,
    include_name=include_name,
)


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, **migration_options)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Synchronous psycopg engine: migrations are short scripts, and sync mode
    # needs no event-loop changes on Windows.
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, **migration_options)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
