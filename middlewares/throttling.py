"""
Ona tili Bot — Throttling (Rate Limiter) Middleware
Aiogram 3.x Outer Middleware
===================================================
1000+ foydalanuvchi bir vaqtda (pik yuklamada) /start bosganda yoki
test topshirganda bot va Supabase ma'lumotlar bazasini spam/flood
hujumlaridan himoya qilish hamda server qotib qolmasligi uchun xizmat qiladi.
"""
from __future__ import annotations
import os
import time
import logging
from typing import Dict, Any, Callable, Awaitable, Set, Optional, Iterable
from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger("throttling")


class ThrottlingMiddleware(BaseMiddleware):
    """
    10,000+ foydalanuvchi oqimida botni spam va flood hujumlaridan himoya qiluvchi
    yuqori unumli Rate-Limiting middleware.
    """
    def __init__(self, limit: float = 2.5, admin_ids: Optional[Iterable[int]] = None):
        self.limit = limit
        self.user_timeouts: Dict[int, float] = {}
        self.user_warned: Dict[int, float] = {}
        self.request_count: int = 0
        
        # Adminlar to'plamini shakllantirish (Bypass)
        self._admin_ids: Set[int] = set(admin_ids) if admin_ids else set()
        self._load_env_admin_ids()

    def _load_env_admin_ids(self) -> None:
        """Environment variables orqali adminlarni to'ldirish"""
        for var_name in ("ADMIN_ID", "ADMIN_IDS"):
            raw = os.getenv(var_name, "")
            for part in str(raw).replace(";", ",").split(","):
                part = part.strip()
                if part.isdigit():
                    self._admin_ids.add(int(part))
        # Default fallback admin
        self._admin_ids.add(8039427064)

    def is_admin(self, user_id: int) -> bool:
        """Adminlarni tekshirish (bypass)"""
        return user_id in self._admin_ids

    def _cleanup_if_needed(self, now: float) -> None:
        """Xotira va Performance optimizatsiyasi: eski yozuvlarni tozalash"""
        if self.request_count % 500 == 0 or len(self.user_timeouts) > 10000:
            cutoff = now - (self.limit * 4)  # ~10 sekunddan eski yozuvlar
            self.user_timeouts = {uid: t for uid, t in self.user_timeouts.items() if t > cutoff}
            self.user_warned = {uid: t for uid, t in self.user_warned.items() if t > cutoff}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # 1. Adminlar (ADMIN_ID) uchun ushbu cheklov amal qilmasin (bypass)
        if self.is_admin(user.id):
            return await handler(event, data)

        now = time.time()
        self.request_count += 1
        self._cleanup_if_needed(now)

        last_time = self.user_timeouts.get(user.id, 0.0)

        # 2. Agar foydalanuvchi 2.5 sekund ichida takroran so'rov yuborsa
        if now - last_time < self.limit:
            # Message bo'lsa:
            if isinstance(event, Message):
                last_warn = self.user_warned.get(user.id, 0.0)
                # Ogohlantirish xabarini ham flood qilmaslik uchun (har 2 soniyada 1 marta)
                if now - last_warn >= 2.0:
                    self.user_warned[user.id] = now
                    try:
                        await event.answer(
                            "⏳ <b>Juda ko'p so'rov yuborildi!</b>\n"
                            "Iltimos, serverni ortiqcha yuklamaslik uchun 2-3 soniya kuting va qayta urinib ko'ring.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as err:
                        logger.debug(f"Throttling ogohlantirish yuborishda xatolik: {err}")

            # CallbackQuery (Tugma) bo'lsa:
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Biroz kuting (2-3 sekund)...", show_alert=False)
                except Exception as err:
                    logger.debug(f"Callback answer xatolik: {err}")

            # So'rov to'xtatilib, keyingi handler'larga va Supabase bazasiga o'tkazilmasin
            return

        # So'rov tasdiqlandi, vaqtni yangilaymiz
        self.user_timeouts[user.id] = now

        return await handler(event, data)
