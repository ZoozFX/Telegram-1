import os
import logging
import unicodedata
from typing import List, Optional

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

Base.metadata.create_all(bind=engine)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# -------------------------------
# إعدادات قابلة للتعديل
# -------------------------------
SIDE_MARK = "◾"                          # الرمز الجانبي
HEADER_EMOJI = "🔰"                      # الإيموجي الموجود داخل العنوان
KEEP_EMOJI_IN_MEASUREMENT = False        # إذا False => الإيموجي لا يُحتسب عند حساب التوسيط (يبقى ظاهرًا)
UNDERLINE_MODE = "auto"                  # "auto" أو عدد صحيح لعرض ثابت
UNDERLINE_MIN = 20                       # الحد الأدنى لطول السطر في حالة "auto"
NBSP = "\u00A0"

# -------------------------------
# مساعدة: إزالة الإيموجي (لمجرد القياس إن لزم)
# -------------------------------
def remove_emoji(text: str) -> str:
    """
    حذف الأحرف التي على الأرجح إيموجي أو رموز واسعة من النص — لأغراض القياس فقط.
    هذه دالة تقريبية لكنها فعّالة للتعامل مع تأثير الإيموجي على توسيط النص.
    """
    out = []
    for ch in text:
        o = ord(ch)
        # نطاقات شائعة للإيموجي والرموز
        if (
            0x1F300 <= o <= 0x1F5FF or
            0x1F600 <= o <= 0x1F64F or
            0x1F680 <= o <= 0x1F6FF or
            0x1F900 <= o <= 0x1F9FF or
            0x2600 <= o <= 0x26FF or
            0x2700 <= o <= 0x27BF or
            0x1FA70 <= o <= 0x1FAFF or
            o == 0xFE0F
        ):
            continue
        out.append(ch)
    return "".join(out)

# -------------------------------
# مساعدة: قياس العرض المرئي للنص (تقريبي)
# يدعم الإيموجي، الحروف واسعة العرض، وcombining marks
# -------------------------------
def display_width(text: str) -> int:
    if not text:
        return 0
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        ea = unicodedata.east_asian_width(ch)
        if ea in ("F", "W"):
            width += 2
            continue
        o = ord(ch)
        if (
            0x1F300 <= o <= 0x1F5FF
            or 0x1F600 <= o <= 0x1F64F
            or 0x1F680 <= o <= 0x1F6FF
            or 0x1F900 <= o <= 0x1F9FF
            or 0x2600 <= o <= 0x26FF
            or 0x2700 <= o <= 0x27BF
            or o == 0xFE0F
        ):
            width += 2
            continue
        width += 1
    return width

def max_button_width(labels: List[str]) -> int:
    if not labels:
        return 0
    return max(display_width(lbl) for lbl in labels)

# -------------------------------
# بناء رأس HTML متمركز تقريبًا بالنسبة لعرض أزرار الكيبورد
# تقنيات:
# - نكوّن full_title المرئي (يشمل SIDE_MARK وHEADER_EMOJI)
# - لكن عند الحساب للتوسيط نستخدم نسخة خالية من الإيموجي إذا KEEP_EMOJI_IN_MEASUREMENT=False
# - نسمح بوضع UNDERLINE_MODE كرقم ثابت أو "auto"
# -------------------------------
def build_header_html(title: str, keyboard_labels: List[str], side_mark: str = SIDE_MARK,
                      header_emoji: str = HEADER_EMOJI,
                      keep_emoji_in_measurement: bool = KEEP_EMOJI_IN_MEASUREMENT,
                      underline_mode = UNDERLINE_MODE,
                      underline_min: int = UNDERLINE_MIN) -> str:
    """
    يعيد سلسلة HTML: عنوان (<b>...</b>) ثم سطر تحتي من ━
    يحاول توسيط العنوان بصريًا مقابل أعرض زر في لوحة الأزرار.
    """
    # العنوان الفعلي الظاهر
    full_title = f"{side_mark} {header_emoji} {title} {side_mark}"
    # نسخة للحساب (قد نزيل الإيموجي من القياس لتفادي الإزاحة)
    if keep_emoji_in_measurement:
        title_for_measure = full_title
    else:
        title_for_measure = remove_emoji(full_title)

    title_width = display_width(title_for_measure)
    target_width = max(10, max_button_width(keyboard_labels))

    # حسم طول underline
    if isinstance(underline_mode, int):
        underline_width = max(underline_mode, underline_min)
    else:
        # وضع "auto"
        underline_width = max(title_width, target_width, underline_min)

    # حساب حشوة NBSP لليسر عند الحاجة (نستخدم النسخة المحسوبة بدون إيموجي عادة)
    space_needed = max(0, underline_width - title_width)
    pad_left = space_needed // 2
    left_padding = NBSP * pad_left

    underline = "━" * underline_width

    header_html = f"{left_padding}<b>{full_title}</b>\n{underline}"
    return header_html

# ===============================
# 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    labels = ["🇪🇬 العربية", "🇺🇸 English"]

    # طلبك: العنوان يجب أن يكون "◾ 🔰 اللغة | Language ◾"
    header = build_header_html("اللغة | Language", labels)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    else:
        if update.message:
            await update.message.reply_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

# ===============================
# 2. عرض الأقسام الرئيسية بعد اختيار اللغة
# ===============================
async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
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
        labels = [name for name, _ in sections]
        header = build_header_html("الأقسام الرئيسية", labels)
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        labels = [name for name, _ in sections]
        header = build_header_html("Main Sections", labels)
        back_button = ("🔙 Back to language", "back_language")

    keyboard = []
    for name, callback in sections:
        keyboard.append([InlineKeyboardButton(name, callback_data=callback)])
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

# ===============================
# 3. اختيار اللغة
# ===============================
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, context, lang)

# ===============================
# 4. الأقسام الفرعية + الرجوع
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    # زر العودة للغة
    if query.data == "back_language":
        await start(update, context)
        return

    if query.data == "back_main":
        await show_main_sections(update, context, lang)
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
        labels = options + ([ "🔙 الرجوع للقائمة الرئيسية"] if lang == "ar" else ["🔙 Back to main menu"])
        box = build_header_html(title, labels)
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    try:
        await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}", parse_mode="HTML", disable_web_page_preview=True)
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
