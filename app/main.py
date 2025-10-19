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
# 📏 دالة لتنسيق النص داخل صندوق ASCII مع محاذاة مثالية للوسط
# ===============================
def create_boxed_text(text: str, width: int = 40, icon: str = "") -> str:
    """إنشاء صندوق ASCII للنص مع محاذاة وسط دقيقة."""
    lines = text.split("\n")
    boxed_lines = []
    border = "═" * width
    boxed_lines.append(f"╔{border}╗")
    for line in lines:
        line_content = f"{icon} {line}" if icon else line
        visible_len = sum(2 if ord(c) > 127 else 1 for c in line_content)
        total_padding = width - visible_len
        left_padding = total_padding // 2
        right_padding = total_padding - left_padding
        padded_line = " " * left_padding + line_content + " " * right_padding
        boxed_lines.append(f"║{padded_line}║")
    boxed_lines.append(f"╚{border}╝")
    return "\n".join(boxed_lines)

# ===============================
# 🔹 دالة لتأثير ASCII متحرك على العنوان
# ===============================
async def animated_box(update: Update, title: str, icon: str = "💠", width: int = 40, reply_markup=None):
    """عرض صندوق مع تأثير ASCII متحرك على العنوان."""
    frames = [
        f"✨{icon}✨",
        f" {icon}✨ ",
        f"✨ {icon} ",
        f" {icon} ",
    ]
    for frame in frames * 2:  # تكرار الحركات
        text = create_boxed_text(title, width=width, icon=frame)
        if update.callback_query:
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text)
        await asyncio.sleep(0.3)  # سرعة الحركة

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
    await animated_box(update, "أهلا بك في بوت YesFX!", icon="🌟", reply_markup=reply_markup)
    await animated_box(update, "Welcome to YesFX Bot!", icon="👋", reply_markup=reply_markup)

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
        await animated_box(update, "مرحبًا مجددًا!", icon="🔁", reply_markup=reply_markup)
        await animated_box(update, "Welcome again!", icon="🔁", reply_markup=reply_markup)
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
        back_button = ("🔙 الرجوع للغة", "back_language")
        title = "الأقسام الرئيسية"
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        back_button = ("🔙 Back to language", "back_language")
        title = "Main Sections"

    keyboard = [[InlineKeyboardButton(name, callback_data=callback)] for name, callback in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await animated_box(update, title, icon="🏷️", reply_markup=reply_markup)

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

    if query.data == "back_language":
        await show_language_selection_via_query(update, context)
        return
    if query.data == "back_main":
        await show_main_sections(update, lang)
        return

    sections_data = {
        "forex_main": {
            "ar": ["📊 نسخ الصفقات", "💬 قناة التوصيات", "📰 الأخبار الاقتصادية"],
            "en": ["📊 Copy Trading", "💬 Signals Channel", "📰 Economic News"],
            "title_ar": "تداول الفوركس",
            "title_en": "Forex Trading"
        },
        "dev_main": {
            "ar": ["📈 برمجة المؤشرات", "🤖 برمجة الاكسبيرتات", "💬 بوتات التليجرام", "🌐 مواقع الويب"],
            "en": ["📈 Indicators", "🤖 Expert Advisors", "💬 Telegram Bots", "🌐 Web Development"],
            "title_ar": "خدمات البرمجة",
            "title_en": "Programming Services"
        },
        "agency_main": {
            "ar": ["📄 طلب وكالة YesFX"],
            "en": ["📄 Request YesFX Partnership"],
            "title_ar": "طلب وكالة",
            "title_en": "Partnership"
        }
    }

    if query.data in sections_data:
        data = sections_data[query.data]
        options = data[lang]
        title = data[f"title_{lang}"]
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await animated_box(update, title, icon="💠", reply_markup=reply_markup)
        return

    # Placeholder for sub-services
    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}")

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
