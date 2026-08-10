from typing import Annotated

from fastapi import Depends, Request

from webhook_inbox.config import Settings
from webhook_inbox.db import Database


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


DatabaseDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
