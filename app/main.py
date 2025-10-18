import os
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from app.utils import setup_webhook
from app.db import Base, engine

# إنشاء الجداول في قاعدة البيانات
Base.metadata.create_all(bind=engine)

# إعداد المتغيرات
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# تهيئة البوت والتطبيق
bot = Bot(token=TOKEN)
application = ApplicationBuilder().token(TOKEN).build()

# إنشاء FastAPI app
app = FastAPI()


# 🟢 أوامر البوت
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = (user.language_code or "en").lower()
    if lang.startswith("ar"):
        msg = f"👋 أهلاً {user.first_name}! مرحباً بك في البوت!"
    else:
        msg = f"👋 Hello {user.first_name}! Welcome to the bot!"
    await update.message.reply_text(msg)


# ربط الأوامر
application.add_handler(CommandHandler("start", start))


# 🟣 صفحة اختبار بسيطة
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}


# 🟢 استقبال رسائل الـ Webhook من Telegram
@app.post(WEBHOOK_PATH)
async def process_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        print("❌ Webhook error:", e)
        return {"ok": False, "error": str(e)}


# 🟣 عند تشغيل التطبيق، ضبط الـ Webhook
@app.on_event("startup")
async def on_startup():
    try:
        full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(full_url)  # ✅ يجب استخدام await هنا
        print(f"✅ Webhook set to {full_url}")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")
