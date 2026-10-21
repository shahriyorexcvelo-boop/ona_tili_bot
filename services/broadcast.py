from __future__ import annotations
import asyncio
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
import test_db
from core.config import UZB_TZ, ADMIN_ID, log
from core.bot import bot
from core.telegram_utils import is_user_gone_error

MAINT_START_TEXT = (
    "⚠️ <b>DIQQAT: REJALI TEXNIK PROFILAKTIKA BOSHLANDI!</b>\n\n"
    "Hurmatli o'quvchilar va foydalanuvchilar!\n"
    "Hozirda bot tizimida rejali texnik profilaktika, yangilash va optimallashtirish ishlari olib borilmoqda.\n\n"
    "⏱ <b>Holat:</b> Bot vaqtincha to'xtatildi\n"
    "👨‍💻 <b>Maqsad:</b> Tizim barqarorligi va yangi imkoniyatlarni ishga tushirish\n\n"
    "✅ <i>Texnik jarayon yakunlangach, bot yana avtomatik tarzda to'liq ishga tushadi va bu haqda qo'shimcha xabar beriladi.</i>\n\n"
    "🙏 <b>Keltirilgan vaqtinchalik noqulayliklar uchun uzr so'raymiz!</b>"
)

MAINT_END_TEXT = (
    "✅ <b>XUSHXABAR: TEXNIK ISHLAR YAKUNLANDI!</b>\n\n"
    "Hurmatli o'quvchilar va foydalanuvchilar!\n"
    "Botdagi barcha texnik profilaktika va yangilash ishlari muvaffaqiyatli yakunlandi.\n\n"
    "🎉 <b>Tizim to'liq ishchi holatda!</b>\n"
    "Endi bemalol test topshirishingiz, natijalaringizni ko'rishingiz va botdan foydalanishingiz mumkin.\n\n"
    "🌟 <i>Barchangizga bilim olishda va testlarda ulkan zafarlar tilaymiz!</i>"
)

async def send_broadcast_to_users(
    message_text: str = "",
    photo_id: str = "",
    caption: str = "",
    sender_tg_id: int = 0,
    messages_list: Optional[List[Dict[str, Any]]] = None,
    batch_id: Optional[str] = None
) -> tuple[int, int, str]:
    """
    Barcha faol o'quvchilarga xabar tarqatish:
    - TelegramRetryAfter ushlanadi, e.retry_after kutib 3 martagacha qayta uriniladi.
    - Xabarlar har 50 tadan to'planib (record_broadcast_messages_batch) ommaviy yoziladi.
    - is_user_gone_error bo'lganda ma'lumot o'chirilmaydi, status='blocked_bot' qilib saqlanadi.
    - batch_id holati ('running'/'done') saqlanadi va qayta yuborilganda dublikatlar oldi olinadi.
    """
    users = await test_db.db(test_db.get_broadcast_users)
    sent_count = 0
    fail_count = 0
    blocked_users = []

    if not batch_id:
        batch_id = f"BC-{datetime.now(UZB_TZ).strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        batch_summary = ""
        if messages_list:
            batch_summary = " | ".join([m.get("desc", "Xabar") for m in messages_list])[:1000]
        else:
            batch_summary = caption or message_text

        await test_db.db(
            test_db.create_broadcast_batch,
            batch_id=batch_id,
            sender_tg_id=sender_tg_id or ADMIN_ID,
            message_text=batch_summary,
            photo_id=photo_id
        )
    else:
        await test_db.db(test_db.update_broadcast_status, batch_id, "running")

    # Allaqachon shu partiyada xabar borgan chat_id larga qayta yubormaslik (Idempotentlik)
    already_sent_ids = await test_db.db(test_db.get_broadcast_sent_chat_ids, batch_id)

    pending_records: List[tuple[str, int, int]] = []

    async def _flush_records():
        if pending_records:
            await test_db.db(test_db.record_broadcast_messages_batch, list(pending_records))
            pending_records.clear()

    for u in users:
        uid = u.get("tg_id")
        if not uid or uid in already_sent_ids:
            continue

        user_sent_any = False
        user_failed = False

        MAX_RETRIES = 3
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if messages_list:
                    for m in messages_list:
                        sent_msg = await bot.copy_message(
                            chat_id=uid,
                            from_chat_id=m["chat_id"],
                            message_id=m["message_id"]
                        )
                        if sent_msg and hasattr(sent_msg, "message_id"):
                            pending_records.append((batch_id, uid, sent_msg.message_id))
                            user_sent_any = True
                        await asyncio.sleep(0.02)
                elif photo_id:
                    sent_msg = await bot.send_photo(chat_id=uid, photo=photo_id, caption=caption or message_text)
                    if sent_msg and hasattr(sent_msg, "message_id"):
                        pending_records.append((batch_id, uid, sent_msg.message_id))
                        user_sent_any = True
                else:
                    sent_msg = await bot.send_message(chat_id=uid, text=message_text)
                    if sent_msg and hasattr(sent_msg, "message_id"):
                        pending_records.append((batch_id, uid, sent_msg.message_id))
                        user_sent_any = True

                break  # Yuborildi, qayta urinishdan chiqish
            except TelegramRetryAfter as e:
                wait_sec = min(max(float(e.retry_after), 1.0), 30.0)
                log.warning(f"Telegram Flood control (Retry-After: {wait_sec}s) user {uid} (urinish {attempt}/{MAX_RETRIES})")
                await asyncio.sleep(wait_sec)
                if attempt == MAX_RETRIES:
                    user_failed = True
                    fail_count += 1
            except Exception as e:
                user_failed = True
                fail_count += 1
                if is_user_gone_error(e):
                    await test_db.db(test_db.mark_user_blocked_bot, uid)
                    blocked_users.append(u)
                log.warning(f"Broadcast xatosi user {uid}: {e}")
                break

        if user_sent_any and not user_failed:
            sent_count += 1
            already_sent_ids.add(uid)

        # Har 50 ta xabarda bitta executemany bilan yozish
        if len(pending_records) >= 50:
            await _flush_records()

        await asyncio.sleep(0.04)

    # Qolgan to'plangan yozuvlarni bazaga tushirish
    await _flush_records()

    await test_db.db(test_db.update_broadcast_sent_count, batch_id, len(already_sent_ids))
    await test_db.db(test_db.update_broadcast_status, batch_id, "done")

    # Admin ga bloklagan userlar haqida xabar
    if blocked_users:
        blocked_text = "🚫 <b>Botni bloklagani aniqlangan foydalanuvchilar (status='blocked_bot'):</b>\n\n"
        for bu in blocked_users:
            bname = bu.get('fullname', 'Noma\'lum')
            btid = bu.get('tg_id', '-')
            blocked_text += f"• <b>{bname}</b> (ID: <code>{btid}</code>)\n"
        blocked_text += "\n<i>Ushbu foydalanuvchilar ma'lumotlari saqlab qolindi va kelgusi xabarlardan chiqarildi.</i>"
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=blocked_text)
        except Exception:
            pass

    return sent_count, fail_count, batch_id


async def delete_broadcast_batch_from_users(batch_id: str, progress_callback=None) -> tuple[int, int]:
    """Berilgan batch_id bo'yicha barcha yuborilgan xabarlarni o'quvchilar chatidan tezkor parallel o'chirib tashlash."""
    msgs = await test_db.db(test_db.get_broadcast_messages, batch_id)
    if not msgs:
        await test_db.db(test_db.mark_broadcast_deleted, batch_id)
        return 0, 0

    sem = asyncio.Semaphore(12)
    deleted_count = [0]
    fail_count = [0]
    total = len(msgs)
    processed = [0]

    async def _del_one(m):
        chat_id = m.get("chat_id")
        msg_id = m.get("message_id")
        if not chat_id or not msg_id:
            return
        async with sem:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted_count[0] += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(min(e.retry_after, 1.5))
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    deleted_count[0] += 1
                except Exception:
                    fail_count[0] += 1
            except Exception:
                fail_count[0] += 1
            finally:
                processed[0] += 1
                if progress_callback:
                    try:
                        await progress_callback(processed[0], total, deleted_count[0])
                    except Exception:
                        pass

    await asyncio.gather(*[_del_one(m) for m in msgs], return_exceptions=True)
    await test_db.db(test_db.mark_broadcast_deleted, batch_id)
    return deleted_count[0], fail_count[0]
