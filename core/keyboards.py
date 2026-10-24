from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
)
import test_db
from .config import WEBAPP_URL, ADMIN_ID, get_webapp_url

def make_webapp_button(text: str, url: str, fallback_cb: str = "open_webapp_info") -> InlineKeyboardButton:
    """Telegram WebApp faqat HTTPS talab qiladi. Agar HTTP (localhost) bo'lsa callback ishlatiladi."""
    if url.startswith("https://"):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    elif url.startswith("http://") and not ("localhost" in url or "127.0.0.1" in url):
        return InlineKeyboardButton(text=text, url=url)
    else:
        return InlineKeyboardButton(text=text, callback_data=fallback_cb)

def main_menu_kb(user_tg_id: int) -> ReplyKeyboardMarkup:
    is_adm = test_db.is_admin(user_tg_id, ADMIN_ID)
    base_url = get_webapp_url()
    
    test_webapp_url = f"{base_url}/index.html?tg_id={user_tg_id}&v=20261024_v16_live"
    results_webapp_url = f"{base_url}/results.html?tg_id={user_tg_id}&v=20261024_v16_live"
    admin_webapp_url = f"{base_url}/admin.html?tg_id={user_tg_id}&v=20261024_v16_live"

    if test_webapp_url.startswith("https://"):
        test_btn = KeyboardButton(text="📝 Test yechish", web_app=WebAppInfo(url=test_webapp_url))
    else:
        test_btn = KeyboardButton(text="📝 Test yechish")

    if results_webapp_url.startswith("https://"):
        results_btn = KeyboardButton(text="📊 Mening natijalarim", web_app=WebAppInfo(url=results_webapp_url))
    else:
        results_btn = KeyboardButton(text="📊 Mening natijalarim")

    if admin_webapp_url.startswith("https://"):
        create_test_btn = KeyboardButton(text="➕ Yangi test yaratish", web_app=WebAppInfo(url=admin_webapp_url))
    else:
        create_test_btn = KeyboardButton(text="➕ Yangi test yaratish")

    if is_adm:
        # Adminlar uchun menyu
        buttons = [
            [test_btn, create_test_btn],
            [KeyboardButton(text="📊 Test natijalari va reyting"), KeyboardButton(text="📋 Testlarni boshqarish")],
            [KeyboardButton(text="⚙️ Admin Panel")]
        ]
    else:
        # Oddiy foydalanuvchilar uchun ixcham menyu:
        # 1-qator: Test yechish (Mini App) + Mening natijalarim (Ixcham Mini App)
        # 2-qator: Yordam
        buttons = [
            [test_btn, results_btn],
            [KeyboardButton(text="ℹ️ Yordam")]
        ]

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def profile_webapp_kb(user_tg_id: int) -> InlineKeyboardMarkup:
    """Natijalar mini ilovasini ochish tugmasi."""
    base_url = get_webapp_url()
    results_url = f"{base_url}/results.html?tg_id={user_tg_id}&v=20261022_v9_q37"
    buttons = [
        [make_webapp_button("📊 Mening natijalarimni ochish", results_url, fallback_cb="open_app_info")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def results_webapp_kb(user_tg_id: int = 0) -> InlineKeyboardMarkup:
    """Natijalarni ko'rish mini ilovasi tugmasi."""
    base_url = get_webapp_url()
    qs = f"?tg_id={user_tg_id}&v=20261022_v9_q37" if user_tg_id else "?v=20261022_v9_q37"
    results_url = f"{base_url}/results.html{qs}"
    buttons = [
        [make_webapp_button("📊 Mening natijalarim", results_url, fallback_cb="open_app_info")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def contact_share_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def admin_menu_kb() -> InlineKeyboardMarkup:
    """
    Admin Panel inline menyusi — xabarlar, texnik rejim, test vaqti va adminlar boshqaruvi.
    Foydalanuvchilar boshqaruvi to'liq Asosiy Web Ilovaga (Main App) ko'chirilgan.
    """
    maint_on = test_db.is_maintenance_mode()
    maint_icon = "🔴" if maint_on else "🟢"
    maint_status = "YOQILGAN" if maint_on else "O'CHIQ"
    maint_btn_text = f"🛠 Texnik rejim: {maint_status} {maint_icon}"

    reg_open = test_db.is_registration_open()
    reg_icon = "🟢" if reg_open else "🔴"
    reg_status = "OCHIQ" if reg_open else "YOPIQ"
    reg_btn_text = f"👥 Yangi qabul: {reg_status} {reg_icon}"

    base_url = get_webapp_url()
    buttons = [
        [InlineKeyboardButton(text="📢 O'quvchilarga xabar yuborish", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton(text=maint_btn_text, callback_data="admin_toggle_maint_prompt")],
        [InlineKeyboardButton(text=reg_btn_text, callback_data="admin_toggle_registration")],
        [InlineKeyboardButton(text="👑 Adminlar boshqaruvi", callback_data="admin_manage_admins")],
        [InlineKeyboardButton(text="📊 Bot statistikasi (/stats)", callback_data="admin_stats_quick")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
