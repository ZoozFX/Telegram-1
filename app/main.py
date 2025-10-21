import os
import re
import logging
import unicodedata
from typing import List
import math
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from app.db import Base, engine
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import sessionmaker
from fastapi.responses import JSONResponse
# -------------------------------
# إعداد السجلات
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

# -------------------------------
# نموذج جديد لحفظ بيانات المستخدمين
# -------------------------------
SessionLocal = sessionmaker(bind=engine)

class Subscriber(Base):
    __tablename__ = "subscribers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False)
    phone = Column(String(50), nullable=False)
    telegram_username = Column(String(200), nullable=True)
    telegram_id = Column(Integer, nullable=True)
    lang = Column(String(8), default="ar")


# إذا لم يكن الجدول موجودًا يتم إنشاؤه
Base.metadata.create_all(bind=engine)

# -------------------------------
# ثوابت التسجيل (Conversation states)
# -------------------------------
NAME, EMAIL, PHONE = range(3)

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
SIDE_MARK = "◾"
HEADER_EMOJI = "✨"          # الإيموجي الافتراضي للغة العربية (قابلة للتعديل)
UNDERLINE_MODE = 30          # 👈 الطول الافتراضي للخط
UNDERLINE_MIN = 17           # 👈 الحد الأدنى للطول أيضًا 5
NBSP = "\u00A0"
DEFAULT_HEADER_WIDTH = 17



# -------------------------------
# رابط قاعدة البيانات
# -------------------------------
@app.get("/subscribers")
def get_subscribers():
    try:
        db = SessionLocal()
        subs = db.query(Subscriber).all()
        db.close()
        # نحول النتيجة إلى JSON قابل للإرسال
        return JSONResponse(content=[
            {
                "id": s.id,
                "name": s.name,
                "email": s.email,
                "phone": s.phone,
                "lang": s.lang,
                "telegram_id": s.telegram_id,
                "telegram_username": s.telegram_username
            }
            for s in subs
        ])
    except Exception as e:
        logger.exception("Failed to fetch subscribers")
        return JSONResponse(content={"error": str(e)}, status_code=500)
# -------------------------------
# مساعدة: إزالة الإيموجي لأغراض القياس
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
            continue
        out.append(ch)
    return "".join(out)

# -------------------------------
# قياس العرض المرئي التقريبي للنص
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
    return max((display_width(lbl) for lbl in labels), default=0)

# -------------------------------
# ✅ النسخة المحسّنة من build_header_html
# -------------------------------
def build_header_html(
    title: str,
    keyboard_labels: List[str],
    side_mark: str = "◾",
    header_emoji: str = "💥💥",
    underline_mode: int | str = 25,
    underline_min: int = 25,
    arabic_rtl_bias: float | None = None,
    width_padding: int = 1,
    align: str = "center",
    manual_shift: int = 0,
    underline_char: str = "━",
    underline_enabled: bool = True,
    underline_length: int = 25,
    extra_lines: int = 0,           # 👈 عدد الأسطر الفارغة أو المخفية أسفل الخط
    invisible_space: bool = False,  # 👈 إذا True نستخدم NBSP بدل فراغ عادي
    arabic_indent: int = 0,         # 👈 عدد الفراغات قبل النص العربي
    english_indent: int = 0         # 👈 عدد الفراغات قبل النص الإنجليزي
) -> str:
    """
    نسخة محسّنة:
    - تضيف خطًا سفليًا ثابت الطول وموسّطًا.
    - تعالج مشكلة محاذاة النصوص العربية (RTL misalignment).
    - تضيف أسطر فارغة أو مخفية أسفل الخط للتحكم في التباعد.
    - تضيف إمكانية تحديد عدد المسافات قبل النص العربي والإنجليزي (بما في ذلك قبل الإيموجي).
    """

    NBSP = "\u00A0"
    RLM = "\u200F"   # Right-to-Left Mark
    LRM = "\u200E"   # Left-to-Right Mark
    RLE = "\u202B"   # Right-to-Left Embedding
    PDF = "\u202C"   # Pop Directional Formatting

    import re
    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    # 👇 هنا نضيف المسافات (قبل الإيموجي)
    if is_arabic:
        indent_spaces = NBSP * arabic_indent
        full_title = f"{indent_spaces}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        indent_spaces = NBSP * english_indent
        full_title = f"{indent_spaces}{LRM}{header_emoji} {title} {header_emoji}{LRM}"

    # حساب العرض
    title_width = display_width(remove_emoji(full_title))
    target_width = max(max_button_width(keyboard_labels), underline_min)
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left

    # محاذاة
    if align.lower() == "left":
        pad_left = 0
        pad_right = max(0, target_width - title_width)
    elif align.lower() == "right":
        pad_right = 0
        pad_left = max(0, target_width - title_width)

    # إزاحة يدوية
    if manual_shift != 0:
        pad_left = max(0, pad_left + manual_shift)
        pad_right = max(0, pad_right - manual_shift) if manual_shift > 0 else max(0, pad_right + abs(manual_shift))

    # سطر العنوان
    centered_line = f"{NBSP * pad_left}<b>{full_title}</b>{NBSP * pad_right}"

    # الخط السفلي
    underline_line = ""
    if underline_enabled:
        line = underline_char * underline_length
        diff = max(0, target_width - underline_length)
        pad_left_line = diff // 2
        pad_right_line = diff - pad_left_line
        underline_line = f"\n{NBSP * pad_left_line}{line}{NBSP * pad_right_line}"

    # الأسطر الإضافية تحت الخط
    extra_section = ""
    if extra_lines > 0:
        spacer = NBSP if invisible_space else ""
        extra_section = ("\n" + spacer) * extra_lines

    return centered_line + underline_line + extra_section

# ===============================
# 1. /start → اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en"),
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    labels = ["🇺🇸 English", "🇪🇬 العربية"]

    # ميزة التمييز: استخدم إيموجي مختلف لكل لغة لتمييز العناوين بصريًا
    header = build_header_html("Language | اللغة", labels, header_emoji=HEADER_EMOJI)

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
# 2. الأقسام الرئيسية
# ===============================
async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    # استخدم إيموجي مختلف لتمييز اللغة بصريًا
    header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"

    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        labels = [name for name, _ in sections]
        header = build_header_html("الأقسام الرئيسية", labels, header_emoji=header_emoji_for_lang)
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        labels = [name for name, _ in sections]
        header = build_header_html("Main Sections", labels, header_emoji=header_emoji_for_lang)
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=cb)] for name, cb in sections]
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

# -------------------------------
# حفظ المشترك في قاعدة البيانات
# -------------------------------
def save_subscriber(name: str, email: str, phone: str, lang: str = "ar", telegram_id: int = None, telegram_username: str = None) -> None:
    try:
        db = SessionLocal()
        sub = Subscriber(
            name=name,
            email=email,
            phone=phone,
            lang=lang,
            telegram_id=telegram_id,
            telegram_username=telegram_username
        )
        db.add(sub)
        db.commit()
        db.close()
    except Exception as e:
        logger.exception("Failed to save subscriber: %s", e)


# -------------------------------
# التحقق من صحة الإيميل والهاتف
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# ===============================
# 4. الأقسام الفرعية + البدء في التسجيل عند اختيار نسخ الصفقات
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    if query.data == "back_language":
        await start(update, context)
        return
    if query.data == "back_main":
        await show_main_sections(update, context, lang)
        return

    # إذا نقر المستخدم على زر "نسخ الصفقات" (بالعربية أو الإنجليزية) نبدأ عملية التسجيل
    if query.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
        # نحفظ حالة التسجيل
        context.user_data["registration"] = {"lang": lang}
        context.user_data["reg_state"] = "awaiting_name"

        if lang == "ar":
            text = "فضلاً أدخل اسمك الكامل:" 
            back_label = "🔙 إلغاء"
        else:
            text = "Please enter your full name:"
            back_label = "🔙 Cancel"

        keyboard = [[InlineKeyboardButton(back_label, callback_data="cancel_reg")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup)
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

        # إضافة زر الرجوع ضمن الملصقات لتأثير العرض/قياس العرض
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"
        labels = options + [back_label]

        # تخصيص إيموجي العنوان بحسب اللغة
        header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"

        box = build_header_html(title, labels, header_emoji=header_emoji_for_lang)
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
# 5. معالجة الرسائل أثناء التسجيل (اسم - ايميل - هاتف)
# ===============================
async def registration_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    reg = context.user_data.get("registration")
    if not reg:
        return  # ليست حالة تسجيل

    state = context.user_data.get("reg_state")
    text = msg.text.strip()

    # إلغاء بسيط: المستخدم أرسل كلمة إلغاء
    if text.lower() in ("cancel", "إلغاء", "الغاء"):
        context.user_data.pop("registration", None)
        context.user_data.pop("reg_state", None)
        lang = context.user_data.get("lang", "ar")
        await msg.reply_text("تم إلغاء التسجيل." if lang == "ar" else "Registration cancelled.")
        await show_main_sections(update, context, lang)
        return

    if state == "awaiting_name":
        context.user_data["registration"]["name"] = text
        context.user_data["reg_state"] = "awaiting_email"
        prompt = "الآن أدخل بريدك الإلكتروني:" if reg.get("lang") == "ar" else "Now enter your email:"
        await msg.reply_text(prompt)
        return

    if state == "awaiting_email":
        if not EMAIL_RE.match(text):
            await msg.reply_text("بريد إلكتروني غير صالح. حاول مرة أخرى:" if reg.get("lang") == "ar" else "Invalid email. Try again:")
            return
        context.user_data["registration"]["email"] = text
        context.user_data["reg_state"] = "awaiting_phone"
        prompt = "أخيرًا: أدخل رقم الهاتف (مع رمز الدولة):" if reg.get("lang") == "ar" else "Finally: enter your phone number (with country code):"
        await msg.reply_text(prompt)
        return

    if state == "awaiting_phone":
        if not PHONE_RE.match(text):
            await msg.reply_text("رقم هاتف غير صالح. حاول مرة أخرى:" if reg.get("lang") == "ar" else "Invalid phone number. Try again:")
            return
        context.user_data["registration"]["phone"] = text

        # حفظ في قاعدة البيانات
        try:
            user = update.message.from_user
            save_subscriber(
                name=context.user_data["registration"]["name"],
                email=context.user_data["registration"]["email"],
                phone=context.user_data["registration"]["phone"],
                telegram_id=user.id,
                telegram_username=user.username
                lang=reg.get("lang", "ar")
            )
        except Exception:
            logger.exception("Error saving subscriber")

        lang = reg.get("lang", "ar")
        if lang == "ar":
            await msg.reply_text("✅ تم التسجيل بنجاح! شكرًا لك. سنتواصل معك عبر البريد أو الهاتف.")
        else:
            await msg.reply_text("✅ Registration successful! Thank you. We will contact you via email or phone.")

        # نظف حالة التسجيل وارجع للقائمة الرئيسية
        context.user_data.pop("registration", None)
        context.user_data.pop("reg_state", None)
        await show_main_sections(update, context, lang)
        return

# -------------------------------
# معالجة إلغاء التسجيل بالزر
# -------------------------------
async def cancel_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("registration", None)
    context.user_data.pop("reg_state", None)
    lang = context.user_data.get("lang", "ar")
    if lang == "ar":
        await query.edit_message_text("تم إلغاء التسجيل.")
    else:
        await query.edit_message_text("Registration cancelled.")
    await show_main_sections(update, context, lang)

# ===============================
# Handlers
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))
application.add_handler(CallbackQueryHandler(cancel_registration_callback, pattern="^cancel_reg$"))

# استقبال رسائل المستخدم خلال عملية التسجيل
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration_message_handler))

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
