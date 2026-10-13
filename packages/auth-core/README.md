# schedence-auth-core

Shared **AWS Cognito access-token verification** for Schedence FastAPI services (imported as `schedence_auth`).

It answers one question: *is this a genuine, unexpired access token from our user pool, issued to one of our app clients?* It does **not** decide what the caller may do. Each service resolves roles and permissions from its own data (the Identity Service) or via the Identity Service API (other services).

| Module | Purpose |
|---|---|
| `verifier.py` | `CognitoTokenVerifier.verify_access_token()`: RS256 only, `kid` → JWKS key, `iss`, `exp`/`iat` (with leeway), `token_use == "access"`, `client_id` allow-list, optional required scopes |
| `jwks.py` | `JwksCache`: per-`kid` cache with TTL, rate-limited refresh on unknown `kid` (key rotation), stale-if-error |
| `settings.py` | `CognitoSettings` base class (`AWS_REGION`, `COGNITO_*`), derived issuer/JWKS URLs, `load_settings_model()` with value-free error messages |
| `testing.py` | `TokenFactory`: in-memory RSA keys that mint Cognito-shaped tokens for unit tests |

```powershell
python -m pip install -e "./packages/auth-core[dev]"
python -m pytest packages/auth-core
```

Errors carry a fixed `reason` code (`expired`, `invalid_issuer`, `invalid_client`, `wrong_token_use`, `unknown_key`, ...). Messages never contain the token or configuration values.
