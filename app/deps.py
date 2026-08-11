from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request

from app.config import Settings
from app.db import Database
from app.search.query import Filters


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@dataclass(frozen=True)
class EventQuery:
    q: str
    filters: Filters
    params: dict[str, str]


def event_query(
    q: str = "",
    endpoint: str = "",
    event_status: Annotated[str, Query(alias="status")] = "",
    method: str = "",
    event_type: Annotated[str, Query(alias="type")] = "",
    date_from: Annotated[str, Query(alias="from")] = "",
    date_to: Annotated[str, Query(alias="to")] = "",
) -> EventQuery:
    # Shared by the event list and the export
    return EventQuery(
        q=q,
        filters=Filters(
            endpoint=endpoint or None,
            status=event_status or None,
            method=method or None,
            event_type=event_type or None,
            date_from=date_from or None,
            date_to=date_to or None,
        ),
        params={
            key: value
            for key, value in (
                ("q", q),
                ("endpoint", endpoint),
                ("status", event_status),
                ("method", method),
                ("type", event_type),
                ("from", date_from),
                ("to", date_to),
            )
            if value
        },
    )


DatabaseDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
EventQueryDep = Annotated[EventQuery, Depends(event_query)]
