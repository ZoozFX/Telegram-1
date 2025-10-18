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
# 🎨 تصميمات ASCII محسنة
# ===============================
def get_welcome_design():
    return (
        "✨" + "═" * 38 + "✨\n"
        "            🚀 IYesFX Bot 🚀\n"
        "✨" + "═" * 38 + "✨\n\n"
        "🕌       أهلاً وسهلاً بك في بوت IYesFX\n"
        "🇺🇸       Welcome to YesFX Bot!\n\n"
        "🕒 " + "─" * 36 + " 🕒\n"
        "           ⏰ 2:35 AM ⏰\n"
        "🕒 " + "─" * 36 + " 🕒"
    )

def get_language_design():
    return (
        "🌍" + "═" * 38 + "🌍\n"
        "         📝 اختر اللغة / Choose Language\n"
        "🌍" + "═" * 38 + "🌍"
    )

def get_main_menu_design(lang: str):
    if lang == "ar":
        return (
            "🏠" + "═" * 38 + "🏠\n"
            "          📋 الأقسام الرئيسية\n"
            "🏠" + "═" * 38 + "🏠"
        )
    else:
        return (
            "🏠" + "═" * 38 + "🏠\n"
            "          📋 Main Sections\n"
            "🏠" + "═" * 38 + "🏠"
        )

def get_forex_design(lang: str):
    if lang == "ar":
        return (
            "💹" + "═" * 38 + "💹\n"
            "        📊 قسم تداول الفوركس\n"
            "💹" + "═" * 38 + "💹\n\n"
            "📈 استثمر بذكاء مع أحدث أدوات التداول\n"
            "📈 Invest smartly with latest trading tools"
        )
    else:
        return (
            "💹" + "═" * 38 + "💹\n"
            "        📊 Forex Trading Section\n"
            "💹" + "═" * 38 + "💹\n\n"
            "📈 Invest smartly with latest trading tools"
        )

def get_development_design(lang: str):
    if lang == "ar":
        return (
            "💻" + "═" * 38 + "💻\n"
            "        🛠️ قسم خدمات البرمجة\n"
            "💻" + "═" * 38 + "💻\n\n"
            "⚡ حلول برمجية مبتكرة لتداول أفضل\n"
            "⚡ Innovative programming solutions for better trading"
        )
    else:
        return (
            "💻" + "═" * 38 + "💻\n"
            "        🛠️ Programming Services\n"
            "💻" + "═" * 38 + "💻\n\n"
            "⚡ Innovative programming solutions for better trading"
        )

def get_agency_design(lang: str):
    if lang == "ar":
        return (
            "🤝" + "═" * 38 + "🤝\n"
            "        🌟 قسم طلب وكالة YesFX\n"
            "🤝" + "═" * 38 + "🤝\n\n"
            "💼 انضم إلى شبكة وكلائنا الناجحين\n"
            "💼 Join our successful partner network"
        )
    else:
        return (
            "🤝" + "═" * 38 + "🤝\n"
            "        🌟 YesFX Partnership\n"
            "🤝" + "═" * 38 + "🤝\n\n"
            "💼 Join our successful partner network"
        )

# ===============================
# 🟢 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = get_welcome_design()
    await update.message.reply_text(text, reply_markup=reply_markup)

# ===============================
# 🆕 2. عرض اختيار اللغة عند الرجوع
# ===============================
async def show_language_selection_via_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        keyboard = [
            [
                InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
                InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = get_language_design()
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
        text = get_main_menu_design(lang)
        back_button = ("🔙 الرجوع لاختيار اللغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        text = get_main_menu_design(lang)
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
                ("📰 الأخبار الاقتصادية", "forex_news"),
                ("📊 تحليل السوق", "forex_analysis")
            ]
            text = get_forex_design(lang)
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [
                ("📊 Copy Trading", "forex_copy"),
                ("💬 Signals Channel", "forex_signals"),
                ("📰 Economic News", "forex_news"),
                ("📊 Market Analysis", "forex_analysis")
            ]
            text = get_forex_design(lang)
            back_label = "🔙 Back to main menu"

    elif query.data == "dev_main":
        if lang == "ar":
            options = [
                ("📈 برمجة المؤشرات", "dev_indicators"),
                ("🤖 برمجة الاكسبيرتات", "dev_experts"),
                ("💬 برمجة بوتات التليجرام", "dev_bots"),
                ("🌐 برمجة مواقع الويب", "dev_web"),
                ("📱 برمجة تطبيقات الموبايل", "dev_mobile")
            ]
            text = get_development_design(lang)
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [
                ("📈 Indicators Development", "dev_indicators"),
                ("🤖 Expert Advisors", "dev_experts"),
                ("💬 Telegram Bots", "dev_bots"),
                ("🌐 Web Development", "dev_web"),
                ("📱 Mobile Apps", "dev_mobile")
            ]
            text = get_development_design(lang)
            back_label = "🔙 Back to main menu"

    elif query.data == "agency_main":
        if lang == "ar":
            options = [
                ("📄 طلب وكالة YesFX", "agency_request"),
                ("💰 نظام العمولات", "agency_commissions"),
                ("📊 إحصائيات الوكالة", "agency_stats"),
                ("🎓 تدريب الوكيل", "agency_training")
            ]
            text = get_agency_design(lang)
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            options = [
                ("📄 Request Partnership", "agency_request"),
                ("💰 Commission System", "agency_commissions"),
                ("📊 Agency Statistics", "agency_stats"),
                ("🎓 Agent Training", "agency_training")
            ]
            text = get_agency_design(lang)
            back_label = "🔙 Back to main menu"

    else:
        # خدمات فرعية placeholder
        service_name = query.data
        if lang == "ar":
            service_text = (
                "⭐" + "═" * 38 + "⭐\n"
                f"         🎯 {service_name.replace('_', ' ').title()}\n"
                "⭐" + "═" * 38 + "⭐\n\n"
                "📋 تم اختيار الخدمة بنجاح!\n"
                "⏳ سيتم إضافة التفاصيل قريبًا...\n\n"
                "🛠️ فريق الدعم الفني يعمل على\n"
                "   إعداد أفضل الحلول لك"
            )
        else:
            service_text = (
                "⭐" + "═" * 38 + "⭐\n"
                f"         🎯 {service_name.replace('_', ' ').title()}\n"
                "⭐" + "═" * 38 + "⭐\n\n"
                "📋 Service selected successfully!\n"
                "⏳ Details will be added soon...\n\n"
                "🛠️ Our technical team is working on\n"
                "   preparing the best solutions for you"
            )
        
        keyboard = [[InlineKeyboardButton(
            "🔙 " + ("الرجوع" if lang == "ar" else "Back"), 
            callback_data="back_main"
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=service_text, reply_markup=reply_markup)
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
    return {
        "status": "✅ Bot is running",
        "service": "IYesFX Telegram Bot",
        "version": "2.0.0"
    }

# ===============================
# 🟢 Webhook
# ===============================
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"status": "success", "message": "Update processed"}
    except Exception as e:
        logger.exception("❌ Webhook error")
        return {"status": "error", "error": str(e)}

# ===============================
# 🚀 Startup
# ===============================
@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Starting IYesFX Bot...")
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
