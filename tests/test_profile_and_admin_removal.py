import os
import pytest
import test_db
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from handlers.webapp import create_web_app


@pytest.mark.anyio
async def test_admin_endpoints_removed_404():
    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Removed endpoints must return 404
        removed_routes = [
            ("GET", "/api/app/users"),
            ("POST", "/api/app/update-user-status"),
            ("POST", "/api/app/restrict-all-users"),
            ("POST", "/api/app/unrestrict-all-users"),
            ("POST", "/api/app/broadcast"),
            ("POST", "/api/app/broadcast/delete"),
            ("GET", "/api/app/test-submissions/1"),
        ]
        for method, path in removed_routes:
            if method == "GET":
                resp = await client.get(path)
            else:
                resp = await client.post(path, json={})
            assert resp.status == 404, f"Expected 404 for removed route {method} {path}, got {resp.status}"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_app_me_and_my_submissions(monkeypatch):
    os.environ["ALLOW_INSECURE_AUTH"] = "1"
    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Setup a registered user
        tg_id = 99887766
        test_db.add_or_update_user(tg_id, "Ali Valiyev", "+998901234567", "alivali", "approved")

        # In insecure local auth mode (tests allow tg_id query param)
        resp_me = await client.get(f"/api/app/me?tg_id={tg_id}")
        assert resp_me.status == 200
        data_me = await resp_me.json()
        assert data_me["success"] is True
        assert data_me["user"]["tg_id"] == tg_id
        assert data_me["user"]["fullname"] == "Ali Valiyev"
        assert data_me["user"]["status"] == "approved"
        assert data_me["is_admin"] is False

        # Test /api/app/my-submissions
        resp_subs = await client.get(f"/api/app/my-submissions?tg_id={tg_id}&limit=10&offset=0")
        assert resp_subs.status == 200
        data_subs = await resp_subs.json()
        assert data_subs["success"] is True
        assert "submissions" in data_subs
        assert data_subs["total"] == 0
        assert data_subs["limit"] == 10
        assert data_subs["offset"] == 0
    finally:
        os.environ.pop("ALLOW_INSECURE_AUTH", None)
        await client.close()


@pytest.mark.anyio
async def test_profile_no_pending_users_count_query(monkeypatch):
    os.environ["ALLOW_INSECURE_AUTH"] = "1"
    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        admin_id = test_db.ADMIN_ID
        test_db.add_or_update_user(admin_id, "Bosh Admin", "+998900000000", "admin", "approved")

        # Monitor calls to get_users_count
        called_count = 0
        orig_get_users_count = test_db.get_users_count

        def mock_get_users_count(*args, **kwargs):
            nonlocal called_count
            called_count += 1
            return orig_get_users_count(*args, **kwargs)

        monkeypatch.setattr(test_db, "get_users_count", mock_get_users_count)

        # Request profile for admin
        resp = await client.get(f"/api/app/profile?tg_id={admin_id}")
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["is_admin"] is True
        assert data["pending_users"] == 0

        # Ensure get_users_count was NEVER called during profile loading
        assert called_count == 0, f"get_users_count should not be called when opening profile! Called {called_count} times"
    finally:
        os.environ.pop("ALLOW_INSECURE_AUTH", None)
        await client.close()


@pytest.mark.anyio
async def test_get_system_stats():
    test_db.invalidate_system_stats_cache()
    stats = test_db.get_system_stats()
    assert isinstance(stats, dict)
    assert "approved" in stats
    assert "pending" in stats
    assert "blocked" in stats
    assert "tests" in stats
    assert "today_submissions" in stats


@pytest.mark.anyio
async def test_test_creation_flow_intact():
    """Admin test creation endpoints (/api/next-test-code, /api/create-test) remain intact."""
    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. next-test-code
        resp = await client.get("/api/next-test-code")
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert "next_code" in data

        # 2. trigger-solve route is registered
        routes = [r.resource.canonical for r in app.router.routes() if r.resource]
        assert "/api/app/trigger-solve" in routes
        assert "/api/next-test-code" in routes
        assert "/api/create-test" in routes
        assert "/admin.html" in routes
    finally:
        await client.close()
