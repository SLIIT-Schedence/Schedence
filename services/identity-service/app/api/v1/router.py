from fastapi import APIRouter

from app.api.v1 import me, provisioning, rbac, users

API_PREFIX = "/api/v1/identity"

router = APIRouter(prefix=API_PREFIX)
router.include_router(me.router)
router.include_router(users.router)
router.include_router(provisioning.router)
router.include_router(rbac.router)
