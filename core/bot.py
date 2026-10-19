from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

import test_db
from .config import BOT_TOKEN, ADMIN_ID, get_maintenance_message

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
dp.message.outer_middleware(MaintenanceMiddleware())
dp.callback_query.outer_middleware(MaintenanceMiddleware())
