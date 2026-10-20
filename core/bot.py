import os
import time
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

class MaintenanceMiddleware(BaseMiddleware):
    """Texnik profilaktika vaqtida FAQAT Bosh Admin (ADMIN_ID) o'ta oladi. Boshqalar to'xtatiladi."""
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
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


# FSM Storage: RedisStorage agar REDIS_URL berilgan bo'lsa, aks holda MemoryStorage fallback
REDIS_URL = os.getenv("REDIS_URL", "").strip()
fsm_storage = None

if REDIS_URL:
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        fsm_storage = RedisStorage.from_url(REDIS_URL)
        safe_redis_repr = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else "configured"
        logging.getLogger("OnaTiliRasch").info(f"✅ FSM Holatlari uchun RedisStorage muvaffaqiyatli ulandi ({safe_redis_repr})")
    except Exception as re:
        logging.getLogger("OnaTiliRasch").warning(
            f"⚠️ REDIS_URL mavjud, ammo Redis ulanmadi ({re}). MemoryStorage'ga fallback qilinmoqda. "
            f"Restartlarda FSM holatlari xotiradan o'chadi!"
        )
        fsm_storage = MemoryStorage()
else:
    logging.getLogger("OnaTiliRasch").info(
        "ℹ️ REDIS_URL belgilanmagan. FSM holatlari MemoryStorage'da saqlanadi. "
        "Doimiy holat saqlanishi uchun REDIS_URL o'rnating."
    )
    fsm_storage = MemoryStorage()

# Bot va Dispatcher obyektlari
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=fsm_storage)

# 1000+ pik foydalanuvchi oqimida bot va bazani himoya qiluvchi Throttling (Rate Limiter: 0.35s)
throttling_middleware = ThrottlingMiddleware(limit=0.35, admin_ids=get_config_admin_ids())
dp.message.outer_middleware(throttling_middleware)
dp.callback_query.outer_middleware(throttling_middleware)

# Texnik profilaktika tekshiruvi
dp.message.outer_middleware(MaintenanceMiddleware())
dp.callback_query.outer_middleware(MaintenanceMiddleware())

