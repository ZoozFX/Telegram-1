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
# إعدادات قابلة للتعديل - محسنة
# -------------------------------
HEADER_EMOJI = "🔰"                      # الإيموجي الموجود داخل العنوان
HEADER_STYLE = "modern"                  # "modern" أو "classic" أو "minimal"
HEADER_DECORATION = "✦"                  # رمز الزخرفة
HEADER_LINE_CHAR = "─"                   # رمز الخط

# -------------------------------
# مساعدة: قياس العرض المرئي للنص (محسّن)
# -------------------------------
def display_width(text: str) -> int:
    """
    قياس العرض المرئي للنص مع دعم أفضل للغة العربية والإيموجي
    """
    if not text:
        return 0
    
    width = 0
    for char in text:
        # تجاهل أحرف التحكم والتجميع
        if unicodedata.category(char) in ('Mn', 'Me', 'Cf', 'Cc'):
            continue
            
        # تحديد عرض الحرف
        east_asian_width = unicodedata.east_asian_width(char)
        
        if east_asian_width in ('F', 'W'):
            width += 2
        else:
            # معظم الحروف العربية والعادية تأخذ عرض 1
            width += 1
            
    return width

def max_button_width(labels: List[str]) -> int:
    if not labels:
        return 0
    return max(display_width(str(lbl)) for lbl in labels)

# -------------------------------
# بناء رأس HTML محسن ومهني
# -------------------------------
def build_header_html(title: str, keyboard_labels: List[str], 
                      header_emoji: str = HEADER_EMOJI,
                      style: str = HEADER_STYLE,
                      decoration: str = HEADER_DECORATION,
                      line_char: str = HEADER_LINE_CHAR) -> str:
    """
    يعيد سلسلة HTML بعنوان محسن ومهني بأنماط مختلفة.
    """
    # العنوان الفعلي الظاهر
    full_title = f"{header_emoji} {title}" if header_emoji else title
    
    # حساب عرض العنوان
    title_width = display_width(full_title)
    
    # حساب عرض الأزرار مع هامش إضافي
    button_width = max_button_width(keyboard_labels) if keyboard_labels else 0
    target_width = max(title_width + 4, button_width + 4, 20)  # حد أدنى 20
    
    if style == "modern":
        # النمط الحديث مع إطار كامل - محسّن للتوسيط
        # حساب المسافات المطلوبة للتوسيط
        total_padding = max(0, target_width - title_width - 2)  # -2 للزوايا
        left_padding = total_padding // 2
        right_padding = total_padding - left_padding
        
        top_line = f"┌{line_char * (target_width - 2)}┐"
        title_line = f"│{' ' * left_padding}{full_title}{' ' * right_padding}│"
        bottom_line = f"└{line_char * (target_width - 2)}┘"
        
        header_html = f"<b>{top_line}\n{title_line}\n{bottom_line}</b>"
    
    elif style == "minimal":
        # النمط البسيط والأنيق
        total_padding = max(0, target_width - title_width - 4)  # -4 للزخارف
        left_padding = total_padding // 2
        right_padding = total_padding - left_padding
        
        header_html = f"<b>{decoration * 2}{' ' * left_padding}{full_title}{' ' * right_padding}{decoration * 2}</b>"
    
    else:  # classic
        # النمط الكلاسيكي المحسن
        line_length = max(title_width + 8, target_width)
        total_padding = max(0, line_length - title_width - 6)  # -6 للزخارف والمسافات
        left_padding = total_padding // 2
        right_padding = total_padding - left_padding
        
        top_line = f"{decoration * 3}{' ' * left_padding}{full_title}{' ' * right_padding}{decoration * 3}"
        bottom_line = f"{line_char * line_length}"
        
        header_html = f"<b>{top_line}</b>\n{bottom_line}"

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
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"
        labels = options + [back_label]
        
        header = build_header_html(title, labels)

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    # معالجة الأزرار الفرعية
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
