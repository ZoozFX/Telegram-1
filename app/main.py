import os
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
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

app = FastAPI()


# 🟢 دالة /start — تظهر واجهة اختيار اللغة
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


# 🟣 دالة لمعالجة اختيار اللغة
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "lang_ar":
        msg = "✅ تم اختيار اللغة العربية 🇪🇬"
    else:
        msg = "✅ English language selected 🇺🇸"

    await query.edit_message_text(msg)


# 🔵 ربط الأوامر والمعالجات
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language))


# 🟣 صفحة الفحص الأساسية
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}


# 🟢 مسار الـ webhook
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


# 🟣 تشغيل الـ webhook عند بدء التطبيق
@app.on_event("startup")
async def on_startup():
    full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(full_url)
    print(f"✅ Webhook set to {full_url}")
