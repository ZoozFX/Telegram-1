import os
import logging
from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from app.db import Base, engine

# إعداد سجل الأخطاء
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول في قاعدة البيانات (إن وجدت)
Base.metadata.create_all(bind=engine)

# متغيرات البيئة
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# تحقق من وجود التوكن
if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

# إنشاء تطبيق Telegram و FastAPI
application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()


# 🟢 1. أمر /start → اختيار اللغة
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


# 🟣 2. دالة عرض القائمة بناءً على اللغة
async def show_main_menu(update: Update, lang: str):
    if lang == "ar":
        options = [
            "📊 خدمة نسخ الصفقات",
            "💬 قناة التوصيات",
            "🧑‍💻 خدمات البرمجة والتصميم",
            "📰 الأخبار الاقتصادية",
            "📈 التحليلات الفنية والأساسية",
            "🎓 تعليم التداول",
            "💻 تعليم البرمجة",
            "📞 التواصل مع الدعم الفني",
            "🌐 زيارة الموقع الرسمي",
            "📑 تقارير الآداء"
        ]
    else:
        options = [
            "📊 Copy Trading Service",
            "💬 Signals Channel",
            "🧑‍💻 Programming & Design Services",
            "📰 Economic News",
            "📈 Technical & Fundamental Analysis",
            "🎓 Trading Education",
            "💻 Programming Education",
            "📞 Contact Support",
            "🌐 Visit Official Website",
            "📑 Performance Reports"
        ]

    # ترتيب الأزرار في أعمدة أنيقة (2 أو 3 بالصف)
    keyboard = []
    for i in range(0, len(options), 2):
        row = []
        for opt in options[i:i+2]:
            row.append(InlineKeyboardButton(opt, callback_data=f"menu_{opt[:10]}"))
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if lang == "ar":
        await update.callback_query.edit_message_text(
            "✅ تم اختيار اللغة العربية 🇪🇬\n\nاختر الخدمة التي ترغب بها 👇",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.edit_message_text(
            "✅ English language selected 🇺🇸\n\nPlease choose a service 👇",
            reply_markup=reply_markup
        )


# 🟢 3. عند اختيار اللغة
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang  # حفظ اللغة مؤقتًا للمستخدم

    # عرض القائمة بناءً على اللغة
    await show_main_menu(update, lang)


# 🟢 4. (اختياري) رد على ضغط أي زر داخل القائمة
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # يمكن لاحقًا تخصيص سلوك كل زر
    await query.edit_message_text(
        text=f"🔹 تم اختيار: {query.data}\n\n(سيتم إضافة التفاصيل لاحقًا)"
    )


# 🔗 إضافة الـ Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))


# 🟣 صفحة الفحص الأساسية
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}


# 🟢 مسار الـ webhook
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


# 🚀 بدء التطبيق
@app.on_event("startup")
async def on_startup():
    try:
        logger.info("🚀 Initializing bot...")
        await application.initialize()
        await application.startup()
        if WEBHOOK_URL and WEBHOOK_PATH:
            full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
            await application.bot.set_webhook(full_url)
            logger.info(f"✅ Webhook set to {full_url}")
        else:
            logger.warning("⚠️ WEBHOOK_URL or BOT_WEBHOOK_PATH not set")
    except Exception:
        logger.exception("Startup failed")


# 🛑 إيقاف التطبيق
@app.on_event("shutdown")
async def on_shutdown():
    try:
        logger.info("🛑 Shutting down bot...")
        await application.shutdown()
        await application.stop()
    except Exception:
        logger.exception("Error during shutdown")
