from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request

from app.core.container import IdentityContainer
from app.repositories.base import IdentityUnitOfWork


def get_container(request: Request) -> IdentityContainer:
    return request.app.state.container


ContainerDep = Annotated[IdentityContainer, Depends(get_container)]


async def get_uow(container: ContainerDep) -> AsyncIterator[IdentityUnitOfWork]:
    async with container.uow_factory() as uow:
        yield uow


UnitOfWorkDep = Annotated[IdentityUnitOfWork, Depends(get_uow)]


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


RequestIdDep = Annotated[str | None, Depends(get_request_id)]


@dataclass(frozen=True)
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_pagination(
    page: Annotated[int, Query(ge=1, le=100_000, description="1-based page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page (max 100)")] = 20,
) -> Pagination:
    return Pagination(page, page_size)


PaginationDep = Annotated[Pagination, Depends(get_pagination)]
