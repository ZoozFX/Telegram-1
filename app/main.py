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
from telegram.constants import ParseMode
from app.db import Base, engine

# -------------------------------
# إعداد السجلات
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# -------------------------------
# إعدادات واجهة / صندوق العرض
# -------------------------------
BOX_WIDTH = 45  # توحيد العرض ليكون الشكل متناسقًا في جميع الشاشات

def build_centered_box(text: str, width: int = BOX_WIDTH) -> str:
    """
    ينشئ صندوقًا بسيطًا بعرض ثابت ومحاذاة في المنتصف بدون استخدام <pre>
    يحافظ على حواف ثابتة ويقتصر النص الطويل بنقوص (...)
    """
    line = text.strip()
    if len(line) > width:
        line = line[: width - 3] + "..."

    border = "═" * width
    top = f"╔{border}╗"
    bottom = f"╚{border}╝"

    pad_left = (width - len(line)) // 1
    pad_right = width - len(line) - pad_left
    middle = f"{' ' * pad_left}{line}{' ' * pad_right}"

    return f"{top}\n{middle}\n{bottom}"

# ===============================
# 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يدعم هذا الستاارت كلتا الحالتين:
    - أمر /start (يحتوي update.message)
    - استدعاء عبر callback (يحتوي update.callback_query)
    لذلك يمكن استخدامه كزر "الرجوع للغة" أيضاً.
    """
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    ar_box = build_centered_box("الأقسام الرئيسية")
    en_box = build_centered_box("Main Sections")

    msg = f"{ar_box}\n\n{en_box}"

    # إذا الرسالة أتت من callback_query نستخدم edit_message_text (الرجوع يعمل الآن)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        # نتحقق إن الرسالة موجودة ثم نعدّلها
        try:
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
        except Exception:
            # لو لم نتمكن من التعديل (مثلاً كانت الرسالة قد حذفت)، نرسل رسالة جديدة
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=reply_markup, disable_web_page_preview=True)
    else:
        # حالة /start عبر أمر النصي
        if update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)

# ===============================
# 2. عرض الأقسام الرئيسية بعد اختيار اللغة
# ===============================
async def show_main_sections(update: Update, lang: str):
    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        box = build_centered_box("الأقسام الرئيسية")
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        box = build_centered_box("Main Sections")
        back_button = ("🔙 Back to language", "back_language")

    # نبني لوحة أزرار مرتبة: كل خيار في صف مستقل (قابلة لتعديل لتصبح صفين إن رغبت)
    keyboard = []
    for name, callback in sections:
        keyboard.append([InlineKeyboardButton(name, callback_data=callback)])
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # نحرص على استخدام edit_message_text لإبقاء الرسالة واحدة وتتغير محتوياتها
    try:
        await query.edit_message_text(box, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
    except Exception:
        # في حال فشل التعديل نرسل رسالة جديدة
        await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, disable_web_page_preview=True)

# ===============================
# 3. اختيار اللغة
# ===============================
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, lang)

# ===============================
# 4. الأقسام الفرعية + الرجوع
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    # زر العودة للغة الآن يعمل لأن دالة start تدعم callback
    if query.data == "back_language":
        await start(update, context)
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

    # لو تم اختيار أحد الأقسام الرئيسية
    if query.data in sections_data:
        data = sections_data[query.data]
        options = data[lang]
        title = data[f"title_{lang}"]
        box = build_centered_box(title)
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(box, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, disable_web_page_preview=True)
        return

    # حالة اختيار خدمة فرعية (أو أي callback غير متوقع)
    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    try:
        await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}", parse_mode=None, disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"🔹 {placeholder}: {query.data}\n\n{details}", disable_web_page_preview=True)

# ===============================
# Handlers
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))

# ===============================
# Webhook setup
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

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

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await application.shutdown()
