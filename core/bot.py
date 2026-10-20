import time
from typing import Dict
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

import test_db
from .config import BOT_TOKEN, ADMIN_ID, get_maintenance_message

class ThrottlingMiddleware(BaseMiddleware):
    """10,000+ foydalanuvchi oqimida botni spam va flood hujumlaridan himoya qiluvchi Rate-Limiting middleware."""
    def __init__(self, limit: float = 0.5):
        self.limit = limit
        self.user_timeouts: Dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            # Adminlar uchun limit yo'q
            if user.id == ADMIN_ID:
                return await handler(event, data)

            now = time.time()
            last_time = self.user_timeouts.get(user.id, 0)
            if now - last_time < self.limit:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("Iltimos, biroz kuting...", show_alert=False)
                    except Exception:
                        pass
                return  # Flood/spam so'rovlarini to'xtatish
            self.user_timeouts[user.id] = now

            # Xotirani tejash uchun vaqti-vaqti bilan eski yozuvlarni tozalash
            if len(self.user_timeouts) > 20000:
                cutoff = now - 60
                self.user_timeouts = {uid: t for uid, t in self.user_timeouts.items() if t > cutoff}

        return await handler(event, data)

class MaintenanceMiddleware(BaseMiddleware):
    """Texnik profilaktika vaqtida FAQAT Bosh Admin (ADMIN_ID) o'ta oladi. Boshqalar to'xtatiladi."""
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and test_db.is_maintenance_mode():
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

# Bot va Dispatcher obyektlari
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(ThrottlingMiddleware(limit=0.5))
dp.callback_query.outer_middleware(ThrottlingMiddleware(limit=0.3))
dp.message.outer_middleware(MaintenanceMiddleware())
dp.callback_query.outer_middleware(MaintenanceMiddleware())

