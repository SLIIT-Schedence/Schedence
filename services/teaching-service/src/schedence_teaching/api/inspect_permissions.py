"""Read-only self-inspection. Prompt for a token without echoing or storing it."""
import getpass
import json
import os
import warnings

import httpx
from schedence_auth import build_verifier, load_settings_model

from .identity import IdentityPermissionsClient
from .runtime import TeachingSettings
from .authorization import mode_for


async def inspect_current_permissions(verifier, permissions, token):
    claims=await verifier.verify_access_token(token)
    principal=await permissions.principal_for(token,claims)
    return dict(user_id=str(principal.user_id),institutions=[dict(institution_id=str(tenant),
        roles=sorted(grant.roles),teaching_permissions=sorted(p for p in grant.permissions if p.startswith('teaching:')),
        can_generate='teaching:generate' in grant.permissions,
        timetable_read_mode=mode_for(grant)) for tenant,grant in sorted(principal.grants.items(),key=lambda item:str(item[0]))])


async def _inspect(token):
    from schedence_db import find_env_file
    settings=load_settings_model(TeachingSettings,os.environ.get('SCHEDENCE_ENV_FILE') or find_env_file())
    async with httpx.AsyncClient(follow_redirects=False) as http:
        return await inspect_current_permissions(build_verifier(settings),
            IdentityPermissionsClient(settings.identity_service_url,http,timeout=settings.identity_timeout_seconds),token)


def main():
    from schedence_db.runtime import run
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=getpass.getpass('Cognito access token (hidden, never stored): ').strip()
        if not token:raise ValueError('missing token')
        result=run(_inspect(token))
    except Exception:
        print('Permission inspection failed. Check authentication and Identity configuration; no grants were changed.')
        raise SystemExit(1) from None
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
