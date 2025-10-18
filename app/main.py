import os
import logging
import asyncio
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
# 🟢 1. /start → واجهة اختيار اللغة مع تأثير توهج
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    frames = [
        "╔════════════════════════════╗\n║       أهلاً بك في بوت YesFX! 👋       ║\n╚════════════════════════════╝",
        "╔════════════════════════════╗\n║       👋 Welcome to YesFX Bot!        ║\n╚════════════════════════════╝"
    ]
    msg = await update.message.reply_text(frames[0])
    # تأثير توهج بسيط
    for frame in frames:
        await asyncio.sleep(0.3)
        await msg.edit_text(frame)
    await asyncio.sleep(0.2)
    await msg.edit_text(frames[1], reply_markup=reply_markup)


# ===============================
# 🆕 2. عرض اختيار اللغة عند الرجوع مع تأثير توهج
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
        frames = [
            "╔════════════════════════════╗\n║   👋 مرحبًا مجددًا! اختر لغتك:  ║\n╚════════════════════════════╝",
            "╔════════════════════════════╗\n║  👋 Welcome again! Select your language: ║\n╚════════════════════════════╝"
        ]
        await update.callback_query.answer()
        msg = await update.callback_query.edit_message_text(frames[0])
        for frame in frames:
            await asyncio.sleep(0.25)
            await msg.edit_text(frame, reply_markup=reply_markup)
    else:
        await start(update, context)


# ===============================
# 🟣 3. عرض الأقسام الرئيسية بعد اختيار اللغة مع توهج
# ===============================
async def show_main_sections(update: Update, lang: str):
    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        frames = [
            "╔════════════════════════════╗\n║        🏷️ الأقسام الرئيسية        ║\n╚════════════════════════════╝",
            "╔════════════════════════════╗\n║ اختر القسم الذي ترغب به 👇        ║\n╚════════════════════════════╝"
        ]
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        frames = [
            "╔════════════════════════════╗\n║        🏷️ Main Sections         ║\n╚════════════════════════════╝",
            "╔════════════════════════════╗\n║ Please choose a section 👇      ║\n╚════════════════════════════╝"
        ]
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = await update.callback_query.edit_message_text(frames[0])
    for frame in frames:
        await asyncio.sleep(0.25)
        await msg.edit_text(frame, reply_markup=reply_markup)


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
# 🟡 5. التعامل مع الأقسام الفرعية + زر الرجوع مع توهج
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

    # إعداد الأقسام الفرعية
    options = []
    back_label = ""
    frames = []

    if query.data == "forex_main":
        if lang == "ar":
            options = [("📊 نسخ الصفقات", "forex_copy"), ("💬 قناة التوصيات", "forex_signals"), ("📰 الأخبار الاقتصادية", "forex_news")]
            back_label = "🔙 الرجوع للقائمة الرئيسية"
            frames = [
                "╔════════════════════════════╗\n║       💹 تداول الفوركس        ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ اختر الخدمة التي تريدها 👇      ║\n╚════════════════════════════╝"
            ]
        else:
            options = [("📊 Copy Trading", "forex_copy"), ("💬 Signals Channel", "forex_signals"), ("📰 Economic News", "forex_news")]
            back_label = "🔙 Back to main menu"
            frames = [
                "╔════════════════════════════╗\n║       💹 Forex Trading      ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ Choose the service you want 👇║\n╚════════════════════════════╝"
            ]

    elif query.data == "dev_main":
        if lang == "ar":
            options = [("📈 برمجة المؤشرات", "dev_indicators"), ("🤖 برمجة الاكسبيرتات", "dev_experts"),
                       ("💬 برمجة بوتات التليجرام", "dev_bots"), ("🌐 برمجة مواقع الويب", "dev_web")]
            back_label = "🔙 الرجوع للقائمة الرئيسية"
            frames = [
                "╔════════════════════════════╗\n║       💻 خدمات البرمجة        ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ اختر نوع الخدمة 👇           ║\n╚════════════════════════════╝"
            ]
        else:
            options = [("📈 Indicators Development", "dev_indicators"), ("🤖 Expert Advisors", "dev_experts"),
                       ("💬 Telegram Bots", "dev_bots"), ("🌐 Web Development", "dev_web")]
            back_label = "🔙 Back to main menu"
            frames = [
                "╔════════════════════════════╗\n║   💻 Programming Services    ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ Choose the type 👇           ║\n╚════════════════════════════╝"
            ]

    elif query.data == "agency_main":
        if lang == "ar":
            options = [("📄 طلب وكالة YesFX", "agency_request")]
            back_label = "🔙 الرجوع للقائمة الرئيسية"
            frames = [
                "╔════════════════════════════╗\n║       🤝 طلب وكالة YesFX      ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ اختر ما تريد 👇             ║\n╚════════════════════════════╝"
            ]
        else:
            options = [("📄 Request YesFX Partnership", "agency_request")]
            back_label = "🔙 Back to main menu"
            frames = [
                "╔════════════════════════════╗\n║   🤝 YesFX Partnership       ║\n╚════════════════════════════╝",
                "╔════════════════════════════╗\n║ Choose what you want 👇      ║\n╚════════════════════════════╝"
            ]

    else:
        await query.edit_message_text(
            text=f"🔹 {'تم اختيار الخدمة' if lang=='ar' else 'Service selected'}: {query.data}\n\n"
                 f"{'سيتم إضافة التفاصيل قريبًا...' if lang=='ar' else 'Details will be added soon...'}"
        )
        return

    # أزرار
    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in options]
    keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = await query.edit_message_text(frames[0])
    for frame in frames:
        await asyncio.sleep(0.25)
        await msg.edit_text(frame, reply_markup=reply_markup)


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
