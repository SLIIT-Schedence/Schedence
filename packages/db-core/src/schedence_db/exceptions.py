"""Exception types and credential-safe error descriptions.

Raw driver errors can contain host names, user names or (in rare cases)
fragments of a connection string. Nothing in this package surfaces a raw
database error message; it surfaces the short, fixed descriptions produced
by :func:`describe_db_error` instead.
"""

from __future__ import annotations


class SchedenceDBError(Exception):
    """Base class for errors raised by ``schedence_db``."""


class DatabaseConfigurationError(SchedenceDBError, ValueError):
    """Database configuration is missing or invalid.

    The message names the offending setting but never includes its value.
    """


# (substrings to look for, safe description). Checked in order, first match wins.
_ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("proactoreventloop",),
        "incompatible event loop: psycopg async needs a selector event loop on Windows",
    ),
    (
        ("password authentication failed", "authentication failed"),
        "authentication failed: check the database user name and password",
    ),
    (
        (
            "could not translate host name",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo failed",
            "no such host is known",
            "temporary failure in name resolution",
        ),
        "database host could not be resolved: check the host name and your network/DNS",
    ),
    (
        ("timeout expired", "timed out"),
        "connection timed out: the database may be unreachable or still waking up",
    ),
    (
        ("connection refused", "actively refused"),
        "connection refused: check the host and port",
    ),
    (
        ("too many connections", "remaining connection slots"),
        "too many connections: use the pooled URL or reduce pool sizes",
    ),
    (
        ("endpoint",),
        "Neon endpoint error: check the endpoint ID in the host name and that the compute is enabled",
    ),
    (
        ("ssl", "certificate", "channel binding", "channel_binding"),
        "SSL/TLS negotiation failed: keep sslmode=require and check the connection URL",
    ),
    (
        ("does not exist",),
        "the database, role or object does not exist",
    ),
)


def describe_db_error(exc: BaseException) -> str:
    """Return a short description of ``exc`` that is safe to log or return.

    The raw message is only *inspected*; it is never part of the result.
    """
    if isinstance(exc, TimeoutError):
        return "connection timed out: the database may be unreachable or still waking up"

    # SQLAlchemy wraps driver exceptions; ``orig`` is the psycopg exception.
    original = getattr(exc, "orig", None) or exc
    haystack = f"{exc} {original}".lower()
    for needles, description in _ERROR_HINTS:
        if any(needle in haystack for needle in needles):
            return description
    return f"database error ({type(original).__name__})"


def redact(text: str, *secrets: str | None) -> str:
    """Replace every occurrence of each non-empty secret in ``text`` with ``***``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
