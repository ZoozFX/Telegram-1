import os
import logging
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from app.db import Base, engine

# إعداد logging واضح للـ Render logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول في قاعدة البيانات (إذا لزم)
Base.metadata.create_all(bind=engine)

# متغيرات بيئة
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("TELEGRAM_TOKEN is not set! Set it in environment variables.")

# تهيئة application (PTB)
application = ApplicationBuilder().token(TOKEN).build()

# ------------------ Handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = (user.language_code or "en").lower()
    if lang.startswith("ar"):
        msg = f"👋 أهلاً {user.first_name}! مرحباً بك في البوت!"
    else:
        msg = f"👋 Hello {user.first_name}! Welcome to the bot!"
    await update.message.reply_text(msg)

application.add_handler(CommandHandler("start", start))
# -----------------------------------------------

# FastAPI app
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

# webhook endpoint
@app.post(WEBHOOK_PATH)
async def process_webhook(request: Request):
    try:
        data = await request.json()
        logger.info("Received update: %s", data.get("update_id", "<no id>"))
        update = Update.de_json(data, application.bot)  # use application.bot
        # process update via application
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        # Log full exception with stacktrace so we can see root cause in Render logs
        logger.exception("❌ Webhook error while processing update")
        return {"ok": False, "error": str(e)}

# startup: initialize and start the PTB application and set webhook
@app.on_event("startup")
async def on_startup():
    try:
        logger.info("Starting telegram Application (initialize + startup)...")
        await application.initialize()  # prepares the app (listeners, job queue, etc.)
        await application.startup()     # runs startup tasks
        # set webhook using application.bot (bot ready after initialize)
        if WEBHOOK_URL and WEBHOOK_PATH:
            full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
            await application.bot.set_webhook(full_url)
            logger.info("✅ Webhook set to %s", full_url)
        else:
            logger.warning("WEBHOOK_URL or BOT_WEBHOOK_PATH not set; webhook not configured.")
    except Exception as e:
        logger.exception("Failed during startup")

# shutdown: gracefully stop the application
@app.on_event("shutdown")
async def on_shutdown():
    try:
        logger.info("Shutting down telegram Application (shutdown + stop)...")
        await application.shutdown()
        await application.stop()
    except Exception:
        logger.exception("Error during application shutdown")
