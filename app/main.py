import os
import logging
import asyncio
import html
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from telegram.constants import ParseMode
from app.db import Base, engine

# -------------------------------
# إعداد السجلات
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول (إن وجدت)
Base.metadata.create_all(bind=engine)

# متغيرات البيئة
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

# إنشاء تطبيق Telegram
application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# ===============================
# دوال تنسيق الصندوق مع دعم إرسالـه كـ HTML <pre>
# ===============================

def build_ascii_box_lines(text: str, width: int = 27) -> str:
    """
    يبني صندوق ASCII بمحتوى نصي فقط (بدون إيموجي داخل الصندوق).
    يعيد سلسلة نصية جاهزة لتوضع داخل <pre>...<pre>.
    """
    lines = text.split("\n")
    # نستخدم width كعدد أعمدة داخل الصندوق
    border = "═" * width
    boxed = []
    boxed.append(f"╔{border}╗")
    for line in lines:
        # trim فقط، لا تضيف أي ايقونات هنا
        content = line.strip()
        # نحاول توزيع المسافات مركزيًا بحسب عدد الأحرف البسيط
        # لأن داخل <pre> سيكون خط ثابت العرض، len كافي هنا
        content_len = len(content)
        padding_total = max(width - content_len, 0)
        left = padding_total // 2
        right = padding_total - left
        boxed.append(f"║{' ' * left}{content}{' ' * right}║")
    boxed.append(f"╚{border}╝")
    return "\n".join(boxed)

def boxed_text_as_html(text: str, width: int = 27, icon: str = "") -> str:
    """
    يرجع نص HTML جاهز للإرسال:
    - يضع الـ icon (إيموجي) خارِج كتلة <pre> (حتى لا يكسر المحاذاة)
    - ويغلف الصندوق داخل <pre> مع هروب أحرف HTML
    """
    box = build_ascii_box_lines(text, width=width)
    # هروب أحرف HTML داخل الصندوق
    escaped_box = html.escape(box)
    escaped_icon = html.escape(icon) if icon else ""
    if escaped_icon:
        # نجعل الإيموجي على سطر قبل <pre> لعرض جميل
        return f"{escaped_icon}\n<pre>{escaped_box}</pre>"
    else:
        return f"<pre>{escaped_box}</pre>"

# ===============================
# 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # استخدمنا icon خارج الصندوق لضمان تساوي الأعمدة
    html_ar = boxed_text_as_html("أهلا بك في بوت YesFX!", width=33, icon="🌟")
    html_en = boxed_text_as_html("Welcome to YesFX Bot!", width=33, icon="👋")

    # اجمع الرسالتين — نرسل مع parse_mode=HTML ليظهر <pre> ثابت العرض
    full_html = f"{html_ar}\n{html_en}"
    # استخدام reply_text مع parse_mode و reply_markup
    await update.message.reply_text(full_html, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ===============================
# 2. عرض اختيار اللغة عند الرجوع
# ===============================
async def show_language_selection_via_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        keyboard = [
            [
                InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
                InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        html_ar = boxed_text_as_html("مرحبًا مجددًا!", width=33, icon="🔁")
        html_en = boxed_text_as_html("Welcome again!", width=33, icon="🔁")
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(f"{html_ar}\n{html_en}", reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await start(update, context)

# ===============================
# 3. عرض الأقسام الرئيسية بعد اختيار اللغة
# ===============================
async def show_main_sections(update: Update, lang: str):
    if not update.callback_query:
        return

    callback_query = update.callback_query

    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        # عنوان داخل الصندوق بدون إيموجي داخلي — الإيموجي فوق الصندوق
        html_box = boxed_text_as_html("الأقسام الرئيسية", width=33, icon="✨🏷️✨")
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        html_box = boxed_text_as_html("Main Sections", width=33, icon="✨🏷️✨")
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await callback_query.edit_message_text(html_box, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ===============================
# 4. عند اختيار اللغة
# ===============================
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, lang)

# ===============================
# 5. التعامل مع الأقسام الفرعية + زر الرجوع
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    # 🔙 الرجوع لاختيار اللغة
    if query.data == "back_language":
        await show_language_selection_via_query(update, context)
        return

    # 🔙 الرجوع إلى القائمة الرئيسية
    if query.data == "back_main":
        await show_main_sections(update, lang)
        return

    sections_data = {
        "forex_main": {
            "ar": ["📊 نسخ الصفقات", "💬 قناة التوصيات", "📰 الأخبار الاقتصادية"],
            "en": ["📊 Copy Trading", "💬 Signals Channel", "📰 Economic News"],
            "title_ar": "تداول الفوركس",
            "title_en": "Forex Trading"
        },
        "dev_main": {
            "ar": ["📈 برمجة المؤشرات", "🤖 برمجة الاكسبيرتات", "💬 بوتات التليجرام", "🌐 مواقع الويب"],
            "en": ["📈 Indicators", "🤖 Expert Advisors", "💬 Telegram Bots", "🌐 Web Development"],
            "title_ar": "خدمات البرمجة",
            "title_en": "Programming Services"
        },
        "agency_main": {
            "ar": ["📄 طلب وكالة YesFX"],
            "en": ["📄 Request YesFX Partnership"],
            "title_ar": "طلب وكالة",
            "title_en": "Partnership"
        }
    }

    if query.data in sections_data:
        data = sections_data[query.data]
        options = data[lang]
        title = data[f"title_{lang}"]
        html_box = boxed_text_as_html(title, width=33, icon="💠")
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(html_box, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}")

# ===============================
# ربط الـ Handlers
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))

# ===============================
# صفحة الفحص
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

# ===============================
# Webhook
# ===============================
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("Webhook error")
        return {"ok": False, "error": str(e)}

# ===============================
# Startup
# ===============================
@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Starting bot...")
    await application.initialize()
    if WEBHOOK_URL and WEBHOOK_PATH:
        full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        await application.bot.set_webhook(full_url)
        logger.info(f"✅ Webhook set to {full_url}")
    else:
        logger.warning("⚠️ WEBHOOK_URL or BOT_WEBHOOK_PATH not set")

# ===============================
# Shutdown
# ===============================
@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await application.shutdown()
