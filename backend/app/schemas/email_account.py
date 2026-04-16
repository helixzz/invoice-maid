from pydantic import BaseModel


class EmailAccountCreate(BaseModel):
    name: str
    type: str
    host: str | None = None
    port: int | None = None
    username: str
    password: str | None = None
    oauth_token_path: str | None = None
    is_active: bool = True


class EmailAccountUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    is_active: bool | None = None


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    type: str
    host: str | None
    port: int | None
    username: str
    is_active: bool
    last_scan_uid: str | None
    created_at: str
