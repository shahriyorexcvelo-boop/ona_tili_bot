"""
Lightweight Group / Channel Membership Check with 1-hour RAM Caching.
1,000+ o'quvchi bir vaqtda kirganda Telegram API ga takroriy so'rovlar yuborib flood wait
yoki cheklovlarga tushmasligi uchun 1 soat (3600s) davomida natijalarni RAM keshda saqlaydi.
"""
from __future__ import annotations
import time
import logging
from typing import Dict, Tuple, Optional, Any

log = logging.getLogger(__name__)

# Key: (user_tg_id, chat_id_str) -> Value: (timestamp, is_member)
_USER_MEMBERSHIP_CACHE: Dict[Tuple[int, str], Tuple[float, bool]] = {}
MEMBERSHIP_CACHE_TTL = 3600.0  # 1 soat (3600 soniya)

def set_cached_membership(user_id: int, chat_id: Any, is_member: bool):
    """Chat a'zoligi bo'yicha event (qo'shilish/chiqish) kelganda keshni zudlik bilan yangilash."""
    try:
        _USER_MEMBERSHIP_CACHE[(int(user_id), str(chat_id))] = (time.time(), bool(is_member))
    except Exception:
        pass

async def check_user_membership(bot, user_tg_id: int, chat_id: Any) -> bool:
    """
    1,000+ o'quvchi uchun optimallashtirilgan a'zolik tekshiruvi:
    - 1 soat davomida RAM keshdan darhol (0.00001s) natija qaytaradi.
    - 1 soat ichida qayta kirgan foydalanuvchi uchun Telegram API ga ortiqcha bitta ham so'rov yuborilmaydi.
    """
    if not user_tg_id or not chat_id:
        return True

    key = (int(user_tg_id), str(chat_id))
    now = time.time()
    
    # 1. RAM keshdan tekshirish
    if key in _USER_MEMBERSHIP_CACHE:
        ts, is_member = _USER_MEMBERSHIP_CACHE[key]
        if now - ts < MEMBERSHIP_CACHE_TTL:
            return is_member

    # 2. Faqat kesh muddati o'tgandagina Telegram API ga so'rov yuborish
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_tg_id)
        status = getattr(member, "status", None)
        is_member = status in ("creator", "administrator", "member", "restricted")
        _USER_MEMBERSHIP_CACHE[key] = (now, is_member)
        return is_member
    except Exception as ex:
        log.warning(f"Guruh a'zoligini tekshirishda ogohlantirish: {ex}")
        # Agar bot guruhda admin bo'lmasa yoki vaqtincha xatolik bo'lsa, o'quvchini bloklamaslik uchun True deb hisoblash
        _USER_MEMBERSHIP_CACHE[key] = (now, True)
        return True
