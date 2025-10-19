import os
import logging
import unicodedata
from typing import List

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
SIDE_MARK = "◾"                  # الرمز الجانبي الذي يبقى
NBSP = "\u00A0"                  # مسافة غير قابلة للكسر لاستخدامها كـ padding
UNDERLINE_CHAR = "━"             # حرف السطر التحتي
UNDERLINE_MIN = 10               # الحد الأدنى لطول السطر (أيًا كان auto)
# -------------------------------
# دوال مساعدة لقياس "عرض" النص تقريبيًا (display width)
# تدعم: الحروف ذات العرض الواسع (East Asian), الإيموجي، وcombining marks
# هذا قياس تقريبي بالأعمدة المرئية ويستخدم للوساطة وإنتاج padding مناسب.
# -------------------------------
def display_width(text: str) -> int:
    """
    تقريب عرض النص بالـ 'عرض أعمدة' (columns).
    يعامل بعض الإيموجي والرموز كعرض 2، ويتجاهل combining marks.
    """
    if not text:
        return 0
    width = 0
    for ch in text:
        # تجاهل علامات التجميع عند حساب العرض (لا تضيف عرضًا مستقلاً)
        if unicodedata.combining(ch):
            continue
        ea = unicodedata.east_asian_width(ch)
        if ea in ("F", "W"):  # Fullwidth, Wide => عرض 2
            width += 2
            continue
        o = ord(ch)
        # نطاقات إيموجي ورموز شائعة — نعاملها كعرض 2
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
            width += 2
            continue
        # افتراضيًا عرض 1
        width += 1
    return width

def max_button_width(labels: List[str]) -> int:
    """أرجع أقصى عرض (تقريبي) بين تسميات الأزرار."""
    if not labels:
        return 0
    return max(display_width(lbl) for lbl in labels)

# -------------------------------
# دالة لإزالة الإيموجي من نص (لتظهر العناوين بدون إيموجي)
# هذه الدالة تقريبية لكنها تغطي نطاقات الإيموجي/الرموز الشائعة.
# -------------------------------
def remove_emoji(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
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
            # تجاهل الحرف (يختفي من العنوان)
            continue
        out.append(ch)
    return "".join(out)

# -------------------------------
# بناء هيدر HTML متمركز تقريبًا بدون إيموجي في العنوان
# يُرجع نص HTML (باستخدام <b> للعريض) يحتوي سطرًا علويًا واحدًا (العنوان) وسطر تحتي من ━
# التقنيات:
# - نزيل الإيموجي من العنوان المعروض
# - نحسب العرض المرئي للعنوان بعد إزالة الإيموجي
# - نأخذ بعين الاعتبار عرض أعرض زر في الـ keyboard لضبط طول السطر وتحويل padding
# - نستخدم NBSP لعمل حشوة يسارية للحسّ بالتوسيط
# -------------------------------
def build_centered_header(title: str, keyboard_labels: List[str]) -> str:
    """
    title: نص العنوان (قد يحتوي إيموجي — سيتم إزالته قبل العرض)
    keyboard_labels: تسميات الأزرار لقياس العرض المستهدف
    """
    # 1) قم بإزالة الإيموجي من العنوان (المطلوب: الإيموجي لا يظهر في العناوين)
    title_no_emoji = remove_emoji(title).strip()

    # 2) كون النص النهائي الظاهر داخل الـ <b>
    visible_title = f"{SIDE_MARK} {title_no_emoji} {SIDE_MARK}"

    # 3) قياس العرض المرئي للعنوان الظاهر (بعد إزالة الإيموجي)
    title_width = display_width(visible_title)

    # 4) هدف التوسيط: انتقل إلى أقصى عرض بين العنوان وأوسع زر
    target_width = max(UNDERLINE_MIN, max_button_width(keyboard_labels), title_width)

    # 5) طول السطر التحتي ديناميكي: نستخدم target_width
    underline_width = target_width

    # 6) حساب padding يساري (NBSP) لإعطاء إحساس بالتوسيط: (underline - title_width) // 2
    left_pad_cols = max(0, (underline_width - title_width) // 2)
    left_padding = NBSP * left_pad_cols

    # 7) بناء السلسلة النهائية (HTML bold + underline)
    underline = UNDERLINE_CHAR * underline_width
    header_html = f"{left_padding}<b>{visible_title}</b>\n{underline}"

    return header_html

# ===============================
# Handlers: start, show_main_sections, set_language, menu_handler
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # مطلوب أن يظهر: "◾ اللغة | Language ◾" (بدون إيموجي)
    title = "اللغة | Language"
    labels = ["🇪🇬 العربية", "🇺🇸 English"]
    header = build_centered_header(title, labels)

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

async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    if lang == "ar":
        sections = [
            ("📊 نسخ الصفقات", "forex_main"),
            ("💬 قناة التوصيات", "signals_channel"),
            ("📰 الأخبار الاقتصادية", "economic_news"),
        ]
        labels = [name for name, _ in sections]
        header = build_centered_header("الأقسام الرئيسية", labels)
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("📊 Copy Trading", "forex_main"),
            ("💬 Signals Channel", "signals_channel"),
            ("📰 Economic News", "economic_news"),
        ]
        labels = [name for name, _ in sections]
        header = build_centered_header("Main Sections", labels)
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

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, context, lang)

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
        # نحتفظ بالإيموجي في الأزرار ولكن نحذفها من العنوان داخل build_centered_header
        labels = options + (["🔙 الرجوع للقائمة الرئيسية"] if lang == "ar" else ["🔙 Back to main menu"])
        header = build_centered_header(title, labels)
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
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
