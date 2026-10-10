"""Shared async PostgreSQL utilities for Schedence services.

This package owns *how* services connect, not *what* they store: it provides
configuration, engine, session and health-check helpers. Each service defines
its own models, schema and migrations.
"""

import logging

from schedence_db.config import DatabaseSettings, find_env_file, load_settings, normalize_database_url
from schedence_db.connection import create_db_engine, dispose_engine
from schedence_db.exceptions import DatabaseConfigurationError, SchedenceDBError, describe_db_error
from schedence_db.health import DatabaseHealth, check_database
from schedence_db.session import SessionFactory, create_session_factory, session_dependency, session_scope

__version__ = "0.1.0"

__all__ = [
    "DatabaseConfigurationError",
    "DatabaseHealth",
    "DatabaseSettings",
    "SchedenceDBError",
    "SessionFactory",
    "check_database",
    "create_db_engine",
    "create_session_factory",
    "describe_db_error",
    "dispose_engine",
    "find_env_file",
    "load_settings",
    "normalize_database_url",
    "session_dependency",
    "session_scope",
]

# Library convention: emit nothing unless the application configures logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())
