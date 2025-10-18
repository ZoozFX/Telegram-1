import os
import logging
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from app.db import Base, engine

# إعداد سجل الأخطاء (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول في قاعدة البيانات
Base.metadata.create_all(bind=engine)

# إعداد المتغيرات
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# تأكد أن TOKEN موجود
if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN is not set!")

# إنشاء التطبيق
application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# 🟢 أمر /start — واجهة اختيار اللغة
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

# 🟣 عند الضغط على زر اللغة
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "lang_ar":
        msg = "✅ تم اختيار اللغة العربية 🇪🇬"
    else:
        msg = "✅ English language selected 🇺🇸"

    await query.edit_message_text(msg)

# ربط المعالجات
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language))

# 🟣 صفحة الفحص الأساسية
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

# 🟢 استقبال التحديثات من Telegram
@app.post(WEBHOOK_PATH)
async def process_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("❌ Webhook error")
        return {"ok": False, "error": str(e)}

# 🟣 عند بدء السيرفر
@app.on_event("startup")
async def on_startup():
    try:
        logger.info("🚀 Initializing Telegram bot...")
        await application.initialize()
        await application.startup()
        if WEBHOOK_URL and WEBHOOK_PATH:
            full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
            await application.bot.set_webhook(full_url)
            logger.info(f"✅ Webhook set to {full_url}")
        else:
            logger.warning("⚠️ WEBHOOK_URL or BOT_WEBHOOK_PATH not set")
    except Exception as e:
        logger.exception("Startup failed")

# 🟣 عند إيقاف السيرفر
@app.on_event("shutdown")
async def on_shutdown():
    try:
        logger.info("🛑 Shutting down Telegram bot...")
        await application.shutdown()
        await application.stop()
    except Exception:
        logger.exception("Error during shutdown")
