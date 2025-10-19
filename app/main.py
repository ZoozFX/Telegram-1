import os
import logging
import unicodedata
from typing import Optional

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
# إعدادات واجهة / صندوق العرض المتكيّف
# -------------------------------
BOX_MIN_WIDTH = 10
BOX_MAX_WIDTH = 45
BOX_PADDING = 2  # مسافات داخلية افتراضية

# -------------------------------
# عرض عرض الحرف (display width) الذكي
# -------------------------------
def display_width(text: str) -> int:
    """
    تقريب عرض النص على الشاشة (عدد أعمدة العرض) مع دعم للإيموجي،
    الحروف واسعة العرض (East Asian), والـ combining marks.
    لا يعتمد بالكامل على len() لأن بعض الرموز تأخذ عمودين وما يليها لا يزيد العرض.
    """
    if not text:
        return 0

    width = 0
    for ch in text:
        # تجاهل combining marks (لا تضيف عرضًا مستقلًا)
        if unicodedata.combining(ch):
            continue

        # بعض حروف east asian تعتبر واسعة (width 2)
        ea = unicodedata.east_asian_width(ch)
        if ea in ("F", "W"):
            width += 2
            continue

        o = ord(ch)
        # نطاقات إيموجي شائعة نجعلها عرضاً 2 عمودًا
        if (
            0x1F300 <= o <= 0x1F5FF  # symbols & pictographs
            or 0x1F600 <= o <= 0x1F64F  # emoticons
            or 0x1F680 <= o <= 0x1F6FF  # transport & map
            or 0x1F900 <= o <= 0x1F9FF  # supplemental symbols & pictographs
            or 0x2600 <= o <= 0x26FF    # misc symbols
            or 0x2700 <= o <= 0x27BF    # dingbats
            or 0xFE0F == o              # variation selector
        ):
            width += 2
            continue

        # الحروف الافتراضية نعتبرها بعرض 1
        width += 1

    return width

def slice_by_display_width(text: str, max_width: int) -> str:
    """
    يقطع النص بحيث يكون عرضه <= max_width (بالأعمدة المرئية).
    يحترم الـ combining marks — لا يكسرها.
    """
    if display_width(text) <= max_width:
        return text

    result_chars = []
    acc = 0
    for ch in text:
        ch_width = 0
        if unicodedata.combining(ch):
            # إضافة combining marking إلى النتيجة دون تغيير العرض
            result_chars.append(ch)
            continue
        ea = unicodedata.east_asian_width(ch)
        if ea in ("F", "W"):
            ch_width = 2
        else:
            o = ord(ch)
            if (
                0x1F300 <= o <= 0x1F5FF
                or 0x1F600 <= o <= 0x1F64F
                or 0x1F680 <= o <= 0x1F6FF
                or 0x1F900 <= o <= 0x1F9FF
                or 0x2600 <= o <= 0x26FF
                or 0x2700 <= o <= 0x27BF
                or 0xFE0F == o
            ):
                ch_width = 2
            else:
                ch_width = 1

        if acc + ch_width > max_width:
            break
        result_chars.append(ch)
        acc += ch_width

    return "".join(result_chars)

def build_dynamic_box(text: str, min_width: int = BOX_MIN_WIDTH, max_width: int = BOX_MAX_WIDTH, padding: int = BOX_PADDING) -> str:
    """
    يبني صندوقًا يتكيف عرضيًا مع النص ويقوم بتوسيطه دائمًا بغض النظر عن اللغة أو الإيموجي.
    - القص عند الحاجة مع "..."
    - إعادة النص مع حواف مرئية باستخدام حروف Unicode
    """
    line = text.strip()

    # طول المحتوى الحقيقي بالمقياس العرضي
    content_len = display_width(line)
    required_width = content_len + (padding * 2)

    # ضبط العرض ضمن الحدود (عرض الصندوق بالـ columns)
    width = max(min_width, min(required_width, max_width))

    # المساحة الداخلية المتاحة للنص
    inner_space = width - (padding * 2)

    if content_len > inner_space:
        # نضيف "..." لكن يجب أن نحترم عرض النقاط (3 أعمدة)
        ellipsis = "..."
        ellipsis_width = display_width(ellipsis)
        visible_width = max(0, inner_space - ellipsis_width)
        visible_text = slice_by_display_width(line, visible_width)
        line = visible_text + ellipsis
        content_len = display_width(line)

    # الآن نحسب الوسائط بناءً على العرض المرئي
    total_padding_space = width - content_len
    # نريد توزيع padding على اليسار واليمين بحيث يكون التوسيط بصريًا
    pad_left = total_padding_space // 2
    pad_right = total_padding_space - pad_left

    # نجعل المسافات الداخلية padding ثابتة (padding من الإعداد) ثم نضيف pad_left/pad_right
    # لكن pad_left/pad_right هنا هي أعمدة عرضية — سنبنيها بمسافات عادية لأن المسافة بعرض 1
    left_spaces = " " * pad_left
    right_spaces = " " * pad_right

    border = "═" * width
    top = f"╔{border}╗"
    middle = f"{left_spaces}{line}{right_spaces}"
    bottom = f"╚{border}╝"

    return f"{top}\n{middle}\n{bottom}"

# ===============================
# 1. /start → واجهة اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start يدعم كلتا الحالتين:
    - رسالة نصية (update.message)
    - نداء عبر callback (update.callback_query) — لذلك زر "الرجوع للغة" يعمل.
    """
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # أضفت ايموجي في العناوين كما طلبت
    ar_box = build_dynamic_box("🔰 الأقسام الرئيسية")
    en_box = build_dynamic_box("🔰 Main Sections")

    msg = f"{ar_box}\n\n{en_box}"

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
        except Exception:
            # إذا لم نتمكن من التعديل نرسل رسالة جديدة للحماية
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=reply_markup, disable_web_page_preview=True)
    else:
        if update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)

# ===============================
# 2. عرض الأقسام الرئيسية بعد اختيار اللغة
# ===============================
async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    """
    يعرض الأقسام الرئيسية بعد اختيار اللغة. يتلقى update, context, lang
    """
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
        box = build_dynamic_box("🔰 الأقسام الرئيسية")
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        box = build_dynamic_box("🔰 Main Sections")
        back_button = ("🔙 Back to language", "back_language")

    keyboard = []
    for name, callback in sections:
        keyboard.append([InlineKeyboardButton(name, callback_data=callback)])
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(box, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, disable_web_page_preview=True)

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
        # نضع ايموجي في عنوان الصفحة الفرعية أيضًا
        box = build_dynamic_box(f"🔰 {title}")
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(box, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, disable_web_page_preview=True)
        return

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
