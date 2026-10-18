"""Venue and venue availability endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import VENUE_ID, BlockSource, SeatingReaderDep, SourceDep, VenueType
from app.api.schemas import VenueBlockOut, VenueOut

router = APIRouter(tags=["venues"])


@router.get("/venues", response_model=list[VenueOut])
async def list_venues(
    seating: SeatingReaderDep, venue_type: Annotated[VenueType | None, Query()] = None
) -> list[VenueOut]:
    """Venues with their effective exam seating percentage and capacity."""
    return [
        VenueOut(
            venue_id=v.venue_id,
            venue_name=v.venue_name,
            venue_type=v.venue_type,
            building=v.building,
            floor=v.floor,
            normal_capacity=v.normal_capacity,
            exam_seating_percentage=c.percentage,
            exam_seating_rule=c.rule_scope,
            exam_capacity=c.exam_capacity,
            available_for_exams=c.available_for_exams,
            seed_exam_capacity=v.exam_capacity,
        )
        for v, c in await seating.venues(venue_type=venue_type)
    ]


@router.get("/venue-blocks", response_model=list[VenueBlockOut])
async def list_venue_blocks(
    source: SourceDep,
    venue_id: Annotated[str | None, Query(pattern=VENUE_ID)] = None,
    block_source: Annotated[BlockSource | None, Query(alias="source")] = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> list[VenueBlockOut]:
    """Periods when a venue cannot be used for exams (teaching or maintenance)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    blocks = await source.list_venue_blocks(
        venue_id=venue_id, source=block_source, date_from=date_from, date_to=date_to
    )
    return [VenueBlockOut.model_validate(b) for b in blocks]
