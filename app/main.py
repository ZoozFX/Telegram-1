import os
import logging
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from app.db import Base, engine

# -------------------------------
# إعداد السجلات
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول
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
# 🟢 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "╔════════════════════╗\n"
        "👋 أهلاً بك في بوت YesFX!\n"
        "╚════════════════════╝\n\n"
        "الرجاء اختيار اللغة 👇\n\n"
        "╔════════════════════╗\n"
        "Welcome to YesFX Bot!\n"
        "╚════════════════════╝\n"
        "Please select a language 👇"
    )
    await update.message.reply_text(text, reply_markup=reply_markup)

# ===============================
# 🆕 2. عرض اختيار اللغة عند الرجوع
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
        text = (
            "╔════════════════════╗\n"
            "👋 مرحبًا مجددًا!\n"
            "╚════════════════════╝\n\n"
            "الرجاء اختيار اللغة 👇\n\n"
            "╔════════════════════╗\n"
            "Welcome again!\n"
            "╚════════════════════╝\n"
            "Please select a language 👇"
        )
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
    else:
        await start(update, context)

# ===============================
# 🟣 3. عرض الأقسام الرئيسية بعد اختيار اللغة
# ===============================
async def show_main_sections(update: Update, lang: str):
    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        text = (
            "╔════════════════════╗\n"
            "🏷️ الأقسام الرئيسية\n"
            "╚════════════════════╝\n"
            "اختر القسم الذي ترغب به 👇"
        )
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        text = (
            "╔════════════════════╗\n"
            "🏷️ Main Sections\n"
            "╚════════════════════╝\n"
            "Please choose a section 👇"
        )
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)

# ===============================
# 🟢 4. عند اختيار اللغة
# ===============================
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, lang)

# ===============================
# 🟡 5. التعامل مع الأقسام الفرعية + زر الرجوع
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

    # الأقسام الرئيسية
    if query.data == "forex_main":
        if lang == "ar":
            options = [
                ("📊 نسخ الصفقات", "forex_copy"),
                ("💬 قناة التوصيات", "forex_signals"),
                ("📰 الأخبار الاقتصادية", "forex_news")
            ]
            text = (
                "╔════════════════════╗\n"
                "💹 قسم تداول الفوركس\n"
                "╚════════════════════╝\n"
                "اختر الخدمة 👇"
            )
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [
                ("📊 Copy Trading", "forex_copy"),
                ("💬 Signals Channel", "forex_signals"),
                ("📰 Economic News", "forex_news")
            ]
            text = (
                "╔════════════════════╗\n"
                "💹 Forex Trading Section\n"
                "╚════════════════════╝\n"
                "Choose a service 👇"
            )
            back_label = "🔙 Back to main menu"

    elif query.data == "dev_main":
        if lang == "ar":
            options = [
                ("📈 برمجة المؤشرات", "dev_indicators"),
                ("🤖 برمجة الاكسبيرتات", "dev_experts"),
                ("💬 برمجة بوتات التليجرام", "dev_bots"),
                ("🌐 برمجة مواقع الويب", "dev_web")
            ]
            text = (
                "╔════════════════════╗\n"
                "💻 قسم خدمات البرمجة\n"
                "╚════════════════════╝\n"
                "اختر نوع الخدمة 👇"
            )
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [
                ("📈 Indicators Development", "dev_indicators"),
                ("🤖 Expert Advisors", "dev_experts"),
                ("💬 Telegram Bots", "dev_bots"),
                ("🌐 Web Development", "dev_web")
            ]
            text = (
                "╔════════════════════╗\n"
                "💻 Programming Services\n"
                "╚════════════════════╝\n"
                "Choose the type 👇"
            )
            back_label = "🔙 Back to main menu"

    elif query.data == "agency_main":
        if lang == "ar":
            options = [("📄 طلب وكالة YesFX", "agency_request")]
            text = (
                "╔════════════════════╗\n"
                "🤝 قسم طلب وكالة\n"
                "╚════════════════════╝\n"
                "اختر 👇"
            )
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [("📄 Request YesFX Partnership", "agency_request")]
            text = (
                "╔════════════════════╗\n"
                "🤝 Partnership Section\n"
                "╚════════════════════╝\n"
                "Choose 👇"
            )
            back_label = "🔙 Back to main menu"

    else:
        # خدمات فرعية placeholder
        await query.edit_message_text(
            text=f"🔹 {'تم اختيار الخدمة' if lang=='ar' else 'Service selected'}: {query.data}\n\n"
                 f"{'سيتم إضافة التفاصيل قريبًا...' if lang=='ar' else 'Details will be added soon...'}"
        )
        return

    # أزرار الخيارات + زر الرجوع
    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in options]
    keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=text, reply_markup=reply_markup)

# ===============================
# 🔗 Handlers
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))

# ===============================
# 🟣 صفحة الفحص
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

# ===============================
# 🟢 Webhook
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
# 🚀 Startup
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
# 🛑 Shutdown
# ===============================
@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await application.shutdown()
