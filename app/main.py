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

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء الجداول في قاعدة البيانات (إن وجدت)
Base.metadata.create_all(bind=engine)

# إعداد المتغيرات
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN is not set")

# تهيئة التطبيقين
application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()


# 🟢 دالة /start → اختيار اللغة
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


# 🟣 عرض القائمة الرئيسية حسب اللغة
async def show_main_menu(update: Update, lang: str):
    if lang == "ar":
        buttons = [
            [InlineKeyboardButton("💹 تداول الفوركس", callback_data="main_forex")],
            [InlineKeyboardButton("💻 خدمات البرمجة", callback_data="main_programming")],
            [InlineKeyboardButton("🏢 طلب وكالة YesFX", callback_data="main_agency")]
        ]
        text = "📋 اختر القسم الذي ترغب بالدخول إليه 👇"
    else:
        buttons = [
            [InlineKeyboardButton("💹 Forex Trading", callback_data="main_forex")],
            [InlineKeyboardButton("💻 Programming Services", callback_data="main_programming")],
            [InlineKeyboardButton("🏢 Request YesFX Agency", callback_data="main_agency")]
        ]
        text = "📋 Please choose a section below 👇"

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)


# 🟢 دالة عرض الأقسام الفرعية حسب القسم واللغة
async def show_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, main_menu: str):
    lang = context.user_data.get("lang", "ar")

    if lang == "ar":
        if main_menu == "forex":
            title = "💹 قسم تداول الفوركس"
            options = [
                ("📊 نسخ الصفقات", "sub_copytrading"),
                ("💬 قناة التوصيات", "sub_signals"),
                ("📰 الأخبار الاقتصادية", "sub_news")
            ]
        elif main_menu == "programming":
            title = "💻 قسم خدمات البرمجة"
            options = [
                ("📈 برمجة المؤشرات", "sub_indicators"),
                ("🤖 برمجة اكسبيرتات التداول", "sub_experts"),
                ("🤖 برمجة بوتات التليجرام", "sub_telegram_bots"),
                ("🌐 برمجة مواقع الويب", "sub_websites")
            ]
        elif main_menu == "agency":
            title = "🏢 طلب وكالة YesFX"
            options = [
                ("📝 طلب وكالة لأنظمة التداول", "sub_agency_request")
            ]
        back_text = "⬅️ العودة للقائمة الرئيسية"
    else:
        if main_menu == "forex":
            title = "💹 Forex Trading Section"
            options = [
                ("📊 Copy Trading", "sub_copytrading"),
                ("💬 Signals Channel", "sub_signals"),
                ("📰 Economic News", "sub_news")
            ]
        elif main_menu == "programming":
            title = "💻 Programming Services Section"
            options = [
                ("📈 Indicator Development", "sub_indicators"),
                ("🤖 Expert Advisor Development", "sub_experts"),
                ("🤖 Telegram Bot Development", "sub_telegram_bots"),
                ("🌐 Website Development", "sub_websites")
            ]
        elif main_menu == "agency":
            title = "🏢 Request YesFX Agency"
            options = [
                ("📝 Request Trading Systems Agency", "sub_agency_request")
            ]
        back_text = "⬅️ Back to Main Menu"

    keyboard = [[InlineKeyboardButton(text, callback_data=data)] for text, data in options]
    keyboard.append([InlineKeyboardButton(back_text, callback_data="go_back_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(title, reply_markup=reply_markup)


# 🟢 عند اختيار اللغة
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang

    await show_main_menu(update, lang)


# 🟢 عند اختيار قسم رئيسي
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if "forex" in query.data:
        await show_submenu(update, context, "forex")
    elif "programming" in query.data:
        await show_submenu(update, context, "programming")
    elif "agency" in query.data:
        await show_submenu(update, context, "agency")


# 🟢 عند الضغط على العودة
async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")
    await show_main_menu(update, lang)


# 🟣 عند الضغط على قسم فرعي (يمكن تخصيص الرد لاحقًا)
async def submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"✅ تم اختيار: {query.data}\n\n(سيتم إضافة التفاصيل لاحقًا)")


# 🔗 إضافة المعالجات
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^main_"))
application.add_handler(CallbackQueryHandler(back_to_main, pattern="^go_back_main$"))
application.add_handler(CallbackQueryHandler(submenu_handler, pattern="^sub_"))


# 🟣 صفحة الفحص
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}


# 🟢 Webhook endpoint
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


# 🚀 Startup
@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Starting bot...")
    await application.initialize()
    await application.startup()
    if WEBHOOK_URL and WEBHOOK_PATH:
        full_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        await application.bot.set_webhook(full_url)
        logger.info(f"✅ Webhook set to {full_url}")


# 🛑 Shutdown
@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Shutting down...")
    await application.shutdown()
    await application.stop()
