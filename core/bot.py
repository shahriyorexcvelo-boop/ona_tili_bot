import os
import time
import asyncio
import logging
from typing import Dict
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

import test_db
from .config import BOT_TOKEN, ADMIN_ID, get_config_admin_ids, get_maintenance_message
from middlewares.throttling import ThrottlingMiddleware


class TrackedBot(Bot):
    """Barcha chiquvchi xabarlarni tutib, agar qabul qiluvchi qo'shimcha admin bo'lsa,
    message_id ni admin_sent_messages jadvaliga avtomatik yozib boruvchi maxsus Bot klassi."""
    async def __call__(self, method, request_timeout=None):
        res = await super().__call__(method, request_timeout=request_timeout)
        try:
            if not getattr(method, "_skip_admin_log", False):
                msg_id = getattr(res, "message_id", None)
                chat_obj = getattr(res, "chat", None)
                chat_id = getattr(chat_obj, "id", None) if chat_obj else getattr(method, "chat_id", None)
                if chat_id and msg_id:
                    cid = int(chat_id)
                    mid = int(msg_id)
                    if cid != int(ADMIN_ID) and test_db.is_admin(cid, ADMIN_ID):
                        asyncio.create_task(test_db.db(test_db.log_admin_message, cid, mid))
        except Exception:
            pass
        return res


class MaintenanceMiddleware(BaseMiddleware):
    """Texnik profilaktika vaqtida FAQAT Bosh Admin (ADMIN_ID) o'ta oladi. Boshqalar to'xtatiladi.
    Shuningdek, qo'shimcha adminlarning chatdagi xabar ID larini ham qayd etib boradi."""
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            try:
                if user.id != int(ADMIN_ID) and test_db.is_admin(user.id, ADMIN_ID):
                    if isinstance(event, Message) and event.chat and event.chat.id == user.id:
                        asyncio.create_task(test_db.db(test_db.log_admin_message, user.id, event.message_id))
                    elif isinstance(event, CallbackQuery) and event.message and event.message.chat.id == user.id:
                        asyncio.create_task(test_db.db(test_db.log_admin_message, user.id, event.message.message_id))
            except Exception:
                pass

        if user and await test_db.db(test_db.is_maintenance_mode):
            if user.id != ADMIN_ID:
                if isinstance(event, Message):
                    await event.answer(get_maintenance_message())
                    return
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Botda texnik profilaktika ketmoqda!", show_alert=True)
                    try:
                        await event.message.answer(get_maintenance_message())
                    except Exception:
                        pass
                    return
        return await handler(event, data)


# FSM Storage: RedisStorage (mahalliy yoki REDIS_URL orqali), aks holda MemoryStorage fallback
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
fsm_storage = None

if REDIS_URL:
    try:
        if test_db._get_redis() is not None:
            from aiogram.fsm.storage.redis import RedisStorage
            fsm_storage = RedisStorage.from_url(REDIS_URL)
            safe_redis_repr = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else REDIS_URL
            logging.getLogger("OnaTiliRasch").info(f"✅ FSM Holatlari uchun RedisStorage muvaffaqiyatli ulandi ({safe_redis_repr})")
        else:
            fsm_storage = MemoryStorage()
    except Exception as re:
        logging.getLogger("OnaTiliRasch").warning(
            f"⚠️ Redis ulanmadi ({re}). MemoryStorage'ga fallback qilinmoqda."
        )
        fsm_storage = MemoryStorage()
else:
    fsm_storage = MemoryStorage()

# Bot va Dispatcher obyektlari
bot = TrackedBot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=fsm_storage)

# 1000+ pik foydalanuvchi oqimida bot va bazani himoya qiluvchi Throttling (Rate Limiter: 0.35s)
throttling_middleware = ThrottlingMiddleware(limit=0.35, admin_ids=get_config_admin_ids())
dp.message.outer_middleware(throttling_middleware)
dp.callback_query.outer_middleware(throttling_middleware)

# Texnik profilaktika tekshiruvi
dp.message.outer_middleware(MaintenanceMiddleware())
dp.callback_query.outer_middleware(MaintenanceMiddleware())

