from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.dependencies.common import ContainerDep, RequestIdDep, UnitOfWorkDep
from app.services.directory import DirectoryService
from app.services.users import UserManagementService


def get_user_service(uow: UnitOfWorkDep, container: ContainerDep, request_id: RequestIdDep) -> UserManagementService:
    return UserManagementService(uow, container.cognito, clock=container.clock, request_id=request_id)


def get_directory_service(uow: UnitOfWorkDep) -> DirectoryService:
    return DirectoryService(uow)


UserServiceDep = Annotated[UserManagementService, Depends(get_user_service)]
DirectoryServiceDep = Annotated[DirectoryService, Depends(get_directory_service)]
