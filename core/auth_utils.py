"""
Zero-DB-Read Telegram WebApp Auth & initData Crypto Validator.
1,000+ o'quvchi bir vaqtda kirganda bazaga birorta ham SELECT so'rovi yubormasdan,
sof CPU RAM va HMAC-SHA256 yordamida sub-millisekundda (~0.05ms) autentifikatsiya qiladi.
"""
from __future__ import annotations
import os
import hmac
import hashlib
import json
import time
import urllib.parse
from typing import Optional, Tuple, Dict, Any

# RAM Kesh: oldin tekshirilgan initData larni 5 daqiqa davomida xotirada saqlash
_INITDATA_CACHE: Dict[str, Tuple[float, bool, Optional[Dict[str, Any]]]] = {}
_INITDATA_CACHE_TTL = 300.0  # 5 daqiqa (300 soniya)
_MAX_INITDATA_CACHE_SIZE = 2000

def _clean_initdata_cache(now: float):
    global _INITDATA_CACHE
    if len(_INITDATA_CACHE) > _MAX_INITDATA_CACHE_SIZE:
        # TTL muddati o'tganlarni tozalash
        expired_keys = [k for k, v in _INITDATA_CACHE.items() if now - v[0] >= _INITDATA_CACHE_TTL]
        for k in expired_keys:
            _INITDATA_CACHE.pop(k, None)
        # Agar hali ham 2000 dan ko'p bo'lsa, eng eskilardan o'chirish
        if len(_INITDATA_CACHE) > _MAX_INITDATA_CACHE_SIZE:
            sorted_by_ts = sorted(_INITDATA_CACHE.items(), key=lambda item: item[1][0])
            to_remove = len(_INITDATA_CACHE) - _MAX_INITDATA_CACHE_SIZE
            for k, _ in sorted_by_ts[:to_remove]:
                _INITDATA_CACHE.pop(k, None)

def verify_telegram_webapp_data(init_data: str, bot_token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Telegram WebApp initData ni Telegram rasmiy HMAC-SHA256 algoritmi bo'yicha tekshirish.
    Bazadan 0 ta so'rov talab qiladi (Zero-DB crypto).
    
    Qaytaradi:
        (is_valid: bool, user_dict: Optional[dict])
    """
    if not init_data or not isinstance(init_data, str):
        return False, None

    now = time.time()
    _clean_initdata_cache(now)
    
    # 1. Tezkor RAM kesh tekshiruvi (0.00001s)
    if init_data in _INITDATA_CACHE:
        ts, is_valid, u_dict = _INITDATA_CACHE[init_data]
        if now - ts < _INITDATA_CACHE_TTL:
            return is_valid, u_dict

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        
        user_dict = None
        if "user" in parsed:
            try:
                user_dict = json.loads(parsed["user"])
            except Exception:
                user_dict = None

        allow_insecure = os.getenv("ALLOW_INSECURE_AUTH", "0").strip() == "1"

        # 4.1: hash bo'lmasa HECH QACHON True qaytarmasin (faqat ALLOW_INSECURE_AUTH=1 bo'lganda lokal ruxsat)
        if not received_hash:
            if allow_insecure and user_dict and user_dict.get("id"):
                _INITDATA_CACHE[init_data] = (now, True, user_dict)
                return True, user_dict
            _INITDATA_CACHE[init_data] = (now, False, None)
            return False, None

        if not bot_token:
            _INITDATA_CACHE[init_data] = (now, False, None)
            return False, None

        # 4.2: auth_date 24 soatdan (86400 soniya) eski bo'lsa rad etilsin
        auth_date_str = parsed.get("auth_date")
        if not auth_date_str:
            _INITDATA_CACHE[init_data] = (now, False, None)
            return False, None
        try:
            auth_date = float(auth_date_str)
            if now - auth_date > 86400.0:  # 24 soatdan eski
                _INITDATA_CACHE[init_data] = (now, False, None)
                return False, None
        except (ValueError, TypeError):
            _INITDATA_CACHE[init_data] = (now, False, None)
            return False, None

        # 2. Telegram HMAC-SHA256 hisoblash
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        is_valid = hmac.compare_digest(calculated_hash, received_hash)
        _INITDATA_CACHE[init_data] = (now, is_valid, user_dict if is_valid else None)
        return is_valid, user_dict if is_valid else None

    except Exception:
        return False, None
