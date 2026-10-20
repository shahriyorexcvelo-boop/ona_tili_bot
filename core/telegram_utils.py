"""
Telegram API xatoliklarini markazlashgan holda tahlil qilish utiliti.
Foydalanuvchi botni bloklaganmi yoki akkaunti butunlay o'chirilganmi?
"""
import logging
from typing import Optional
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramRetryAfter,
    TelegramNetworkError,
    TelegramAPIError,
)

log = logging.getLogger("telegram_utils")

# Foydalanuvchi botni bloklagan yoki profili o'chirilganini bildiruvchi xos iboralar
GONE_PHRASES = (
    "bot was blocked by the user",
    "bot was blocked by user",
    "blocked by the user",
    "blocked by user",
    "user is deactivated",
    "chat not found",
    "bot was kicked from the supergroup",
    "bot was kicked from the channel",
    "bot was kicked from the group",
    "user not found",
    "peer_id_invalid",
)


def is_user_gone_error(exc: Optional[Exception]) -> bool:
    """
    Foydalanuvchi rostdan ham botni bloklagan yoki o'chirilganligini tekshiradi.
    Faqat TelegramForbiddenError yoki aniq BadRequest xatolarida True qaytaradi.

    MUHIM XAVFSIZLIK QOIDASI:
    TelegramRetryAfter (Flood control), Timeout, Network va boshqa
    vaqtinchalik xatolarda qat'iyan False qaytaradi — foydalanuvchi aslo o'chirilmaydi!
    """
    if exc is None:
        return False

    # Flood limit yoki tarmoq xatolarida aslo foydalanuvchini o'chirib yubormaslik kerak!
    if isinstance(exc, (TelegramRetryAfter, TelegramNetworkError)):
        return False

    err_str = str(exc).lower()

    # TelegramForbiddenError — foydalanuvchi botni bloklaganda Telegram qaytaradigan asosiy xatolik
    if isinstance(exc, TelegramForbiddenError):
        return True

    # TelegramBadRequest yoki umumiy TelegramAPIError holatida xato matnini tekshiramiz
    if isinstance(exc, (TelegramBadRequest, TelegramAPIError)) or isinstance(exc, Exception):
        for phrase in GONE_PHRASES:
            if phrase in err_str:
                return True

    return False
