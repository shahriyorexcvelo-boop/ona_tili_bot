from __future__ import annotations
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


UZB_TZ = timezone(timedelta(hours=5))

def format_uzb_time(timestamp: Optional[float] = None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """O'zbekiston (Toshkent, UTC+5) vaqti bo'yicha formatlash"""
    if timestamp is None:
        dt = datetime.now(UZB_TZ)
    else:
        dt = datetime.fromtimestamp(timestamp, tz=UZB_TZ)
    return dt.strftime(fmt)

# ── TELEGRAM CUSTOM EMOJI & ESTETIK DIZAYN TO'PLAMI ───────────
TG_ICONS = {
    "star_gold": "5368324170671202286",      # ⭐ Oltin yulduzcha
    "star_premium": "5429674068305786451",   # ⭐ Premium yulduzcha
    "star_gradient": "5429674068305786452",  # 🌟 Maxsus yulduzcha
    "user_outline": "5429674068305786448",   # 👤 Oq kontur foydalanuvchi
    "warning": "5429674068305786449",        # ⚠️ Oq kontur ogohlantirish
    "freeze": "5429674068305786455",         # ❄️ Muzlatish
    "chat_check": "5429674068305786450",     # 💬 SMS / Chat check
    "wallet": "5429674068305786453",         # 👛 Hamyon / Balans
    "clock": "5429674068305786458",          # ⏰ Soat / Taymer
    "success": "5429674068305786459",        # ✅ Tasdiq / Muvaffaqiyat
    "error": "5429674068305786460",          # ❌ Xatolik
    "key": "5429674068305786461",            # 🔑 Kalit / Parol / Token
    "card": "5429674068305786462",           # 💳 Karta / To'lov
    "order": "5429674068305786465",          # 📋 Ro'yxat / Natija
}

def tg_e(icon_name: str, fallback: str = "•") -> str:
    """Telegram Custom Emoji tegi: <tg-emoji emoji-id="...">fallback</tg-emoji>"""
    eid = TG_ICONS.get(icon_name)
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

_dashboard_overview_cache = {"data": None, "ts": 0}
_dashboard_users_cache = {"data": None, "ts": 0}
_dashboard_tests_cache = {"data": None, "ts": 0}

def invalidate_dashboard_cache():
    global _dashboard_overview_cache, _dashboard_users_cache, _dashboard_tests_cache
    _dashboard_overview_cache["ts"] = 0
    _dashboard_users_cache["ts"] = 0
    _dashboard_tests_cache["ts"] = 0

def get_test_schedule_status(test: Dict[str, Any]) -> Dict[str, Any]:
    """
    Testning joriy vaqtga (UZB_TZ) nisbatan aniq holatini hisoblaydi:
    - is_upcoming: Test boshlanish vaqti hali kelmagan
    - is_active: Test ayni paytda faol va yechish mumkin
    - is_closed: Test vaqti tugagan yoki to'xtatilgan
    """
    now_uzb = datetime.now(UZB_TZ)
    now_minutes = now_uzb.hour * 60 + now_uzb.minute
    today_date = now_uzb.date()

    sdate = str(test.get('scheduled_date') or '').strip()
    sstart = str(test.get('scheduled_start') or '').strip()
    send = str(test.get('scheduled_end') or '').strip()
    raw_active = (test.get('is_active', 1) == 1)

    # Agar jadval belgilanmagan bo'lsa
    if not sstart:
        return {
            "is_upcoming": False,
            "is_active": raw_active,
            "is_closed": not raw_active,
            "reason": "no_schedule"
        }

    # Sana tekshiruvi (agar sdate berilgan bo'lsa)
    test_date_obj = None
    if sdate:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y.%m.%d"):
            try:
                test_date_obj = datetime.strptime(sdate, fmt).date()
                break
            except Exception:
                pass

    if test_date_obj:
        if test_date_obj > today_date:
            return {
                "is_upcoming": True,
                "is_active": False,
                "is_closed": False,
                "reason": "future_date"
            }
        elif test_date_obj < today_date:
            return {
                "is_upcoming": False,
                "is_active": False,
                "is_closed": True,
                "reason": "past_date"
            }

    # Bugungi kun bo'yicha start va end daqiqalarini tekshiramiz
    start_minutes = None
    end_minutes = None
    try:
        sh, sm = map(int, sstart.split(":"))
        start_minutes = sh * 60 + sm
    except Exception:
        pass

    if send:
        try:
            eh, em = map(int, send.split(":"))
            end_minutes = eh * 60 + em
        except Exception:
            pass

    if start_minutes is not None:
        if end_minutes is not None and end_minutes < start_minutes:
            # Yarim tun orqali o'tuvchi test (masalan 23:30 dan 00:30 gacha)
            if now_minutes >= start_minutes or now_minutes < end_minutes:
                return {
                    "is_upcoming": False,
                    "is_active": raw_active,
                    "is_closed": not raw_active,
                    "reason": "running_overnight"
                }
            else:
                return {
                    "is_upcoming": True,
                    "is_active": False,
                    "is_closed": False,
                    "reason": "before_start_time"
                }
        else:
            # Bir kunlik normal vaqt oralig'i
            if now_minutes < start_minutes:
                return {
                    "is_upcoming": True,
                    "is_active": False,
                    "is_closed": False,
                    "reason": "before_start_time"
                }
            elif end_minutes is not None and now_minutes >= end_minutes:
                return {
                    "is_upcoming": False,
                    "is_active": False,
                    "is_closed": True,
                    "reason": "after_end_time"
                }
            else:
                return {
                    "is_upcoming": False,
                    "is_active": raw_active,
                    "is_closed": not raw_active,
                    "reason": "running"
                }

    return {
        "is_upcoming": False,
        "is_active": raw_active,
        "is_closed": not raw_active,
        "reason": "default"
    }

# ── 45 DAQIQALIK TOPSHIRISH CHEKLOVI (MILLIY SERTIFIKAT STANDARTI) ──
def get_test_min_submit_info(test: Dict[str, Any]) -> Dict[str, Any]:
    """
    Test boshlanganidan keyingi dastlabki 45 daqiqa davomida javob yuborishni cheklash holatini aniqlaydi.
    """
    now_uzb = datetime.now(UZB_TZ)
    now_ts = now_uzb.timestamp()

    sdate = str(test.get('scheduled_date') or '').strip()
    sstart = str(test.get('scheduled_start') or '').strip()

    start_dt = None
    if sstart:
        test_date = now_uzb.date()
        if sdate:
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y.%m.%d"):
                try:
                    test_date = datetime.strptime(sdate, fmt).date()
                    break
                except Exception:
                    pass
        try:
            sh, sm = map(int, sstart.split(":"))
            start_dt = datetime(test_date.year, test_date.month, test_date.day, sh, sm, 0, tzinfo=UZB_TZ)
        except Exception:
            start_dt = None

    if not start_dt:
        created_at = test.get('created_at')
        if created_at:
            start_dt = datetime.fromtimestamp(created_at, tz=UZB_TZ)
        else:
            start_dt = now_uzb

    start_ts = start_dt.timestamp()
    unlock_dt = start_dt + timedelta(minutes=45)
    unlock_ts = unlock_dt.timestamp()

    if now_ts < unlock_ts:
        rem_sec = max(0, int(unlock_ts - now_ts))
        return {
            "can_submit": False,
            "remaining_seconds": rem_sec,
            "start_time_str": start_dt.strftime("%H:%M"),
            "unlock_time_str": unlock_dt.strftime("%H:%M:%S"),
            "is_before_start": (now_ts < start_ts)
        }

    return {
        "can_submit": True,
        "remaining_seconds": 0,
        "start_time_str": start_dt.strftime("%H:%M"),
        "unlock_time_str": unlock_dt.strftime("%H:%M:%S"),
        "is_before_start": False
    }

# ── TUN REJIMI: 23:00 – 07:00 ────────────────────────────
WORK_START_HOUR = 7   # 07:00 Toshkent
WORK_END_HOUR   = 23  # 23:00 Toshkent

def is_working_hours() -> bool:
    """Hozir ish vaqti (07:00–23:00 Toshkent)mi?"""
    now_hour = datetime.now(UZB_TZ).hour
    return WORK_START_HOUR <= now_hour < WORK_END_HOUR

def get_night_message() -> str:
    """Tun rejimi xabari."""
    now = datetime.now(UZB_TZ)
    if now.hour < WORK_START_HOUR:
        wait_h = WORK_START_HOUR - now.hour
        wait_text = f"{wait_h} soatdan so'ng (07:00 da)"
    else:
        wait_text = "ertaga ertalab 07:00 da"
    return (
        f"🌙 <b>Tun rejimi — Bot hozir dam olmoqda</b>\n\n"
        f"⏰ <b>Ish vaqti:</b> har kuni 07:00 – 23:00 (Toshkent)\n"
        f"🕐 <b>Hozir:</b> {now.strftime('%H:%M')}\n\n"
        f"✅ Bot <b>{wait_text}</b> yana faol bo'ladi.\n\n"
        f"<i>Iltimos, ish vaqtida qayta murojaat qiling!</i> 🙏\n\n"
        f"⛔️ <b>Iltimos, qayta /start yoki boshqa tugmalarni bosmang</b> — "
        f"har bir xabar serverni keraksiz uyg'otadi va bot tezroq o'chib qolishi mumkin."
    )

def get_maintenance_message() -> str:
    """Texnik profilaktika rejimi xabari."""
    now = datetime.now(UZB_TZ)
    return (
        "🛠 <b>Hozirda botda texnik profilaktika ishlari olib borilmoqda!</b>\n\n"
        "Hurmatli foydalanuvchi, tizim barqarorligini oshirish, yangi imkoniyatlarni sozlash "
        "va ma'lumotlar xavfsizligini ta'minlash maqsadida bot vaqtincha to'xtatildi.\n\n"
        f"🕐 <b>Vaqt:</b> {now.strftime('%H:%M')} (Toshkent)\n"
        "⏱ <b>Holat:</b> Rejali texnik tanaffus\n"
        "👨‍💻 <b>Bajarilmoqda:</b> Tizim yangilanishi va optimallashtirish\n\n"
        "✅ <i>Tez orada barcha xizmatlar to'liq va odatdagidek qayta tiklanadi.</i>\n\n"
        "🙏 <b>Keltirilgan vaqtinchalik noqulayliklar uchun uzr so'raymiz!</b>"
    )

# ── SOZLAMALAR ────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    # Lokal ishlab chiqish yoki test uchun ogohlantirish, runtime da tekshiriladi
    BOT_TOKEN = "MOCK_TOKEN_UNSET"

ADMIN_ID = int(os.getenv("ADMIN_ID", "8039427064"))
PORT = int(os.getenv("PORT", "8080"))

_raw_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBAPP_URL", "")
if _raw_url:
    if not _raw_url.startswith("http"):
        _raw_url = f"https://{_raw_url}"
    WEBAPP_URL = _raw_url.rstrip("/")
else:
    WEBAPP_URL = "https://ona-tili-bot.onrender.com"

CACHED_BOT_USERNAME = os.getenv("BOT_USERNAME", "demo_rashonatili_bot")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("OnaTiliRasch")
