import pytest

from app.models import AppSettings


@pytest.mark.asyncio
async def test_get_classifier_settings_returns_defaults(client, auth_headers, settings) -> None:
    response = await client.get("/api/v1/settings/classifier", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["trusted_senders"] == ""
    assert data["extra_keywords"] == ""


@pytest.mark.asyncio
async def test_update_classifier_settings_persists(client, auth_headers, settings, db) -> None:
    for key in ("classifier_trusted_senders", "classifier_extra_keywords"):
        row = await db.get(AppSettings, key)
        if row:
            await db.delete(row)
    await db.commit()

    put = await client.put(
        "/api/v1/settings/classifier",
        headers=auth_headers,
        json={"trusted_senders": "tax.gov.cn\neinvoice@co.com", "extra_keywords": "财务,billing"},
    )
    assert put.status_code == 200
    data = put.json()
    assert data["trusted_senders"] == "tax.gov.cn\neinvoice@co.com"
    assert data["extra_keywords"] == "财务,billing"

    get = await client.get("/api/v1/settings/classifier", headers=auth_headers)
    assert get.json() == data


@pytest.mark.asyncio
async def test_update_classifier_settings_partial(client, auth_headers, settings) -> None:
    await client.put(
        "/api/v1/settings/classifier",
        headers=auth_headers,
        json={"trusted_senders": "a@b.com"},
    )
    put2 = await client.put(
        "/api/v1/settings/classifier",
        headers=auth_headers,
        json={"extra_keywords": "billing"},
    )
    assert put2.status_code == 200
    data = put2.json()
    assert data["trusted_senders"] == "a@b.com"
    assert data["extra_keywords"] == "billing"


@pytest.mark.asyncio
async def test_update_classifier_settings_overwrites_existing(client, auth_headers, settings) -> None:
    await client.put("/api/v1/settings/classifier", headers=auth_headers,
                     json={"trusted_senders": "first@a.com"})
    put2 = await client.put("/api/v1/settings/classifier", headers=auth_headers,
                            json={"trusted_senders": "second@b.com"})
    assert put2.json()["trusted_senders"] == "second@b.com"


@pytest.mark.asyncio
async def test_classifier_settings_requires_auth(client) -> None:
    assert (await client.get("/api/v1/settings/classifier")).status_code == 401
    assert (await client.put("/api/v1/settings/classifier", json={})).status_code == 401
