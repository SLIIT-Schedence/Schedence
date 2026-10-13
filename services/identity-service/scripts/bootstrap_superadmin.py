"""Bootstrap the first Super Admin. Operator-only; there is no HTTP equivalent.

Requires: database access (DIRECT_DATABASE_URL or DATABASE_URL), AWS credentials
allowed to call AdminGetUser on the pool (AWS_PROFILE), seeded roles, and a
Cognito user for the person that has completed first sign-in.

Usage (PowerShell, from services/identity-service):
    python -m scripts.bootstrap_superadmin --email admin@example.edu --institution-id <UUID>
    python -m scripts.bootstrap_superadmin --email admin@example.edu --institution-id <UUID> --provision --full-name "First Admin"
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import load_identity_settings
from app.integrations.cognito import CognitoError
from app.integrations.cognito.boto3_client import Boto3CognitoAdminClient
from app.repositories.base import PersistenceError
from app.services.bootstrap import BootstrapError, BootstrapResult, SuperAdminBootstrapService
from schedence_auth import ConfigurationError
from schedence_db import DatabaseConfigurationError
from schedence_db.runtime import run
from scripts._operator import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_REFUSED,
    database_failure,
    fail,
    operator_label,
    operator_unit_of_work,
    unexpected_failure,
)


async def _bootstrap(args: argparse.Namespace) -> BootstrapResult:
    settings = load_identity_settings()
    cognito = Boto3CognitoAdminClient(region=settings.aws_region, user_pool_id=settings.cognito_user_pool_id,
                                      profile=settings.aws_profile, timeout_seconds=settings.cognito_admin_timeout_seconds)
    async with operator_unit_of_work() as uow:
        return await SuperAdminBootstrapService(uow, cognito).bootstrap(
            email=args.email, institution_id=args.institution_id, operator=operator_label(), provision=args.provision,
            full_name=args.full_name,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.bootstrap_superadmin", description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--institution-id", required=True, type=uuid.UUID, help="UUID that identifies the institution (e.g. SLIIT)")
    parser.add_argument("--full-name", default=None)
    parser.add_argument("--provision", action="store_true", help="Send the Cognito invitation if the user does not exist yet")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks (may include host names)")
    args = parser.parse_args(argv)

    if not args.yes:
        print(f"About to grant SUPER_ADMIN for institution {args.institution_id} to {args.email.strip().lower()}.")
        if input("Type the e-mail address again to confirm: ").strip().lower() != args.email.strip().lower():
            return fail("Confirmation did not match. Nothing changed.")

    try:
        result = run(_bootstrap(args))
    except (ConfigurationError, DatabaseConfigurationError) as exc:
        return fail(str(exc), EXIT_CONFIG)
    except BootstrapError as exc:
        return fail(f"[{exc.code}] {exc}")
    except CognitoError as exc:
        return fail(f"Cognito call failed ({exc.category}). Check AWS_PROFILE, `aws sso login`, and the IAM policy.", EXIT_CONFIG)
    except PersistenceError as exc:
        return fail(f"Database error: {exc}", EXIT_REFUSED)
    except (SQLAlchemyError, OSError) as exc:
        if args.debug:
            raise
        return database_failure(exc)
    except Exception as exc:
        if args.debug:
            raise
        return unexpected_failure(exc)

    print(f"{result.status}: {result.message}")
    if result.user_id:
        print(f"User ID: {result.user_id}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
