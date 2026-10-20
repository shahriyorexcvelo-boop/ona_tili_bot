from __future__ import annotations
import asyncio
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from aiogram.exceptions import TelegramRetryAfter
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
    messages_list: Optional[List[Dict[str, Any]]] = None
) -> tuple[int, int, str]:
    """Barcha faol (bloklanmagan) o'quvchilarga xabar tarqatish va keyinchalik o'chirish uchun ID larni saqlash."""
    users = await test_db.db(test_db.get_broadcast_users)
    sent_count = 0
    fail_count = 0
    blocked_users = []  # Botni bloklagan foydalanuvchilar

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

    for u in users:
        uid = u.get("tg_id")
        if not uid:
            continue
        try:
            user_sent_any = False
            if messages_list:
                for m in messages_list:
                    sent_msg = await bot.copy_message(
                        chat_id=uid,
                        from_chat_id=m["chat_id"],
                        message_id=m["message_id"]
                    )
                    if sent_msg and hasattr(sent_msg, "message_id"):
                        await test_db.db(test_db.record_broadcast_message, batch_id, uid, sent_msg.message_id)
                        user_sent_any = True
                    await asyncio.sleep(0.02)
            elif photo_id:
                sent_msg = await bot.send_photo(chat_id=uid, photo=photo_id, caption=caption or message_text)
                if sent_msg and hasattr(sent_msg, "message_id"):
                    await test_db.db(test_db.record_broadcast_message, batch_id, uid, sent_msg.message_id)
                    user_sent_any = True
            else:
                sent_msg = await bot.send_message(chat_id=uid, text=message_text)
                if sent_msg and hasattr(sent_msg, "message_id"):
                    await test_db.db(test_db.record_broadcast_message, batch_id, uid, sent_msg.message_id)
                    user_sent_any = True

            if user_sent_any:
                sent_count += 1
            await asyncio.sleep(0.04)
        except Exception as e:
            fail_count += 1
            if is_user_gone_error(e):
                await test_db.db(test_db.delete_user, uid, is_self_delete=True)
                blocked_users.append(u)
            log.warning(f"Broadcast xatosi user {uid}: {e}")

    await test_db.db(test_db.update_broadcast_sent_count, batch_id, sent_count)

    # Admin ga bloklagan userlar haqida xabar
    if blocked_users:
        blocked_text = "🚫 <b>Botni bloklagani uchun bazadan o'chirilgan foydalanuvchilar:</b>\n\n"
        for bu in blocked_users:
            bname = bu.get('fullname', 'Noma\'lum')
            btid = bu.get('tg_id', '-')
            blocked_text += f"• <b>{bname}</b> (ID: <code>{btid}</code>)\n"
        blocked_text += "\n<i>Ular botni to'xtatgani/bloklagani sababli bazadan to'liq o'chirildi.</i>"
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

    sem = asyncio.Semaphore(12)  # Bir vaqtning o'zida 12 ta parallel so'rov
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

async def delete_recent_bot_messages_from_all_users(count: int = 1, progress_callback=None) -> tuple[int, int]:
    """
    Barcha o'quvchilar chatidagi so'nggi bot xabarlarini tezkor o'chirish (hatto oldingilarini ham).
    """
    deleted_total = 0

    # 1. Agar bazada o'chirilmagan broadcast partiyalar bo'lsa, ularni to'g'ridan-to'g'ri o'chiramiz
    recent_batches = await test_db.db(test_db.get_recent_broadcasts, limit=max(5, count * 2))
    active_batches = [b for b in recent_batches if b.get('is_deleted') != 1]
    if active_batches:
        for b in active_batches[:count]:
            d_cnt, _ = await delete_broadcast_batch_from_users(b['batch_id'], progress_callback=progress_callback)
            deleted_total += d_cnt
        if deleted_total > 0:
            all_u = await test_db.db(test_db.get_broadcast_users)
            return deleted_total, len(all_u)

    # 2. Agar bazada partiya topilmasa, chatdagi so'nggi bot xabarlarini tezkor parallel tozalash:
    users = await test_db.db(test_db.get_broadcast_users)
    if not users:
        return 0, 0

    sem = asyncio.Semaphore(5)  # Telegram API flood limitiga tushmaslik uchun 5 ta parallel worker
    total_users = len(users)
    processed_count = [0]
    deleted_count = [0]

    async def _clean_one_user(u):
        uid = u.get("tg_id")
        if not uid:
            return
        async with sem:
            try:
                # O'quvchini bezovta qilmaslik uchun bildirishnomasiz jim (silent) probe
                probe = await bot.send_message(chat_id=uid, text=".", disable_notification=True)
                top_id = probe.message_id
                try:
                    await bot.delete_message(chat_id=uid, message_id=top_id)
                except Exception:
                    pass

                # Faqat so'nggi 3 ta ID ni tekshiramiz (ortiqcha yuzlab so'rov yubormaslik uchun)
                u_del = 0
                for mid in range(top_id - 1, max(1, top_id - 4), -1):
                    try:
                        await bot.delete_message(chat_id=uid, message_id=mid)
                        u_del += 1
                        deleted_count[0] += 1
                        if u_del >= count:
                            break
                        await asyncio.sleep(0.04)
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(min(e.retry_after, 1.5))
                        break
                    except Exception:
                        continue
            except TelegramForbiddenError:
                pass
            except TelegramRetryAfter as e:
                await asyncio.sleep(min(e.retry_after, 1.5))
            except Exception as e:
                log.debug(f"User clean exception {uid}: {e}")
            finally:
                processed_count[0] += 1
                if progress_callback:
                    try:
                        await progress_callback(processed_count[0], total_users, deleted_count[0])
                    except Exception:
                        pass

    await asyncio.gather(*[_clean_one_user(u) for u in users], return_exceptions=True)
    return deleted_count[0], processed_count[0]

