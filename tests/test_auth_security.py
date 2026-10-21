import os
import time
import hmac
import hashlib
import json
import urllib.parse
import pytest
from aiohttp import web
from core.auth_utils import verify_telegram_webapp_data
from handlers.webapp import create_web_app

def generate_signed_init_data(bot_token: str, user_id: int, auth_date: int = None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    user_str = json.dumps({"id": user_id, "first_name": "TestUser"}, separators=(',', ':'))
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_str
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    cal_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = cal_hash
    return urllib.parse.urlencode(params)


def test_auth_crypto_validation():
    bot_token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
    
    # 1. ALLOW_INSECURE_AUTH bo'lmaganda imzosiz initData qat'iy rad etilishi shart
    os.environ.pop("ALLOW_INSECURE_AUTH", None)
    unsigned_init_data = "user=%7B%22id%22%3A8039427064%7D"
    is_valid, u_dict = verify_telegram_webapp_data(unsigned_init_data, bot_token)
    assert is_valid is False
    assert u_dict is None

    # 2. Soxtalashtirilgan noto'g'ri hash rad etilishi shart
    fake_signed = unsigned_init_data + "&hash=deadbeef0123456789"
    is_valid, u_dict = verify_telegram_webapp_data(fake_signed, bot_token)
    assert is_valid is False
    assert u_dict is None

    # 3. 24 soatdan (86400s) eski bo'lgan imzo rad etilishi shart
    old_time = int(time.time()) - 90000
    expired_signed = generate_signed_init_data(bot_token, 8039427064, auth_date=old_time)
    is_valid, u_dict = verify_telegram_webapp_data(expired_signed, bot_token)
    assert is_valid is False
    assert u_dict is None

    # 4. To'g'ri va yangi imzolangan initData tasdiqlanishi shart
    valid_signed = generate_signed_init_data(bot_token, 8039427064)
    is_valid, u_dict = verify_telegram_webapp_data(valid_signed, bot_token)
    assert is_valid is True
    assert u_dict["id"] == 8039427064

    # 5. Faqat ALLOW_INSECURE_AUTH=1 bo'lganda lokal sinov uchun imzosiz ruxsat beriladi
    os.environ["ALLOW_INSECURE_AUTH"] = "1"
    unsigned_local = "user=%7B%22id%22%3A999000999%7D"
    is_valid_local, u_dict_local = verify_telegram_webapp_data(unsigned_local, bot_token)
    assert is_valid_local is True
    assert u_dict_local["id"] == 999000999
    os.environ.pop("ALLOW_INSECURE_AUTH", None)


@pytest.mark.anyio
async def test_endpoint_security_rejects_query_spoofing():
    # Production rejimini taqlid qilish (ALLOW_INSECURE_AUTH yo'q)
    os.environ.pop("ALLOW_INSECURE_AUTH", None)
    app = await create_web_app()

    from aiohttp.test_utils import TestClient, TestServer
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # A) ?tg_id=8039427064 bilan aldashga urinish -> 401 olishi shart
        resp = await client.get("/api/app/profile?tg_id=8039427064")
        assert resp.status == 401
        data = await resp.json()
        assert data["success"] is False

        # B) Imzosiz initData bilan profile olishga urinish -> 401
        resp2 = await client.get("/api/app/profile?init_data=user=%7B%22id%22%3A8039427064%7D")
        assert resp2.status == 401

        # C) Imzosiz submit-test ga urinish -> 401
        resp3 = await client.post("/api/submit-test", json={"test_id": 1, "answers": {"1": "A"}})
        assert resp3.status == 401

        # D) Imzosiz admin amallariga urinish -> 401
        resp4 = await client.post("/api/admin/clean-blocked-users", json={"admin_id": 8039427064})
        assert resp4.status == 401
    finally:
        await client.close()
