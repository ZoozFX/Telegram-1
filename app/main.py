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

# إعداد السجلات
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

# 🟢 1. /start لاختيار اللغة
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "👋 أهلاً بك!\n\n"
        "Welcome!\n\n"
        "Please choose your language below 👇\n"
        "الرجاء اختيار لغتك أدناه 👇"
    )
    await update.message.reply_text(text, reply_markup=reply_markup)


# 🟣 2. إظهار الأقسام الرئيسية بعد اختيار اللغة
async def show_main_sections(update: Update, lang: str):
    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        text = "✅ تم اختيار اللغة العربية 🇪🇬\n\nاختر القسم الذي ترغب به 👇"
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        text = "✅ English language selected 🇺🇸\n\nPlease choose a section 👇"

    keyboard = [
        [InlineKeyboardButton(name, callback_data=callback)]
        for name, callback in sections
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(text, reply_markup=reply_markup)


# 🟢 3. التعامل مع اللغة المختارة
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, lang)


# 🟡 4. التعامل مع الأقسام الفرعية
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    if query.data == "forex_main":
        if lang == "ar":
            options = [
                ("📊 نسخ الصفقات", "forex_copy"),
                ("💬 قناة التوصيات", "forex_signals"),
                ("📰 الأخبار الاقتصادية", "forex_news")
            ]
            text = "💹 قسم تداول الفوركس:\nاختر الخدمة 👇"
        else:
            options = [
                ("📊 Copy Trading", "forex_copy"),
                ("💬 Signals Channel", "forex_signals"),
                ("📰 Economic News", "forex_news")
            ]
            text = "💹 Forex Trading Section:\nChoose a service 👇"

    elif query.data == "dev_main":
        if lang == "ar":
            options = [
                ("📈 برمجة المؤشرات", "dev_indicators"),
                ("🤖 برمجة الاكسبيرتات", "dev_experts"),
                ("💬 برمجة بوتات التليجرام", "dev_bots"),
                ("🌐 برمجة مواقع الويب", "dev_web")
            ]
            text = "💻 قسم خدمات البرمجة:\nاختر نوع الخدمة 👇"
        else:
            options = [
                ("📈 Indicators Development", "dev_indicators"),
                ("🤖 Expert Advisors", "dev_experts"),
                ("💬 Telegram Bots", "dev_bots"),
                ("🌐 Web Development", "dev_web")
            ]
            text = "💻 Programming Services:\nChoose the type 👇"

    elif query.data == "agency_main":
        if lang == "ar":
            options = [("📄 طلب وكالة YesFX", "agency_request")]
            text = "🤝 قسم طلب وكالة:\nاختر 👇"
        else:
            options = [("📄 Request YesFX Partnership", "agency_request")]
            text = "🤝 Partnership Section:\nChoose 👇"

    else:
        text = "🔹 سيتم إضافة هذه الميزة قريبًا!"
        options = []

    if options:
        keyboard = [
            [InlineKeyboardButton(name, callback_data=callback)]
            for name, callback in options
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text=text)


# 🔗 Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))


# 🟣 صفحة الفحص
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}


# 🟢 webhook
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


# 🚀 Startup
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


# 🛑 Shutdown
@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await application.shutdown()
