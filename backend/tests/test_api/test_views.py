from __future__ import annotations


async def test_saved_views_crud_cycle(client, auth_headers) -> None:
    create_response = await client.post(
        "/api/v1/views",
        headers=auth_headers,
        json={"name": "April spend", "filter_json": '{"seller":"Beta Seller","month":"2026-04"}'},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "April spend"
    assert created["filter_json"] == '{"seller":"Beta Seller","month":"2026-04"}'
    assert created["created_at"]

    list_response = await client.get("/api/v1/views", headers=auth_headers)
    assert list_response.status_code == 200
    assert list_response.json() == [created]

    delete_response = await client.delete(f"/api/v1/views/{created['id']}", headers=auth_headers)
    assert delete_response.status_code == 204

    empty_list_response = await client.get("/api/v1/views", headers=auth_headers)
    assert empty_list_response.status_code == 200
    assert empty_list_response.json() == []


async def test_saved_views_require_auth_and_missing_delete_returns_404(client, auth_headers) -> None:
    assert (await client.get("/api/v1/views")).status_code == 401
    assert (await client.post("/api/v1/views", json={"name": "A", "filter_json": "{}"})).status_code == 401
    assert (await client.delete("/api/v1/views/1")).status_code == 401

    missing_delete = await client.delete("/api/v1/views/999", headers=auth_headers)
    assert missing_delete.status_code == 404
    assert missing_delete.json() == {"detail": "Not found"}
