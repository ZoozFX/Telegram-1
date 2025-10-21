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

# -------------------------------
# قاعدة البيانات - نموذج المشتركين
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
HEADER_EMOJI = "✨"
UNDERLINE_MODE = 30
UNDERLINE_MIN = 17
NBSP = "\u00A0"
DEFAULT_HEADER_WIDTH = 17

# -------------------------------
# Utilities: إزالة الإيموجي وقياس العرض
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
# build_header_html (محسّن)
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
    extra_lines: int = 0,
    invisible_space: bool = False,
    arabic_indent: int = 0,
    english_indent: int = 0
) -> str:
    NBSP = "\u00A0"
    RLM = "\u200F"
    LRM = "\u200E"
    RLE = "\u202B"
    PDF = "\u202C"

    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    if is_arabic:
        indent_spaces = NBSP * arabic_indent
        full_title = f"{indent_spaces}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        indent_spaces = NBSP * english_indent
        full_title = f"{indent_spaces}{LRM}{header_emoji} {title} {header_emoji}{LRM}"

    title_width = display_width(remove_emoji(full_title))
    target_width = max(max_button_width(keyboard_labels), underline_min)
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left

    if align.lower() == "left":
        pad_left = 0
        pad_right = max(0, target_width - title_width)
    elif align.lower() == "right":
        pad_right = 0
        pad_left = max(0, target_width - title_width)

    if manual_shift != 0:
        pad_left = max(0, pad_left + manual_shift)
        pad_right = max(0, pad_right - manual_shift) if manual_shift > 0 else max(0, pad_right + abs(manual_shift))

    centered_line = f"{NBSP * pad_left}<b>{full_title}</b>{NBSP * pad_right}"

    underline_line = ""
    if underline_enabled:
        line = underline_char * underline_length
        diff = max(0, target_width - underline_length)
        pad_left_line = diff // 2
        pad_right_line = diff - pad_left_line
        underline_line = f"\n{NBSP * pad_left_line}{line}{NBSP * pad_right_line}"

    extra_section = ""
    if extra_lines > 0:
        spacer = NBSP if invisible_space else ""
        extra_section = ("\n" + spacer) * extra_lines

    return centered_line + underline_line + extra_section

# -------------------------------
# REST endpoint to list subscribers
# -------------------------------
@app.get("/subscribers")
def get_subscribers():
    try:
        db = SessionLocal()
        subs = db.query(Subscriber).all()
        db.close()
        return JSONResponse(content=[
            {
                "id": s.id,
                "name": s.name,
                "email": s.email,
                "phone": s.phone,
                "telegram_username": s.telegram_username,
                "telegram_id": s.telegram_id,
                "lang": s.lang
            }
            for s in subs
        ])
    except Exception as e:
        logger.exception("Failed to fetch subscribers")
        return JSONResponse(content={"error": str(e)}, status_code=500)

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
            telegram_username=telegram_username,
            telegram_id=telegram_id,
            lang=lang
        )
        db.add(sub)
        db.commit()
        db.close()
    except Exception as e:
        logger.exception("Failed to save subscriber: %s", e)

# -------------------------------
# Regex للتحقق من الايميل والهاتف
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# ===============================
# واجهة التسجيل التفاعلي (نموذج داخل رسالة واحدة)
# ===============================
async def show_registration_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعرض أو يحدث رسالة النموذج التفاعلي. 
    إذا كانت هناك رسالة سابقة للنموذج فسيتم تعديلها (edit) وإلا سيتم إرسال رسالة جديدة وتخزين message_id في user_data['form_message_id'].
    """
    query = getattr(update, "callback_query", None)
    lang = context.user_data.get("lang", "ar")
    reg = context.user_data.get("registration", {})

    name = reg.get("name", "❌ لم يتم الإدخال" if lang == "ar" else "❌ Not entered")
    email = reg.get("email", "❌ لم يتم الإدخال" if lang == "ar" else "❌ Not entered")
    phone = reg.get("phone", "❌ لم يتم الإدخال" if lang == "ar" else "❌ Not entered")

    if lang == "ar":
        title = "🧾 من فضلك أكمل بياناتك"
        back_label = "🔙 رجوع"
        save_label = "✅ حفظ البيانات"
    else:
        title = "🧾 Please complete your data"
        back_label = "🔙 Back"
        save_label = "✅ Save Data"

    labels = ["👤 الاسم", "📧 البريد الإلكتروني", "📞 الهاتف", back_label, save_label]
    header = build_header_html(
        title,
        labels,
        header_emoji="✨" if lang != "ar" else HEADER_EMOJI,
        underline_enabled=True,
        underline_length=25,
        underline_min=20,
        underline_char="━",
        arabic_indent=1 if lang == "ar" else 0,
    )

    text = (
        f"{header}\n\n"
        f"👤 <b>{'الاسم' if lang == 'ar' else 'Name'}:</b> {name}\n"
        f"📧 <b>{'البريد الإلكتروني' if lang == 'ar' else 'Email'}:</b> {email}\n"
        f"📞 <b>{'رقم الهاتف' if lang == 'ar' else 'Phone'}:</b> {phone}"
    )

    keyboard = [
        [
            InlineKeyboardButton("👤", callback_data="edit_name"),
            InlineKeyboardButton("📧", callback_data="edit_email"),
            InlineKeyboardButton("📞", callback_data="edit_phone"),
        ],
        [InlineKeyboardButton(save_label, callback_data="save_registration")],
        [InlineKeyboardButton(back_label, callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # إذا جاءت النداء من callback_query نعدل الرسالة الموجودة (أكثر "نظافة")
    if query:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            # حفظ رسالة النموذج حتى نتمكن من تعديلها لاحقًا
            context.user_data["form_message_id"] = query.message.message_id
            context.user_data["form_chat_id"] = query.message.chat_id
        except Exception:
            # إرسال رسالة جديدة كحالة طوارئ
            sent = await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            context.user_data["form_message_id"] = sent.message_id
            context.user_data["form_chat_id"] = sent.chat_id
    else:
        # النداء جاي من رسالة (بعد أن أدخل المستخدم قيمة) — نحاول تعديل رسالة النموذج السابقة
        chat_id = context.user_data.get("form_chat_id")
        message_id = context.user_data.get("form_message_id")
        try:
            if chat_id and message_id:
                await context.bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            else:
                sent = await context.bot.send_message(chat_id=update.message.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                context.user_data["form_message_id"] = sent.message_id
                context.user_data["form_chat_id"] = sent.chat_id
        except Exception:
            # إرسال رسالة جديدة كحل احتياطي
            sent = await context.bot.send_message(chat_id=update.message.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            context.user_data["form_message_id"] = sent.message_id
            context.user_data["form_chat_id"] = sent.chat_id

# ===============================
# معالجة أزرار نموذج التسجيل
# ===============================
async def registration_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    # تعديل حقل محدد
    if query.data.startswith("edit_"):
        field = query.data.split("_", 1)[1]  # name, email, phone
        context.user_data["editing_field"] = field

        prompts = {
            "ar": {
                "name": "✏️ فضلاً أدخل اسمك الكامل:",
                "email": "📧 فضلاً أدخل بريدك الإلكتروني:",
                "phone": "📞 فضلاً أدخل رقم هاتفك (مع رمز الدولة):",
            },
            "en": {
                "name": "✏️ Please enter your full name:",
                "email": "📧 Please enter your email address:",
                "phone": "📞 Please enter your phone number (with country code):",
            }
        }

        # نستخدم edit_message_text لعرض الطلب داخل نفس الرسالة (أو نرسل رسالة جديدة إذا فشل)
        try:
            await query.edit_message_text(prompts[lang][field])
            # حفظ بيانات الرسالة الحالية (حتى نعود ونحدثها لاحقًا)
            context.user_data["form_message_id"] = query.message.message_id
            context.user_data["form_chat_id"] = query.message.chat_id
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=prompts[lang][field])
        return

    # حفظ التسجيل
    if query.data == "save_registration":
        reg = context.user_data.get("registration", {})
        missing = [k for k in ("name", "email", "phone") if not reg.get(k)]
        if missing:
            msg = "⚠️ يرجى تعبئة جميع الحقول قبل الحفظ." if lang == "ar" else "⚠️ Please fill all fields before saving."
            await query.answer(msg, show_alert=True)
            return

        # تحقق نهائي
        if not EMAIL_RE.match(reg["email"]):
            msg = "⚠️ البريد الإلكتروني غير صالح." if lang == "ar" else "⚠️ Invalid email address."
            await query.answer(msg, show_alert=True)
            return
        if not PHONE_RE.match(reg["phone"]):
            msg = "⚠️ رقم الهاتف غير صالح." if lang == "ar" else "⚠️ Invalid phone number."
            await query.answer(msg, show_alert=True)
            return
        # حفظ في قاعدة البيانات
        try:
            user = query.from_user
            save_subscriber(
                name=reg["name"],
                email=reg["email"],
                phone=reg["phone"],
                lang=reg.get("lang", lang),
                telegram_id=getattr(user, "id", None),
                telegram_username=getattr(user, "username", None),
            )
        except Exception:
            logger.exception("Error saving subscriber")

        success_msg = "✅ تم حفظ بياناتك بنجاح!" if lang == "ar" else "✅ Your data has been saved successfully!"
        try:
            await query.edit_message_text(success_msg)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=success_msg)

        # تنظيف حالات التسجيل
        context.user_data.pop("registration", None)
        context.user_data.pop("reg_state", None)
        context.user_data.pop("editing_field", None)
        context.user_data.pop("form_message_id", None)
        context.user_data.pop("form_chat_id", None)
        return

# ===============================
# استقبال رد المستخدم بعد طلب حقل معين
# ===============================
async def handle_registration_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يتعامل مع النص الذي يدخله المستخدم بعد أن يضغط زر تعديل حقل.
    يقوم بالتحقق من الصحة (email/phone) فورًا ثم يعيد عرض النموذج محدثًا.
    """
    msg = update.message
    if not msg or not msg.text:
        return

    field = context.user_data.get("editing_field")
    if not field:
        # ليست حالة تحرير، تجاهل أو مرر للمعالجات الأخرى
        return

    text = msg.text.strip()
    lang = context.user_data.get("lang", "ar")

    # تحقق فوري حسب الحقل
    if field == "email":
        if not EMAIL_RE.match(text):
            await msg.reply_text("⚠️ بريد إلكتروني غير صالح. حاول مرة أخرى:" if lang == "ar" else "⚠️ Invalid email. Try again:")
            # editing_field يبقى كما هو ليحاول المستخدم مجددًا
            return
    elif field == "phone":
        if not PHONE_RE.match(text):
            await msg.reply_text("⚠️ رقم هاتف غير صالح. حاول مرة أخرى:" if lang == "ar" else "⚠️ Invalid phone number. Try again:")
            return
    else:
        # name: تحقق بسيط (طول)
        if len(text) < 2:
            await msg.reply_text("⚠️ الاسم قصير جدًا. حاول مرة أخرى:" if lang == "ar" else "⚠️ Name too short. Try again:")
            return

    # حفظ القيمة
    reg = context.user_data.setdefault("registration", {})
    reg[field] = text
    context.user_data["editing_field"] = None

    # تأكيد الحفظ للمستخدم
    confirm_msg = "✅ تم حفظ القيمة!" if lang == "ar" else "✅ Value saved!"
    await msg.reply_text(confirm_msg)

    # إعادة عرض النموذج (ستعدل نفس الرسالة إن أمكن)
    await show_registration_form(update, context)

# ===============================
# بقيةhandlers: start, show_main_sections, menu_handler, set_language, cancel_registration_callback, after_registration_continue
# (أخذت الكود الأصلي مع تعديل بسيط: عند الضغط على نسخ الصفقات نستدعي show_registration_form)
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

async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"

    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "forex_main"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        title = "الأقسام الرئيسية"
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [
            ("💹 Forex Trading", "forex_main"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        title = "Main Sections"
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=cb)] for name, cb in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)

    labels = [name for name, _ in sections] + [back_button[0]]
    header = build_header_html(
        title,
        labels,
        header_emoji=header_emoji_for_lang,
        underline_enabled=True,
        underline_char="━",
        underline_length=25,
        underline_min=17,
        arabic_indent=1 if lang == "ar" else 0,
    )

    try:
        await query.edit_message_text(
            header,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=header,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

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

    if query.data == "back_language":
        await start(update, context)
        return
    if query.data == "back_main":
        await show_main_sections(update, context, lang)
        return

    # عند الضغط على "نسخ الصفقات" نعرض نموذج التسجيل التفاعلي
    if query.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
        context.user_data["registration"] = {"lang": lang}
        context.user_data["reg_state"] = "awaiting_name"
        # استدعاء عرض النموذج (سيعدل الرسالة الحالية)
        await show_registration_form(update, context)
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

async def cancel_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("registration", None)
    context.user_data.pop("reg_state", None)
    context.user_data.pop("editing_field", None)
    context.user_data.pop("form_message_id", None)
    context.user_data.pop("form_chat_id", None)
    lang = context.user_data.get("lang", "ar")
    if lang == "ar":
        await query.edit_message_text("تم إلغاء التسجيل.")
    else:
        await query.edit_message_text("Registration cancelled.")
    await show_main_sections(update, context, lang)

async def after_registration_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")
    if lang == "ar":
        title = "اختر الوسيط"
        brokers = [
            ("🏦 Oneroyall", "https://t.me/ZoozFX"),
            ("🏦 Tickmill", "https://t.me/ZoozFX")
        ]
        back_label = "🔙 الرجوع للقائمة الرئيسية"
        header_emoji_for_lang = "✨"
    else:
        title = "Choose your broker"
        brokers = [
            ("🏦 Oneroyall", "https://t.me/ZoozFX"),
            ("🏦 Tickmill", "https://t.me/ZoozFX")
        ]
        back_label = "🔙 Back to main menu"
        header_emoji_for_lang = "✨"

    keyboard = [[InlineKeyboardButton(name, url=url)] for name, url in brokers]
    keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    labels = [b[0] for b in brokers] + [back_label]
    header = build_header_html(
        title,
        labels,
        header_emoji=header_emoji_for_lang,
        underline_enabled=True,
        underline_length=25,
        underline_min=20,
        underline_char="━",
        arabic_indent=1 if lang == "ar" else 0,
    )

    try:
        await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

# ===============================
# تسجيل الهاندلرز
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))  # يتعامل مع معظم الزرار
application.add_handler(CallbackQueryHandler(cancel_registration_callback, pattern="^cancel_reg$"))
# هاندلر أزرار التعديل والحفظ في النموذج
application.add_handler(CallbackQueryHandler(registration_button_handler, pattern="^(edit_name|edit_email|edit_phone|save_registration)$"))
# هاندلر لاستقبال النصوص عند تعديل الحقول
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration_input))
# الاحتفاظ بالهاندلر العام للخيارات
application.add_handler(CallbackQueryHandler(after_registration_continue, pattern="^after_registration_continue$"))

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
