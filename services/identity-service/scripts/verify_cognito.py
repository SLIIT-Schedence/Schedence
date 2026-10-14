"""Verify the Cognito configuration in .env against AWS. Read-only.

Prints PASS / WARN / FAIL per check. Never prints IDs, URLs, keys or tokens.

Usage (PowerShell, from services/identity-service):
    python -m scripts.verify_cognito                 # public endpoints + DescribeUserPool via AWS_PROFILE
    python -m scripts.verify_cognito --public-only   # no AWS credentials needed
"""

from __future__ import annotations

import argparse
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import IdentitySettings, load_identity_settings
from app.integrations.cognito import CognitoError, CognitoPermissionError
from app.integrations.cognito.boto3_client import Boto3CognitoAdminClient
from schedence_auth import ConfigurationError
from schedence_db.runtime import run

AUTHJS_CALLBACK = "http://localhost:3000/api/auth/callback/cognito"


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str, hint: str = "", *, warn_only: bool = False) -> None:
        status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
        self.failures += 0 if ok or warn_only else 1
        print(f"[{status}] {label}" + (f" - {hint}" if hint and not ok else ""))


_BROWSER = {"User-Agent": "Mozilla/5.0 (schedence verify_cognito)", "Accept": "text/html"}
_PKCE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # any valid S256 challenge; no code is ever exchanged


def authorize_outcome(http: httpx.Client, domain: str, client_id: str, redirect_uri: str) -> str:
    """'ok' if Cognito would show its sign-in page; otherwise the error it reports (e.g. redirect_mismatch, invalid_scope)."""
    try:
        response = http.get(f"{domain}/oauth2/authorize", headers=_BROWSER, params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "openid email profile",
            "code_challenge_method": "S256", "code_challenge": _PKCE_CHALLENGE})
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"
    location = urlparse(response.headers.get("location", ""))
    query = parse_qs(location.query)
    if location.path.endswith("/login") and "error" not in query:
        return "ok"
    if "error" in query:
        return query.get("error_description", query["error"])[0]
    return f"HTTP {response.status_code}"


def client_has_secret(http: httpx.Client, domain: str, client_id: str) -> bool | None:
    """Ask the token endpoint to redeem a fake code without a secret: 'invalid_client' means a secret is required."""
    try:
        response = http.post(f"{domain}/oauth2/token", data={
            "grant_type": "authorization_code", "client_id": client_id, "code": "00000000-0000-0000-0000-000000000000",
            "redirect_uri": "http://localhost:8001/docs/oauth2-redirect", "code_verifier": "x" * 43})
        error = response.json().get("error")
    except (httpx.HTTPError, ValueError):
        return None
    return {"invalid_client": True, "invalid_grant": False}.get(error)


def login_pages_available(http: httpx.Client, domain: str, client_id: str, redirect_uri: str) -> bool:
    try:
        page = http.get(f"{domain}/login", headers=_BROWSER, follow_redirects=True,
                        params={"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri})
    except httpx.HTTPError:
        return False
    return page.status_code == 200


def public_checks(settings: IdentitySettings, report: Report) -> None:
    with httpx.Client(timeout=15, follow_redirects=False) as http:
        try:
            discovery = http.get(f"{settings.issuer}/.well-known/openid-configuration")
            data = discovery.json() if discovery.status_code == 200 else {}
        except httpx.HTTPError:
            data = {}
        report.check(data.get("issuer") == settings.issuer, "Issuer discovery document matches AWS_REGION + COGNITO_USER_POOL_ID")
        try:
            keys = http.get(settings.jwks_url).json().get("keys", [])
        except (httpx.HTTPError, ValueError):
            keys = []
        report.check(bool(keys) and all(k.get("alg") == "RS256" for k in keys), f"JWKS has RS256 signing keys ({len(keys)})")
        if settings.cognito_domain:
            domain = settings.cognito_domain
            report.check(str(data.get("authorization_endpoint", "")).startswith(domain), "COGNITO_DOMAIN is the pool's authorization domain")
            web = settings.cognito_app_client_id
            report.check(login_pages_available(http, domain, web, AUTHJS_CALLBACK),
                         "Managed login page loads for the web app client and its callback URL",
                         "check the app client ID, its allowed callback URLs and its managed login style")
            outcome = authorize_outcome(http, domain, web, AUTHJS_CALLBACK)
            report.check(outcome == "ok", "Web app client accepts scopes openid, email, profile",
                         f"{outcome}: tick OpenID, Email and Profile under Schedence-Web > Login pages > OpenID Connect scopes",
                         warn_only=True)
            dev = settings.cognito_devtools_client_id
            if dev:
                report.check(client_has_secret(http, domain, dev) is False, "DevTools client is public (no client secret)",
                             "it was created as 'Traditional web application'; secrets can't be removed, so create a new app "
                             "client of type 'Single-page application (SPA)' and use its ID")
                swagger = "http://localhost:8001/docs/oauth2-redirect"
                report.check(login_pages_available(http, domain, dev, swagger), "DevTools client has managed login pages",
                             "Branding > Managed login > Create a style > choose Schedence-DevTools > Create "
                             "(Cognito shows 'Login pages unavailable')")
                for callback in (swagger, "http://localhost:8002/docs/oauth2-redirect", "https://oauth.pstmn.io/v1/callback"):
                    outcome = authorize_outcome(http, domain, dev, callback)
                    hint = ("add it under Schedence-DevTools > Login pages > Allowed callback URLs" if outcome == "redirect_mismatch"
                            else f"{outcome}: check grant type (Authorization code) and scopes (OpenID, Email, Profile)")
                    report.check(outcome == "ok", f"DevTools client accepts callback {callback}", hint,
                                 warn_only=callback.startswith("https://oauth.pstmn.io"))
            else:
                report.check(False, "COGNITO_DEVTOOLS_CLIENT_ID is set", "needed for Swagger UI / Postman sign-in", warn_only=True)
        else:
            report.check(False, "COGNITO_DOMAIN is set", "needed by the frontend and Swagger sign-in", warn_only=True)


async def aws_checks(settings: IdentitySettings, report: Report) -> None:
    client = Boto3CognitoAdminClient(region=settings.aws_region, user_pool_id=settings.cognito_user_pool_id, profile=settings.aws_profile)
    try:
        pool = await client.describe_user_pool()
    except CognitoError as exc:
        report.check(False, "DescribeUserPool with AWS_PROFILE", f"{exc.category}: run `aws sso login --profile <profile>` and check the IAM policy")
        return
    report.check(True, "DescribeUserPool with AWS_PROFILE")
    policies = pool.get("Policies", {})
    password = policies.get("PasswordPolicy", {})
    first_factors = policies.get("SignInPolicy", {}).get("AllowedFirstAuthFactors", ["PASSWORD"])
    recovery = [m.get("Name") for m in pool.get("AccountRecoverySetting", {}).get("RecoveryMechanisms", [])]
    pending_verification = pool.get("UserAttributeUpdateSettings", {}).get("AttributesRequireVerificationBeforeUpdate", [])

    report.check(pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly") is True, "Self-registration is disabled")
    sign_in = pool.get("UsernameAttributes") or []
    report.check("email" in sign_in, "Users sign in with e-mail", "the pool must use e-mail as a sign-in identifier (fixed at creation)")
    if "email" in sign_in:
        report.check(sign_in == ["email"], "E-mail is the only sign-in identifier",
                     f"also allows {', '.join(a for a in sign_in if a != 'email')} (fixed at creation)", warn_only=True)
    required = sorted(a["Name"] for a in pool.get("SchemaAttributes", []) if a.get("Required") and a["Name"] not in ("sub", "email"))
    report.check(not required, "No required attributes besides e-mail",
                 f"every user must enter {', '.join(required)} at first sign-in; required attributes are fixed at creation, "
                 "so the only fix is a new user pool")
    report.check(pool.get("UserPoolTier") in ("ESSENTIALS", "PLUS"), "Feature plan supports managed login (Essentials/Plus)")
    report.check(bool(pool.get("Domain") or pool.get("CustomDomain")), "A user pool domain exists")
    report.check(set(first_factors) <= {"PASSWORD", "PASSWORD_SRP"}, "Only password sign-in factors are enabled",
                 "with passwordless factors AdminCreateUser cannot generate temporary passwords", warn_only=True)
    report.check(pool.get("MfaConfiguration") == "ON", "MFA is required", "Sign-in > Multi-factor authentication", warn_only=True)
    report.check(int(password.get("MinimumLength", 0)) >= 12, "Password minimum length >= 12", warn_only=True)
    report.check(recovery[:1] == ["verified_email"], "Account recovery by verified e-mail", warn_only=True)
    report.check("email" in pending_verification, "E-mail changes need verification before taking effect", warn_only=True)
    report.check(pool.get("DeletionProtection") == "ACTIVE", "Deletion protection is active", warn_only=True)
    report.check(pool.get("Name") == "Schedence", "User pool is named 'Schedence'", warn_only=True)

    boto = client._boto_client()  # least-privilege probe: this call is NOT in the recommended policy
    try:
        boto.list_user_pools(MaxResults=1)
        report.check(False, "AWS_PROFILE is least-privilege (ListUserPools denied)", "the profile can do more than it needs", warn_only=True)
    except Exception as exc:  # AccessDenied expected
        from app.integrations.cognito.boto3_client import translate_error

        report.check(isinstance(translate_error(exc), CognitoPermissionError), "AWS_PROFILE is least-privilege (ListUserPools denied)",
                     warn_only=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.verify_cognito", description=__doc__.splitlines()[0])
    parser.add_argument("--public-only", action="store_true")
    args = parser.parse_args(argv)
    report = Report()
    try:
        settings = load_identity_settings()
    except ConfigurationError as exc:
        print(f"[FAIL] Configuration\n{exc}")
        return 2
    report.check(True, "Cognito settings in .env are valid and consistent")
    public_checks(settings, report)
    if args.public_only:
        print("[SKIP] AWS API checks (--public-only)")
    elif not settings.aws_profile:
        print("[SKIP] AWS API checks - AWS_PROFILE is not set (refusing to fall back to the default credential chain)")
        report.failures += 1
    else:
        run(aws_checks(settings, report))
    print("\nResult:", "OK" if report.failures == 0 else f"{report.failures} failing check(s)")
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
