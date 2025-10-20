# app/main.py
import os
import re
import logging
import unicodedata
import asyncio
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# استيراد أداة DB (Base, engine, SessionLocal) من ملفك الحالي app/db.py
from app.db import Base, engine, SessionLocal

# -------------------------------
# إعداد السجلات
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء جداول أساسية (في حال لم تُنشأ)
Base.metadata.create_all(bind=engine)

# متغيرات البيئة للـ webhook والبوت
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

# إنشاء تطبيق Telegram و FastAPI
application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# -------------------------------
# نموذج SQLAlchemy لتخزين التسجيل (إن لم يكن موجودًا)
# -------------------------------
from sqlalchemy import Column, Integer, String, DateTime, func
from app.db import Base as _Base

class Registration(_Base):
    __tablename__ = "registrations"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, index=True, nullable=False)
    name = Column(String(256), nullable=True)
    email = Column(String(256), nullable=True)
    phone = Column(String(64), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())

# تأكد من إنشاء الجدول
_Base.metadata.create_all(bind=engine)

# -------------------------------
# مساعدة: قياس عرض النص وإزالة الإيموجي
# -------------------------------
def remove_emoji(text: str) -> str:
    out = []
    for ch in text:
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
            continue
        out.append(ch)
    return "".join(out)

import unicodedata
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
# بناء رأس (box) مع محاذاة — تستخدمه كل القوائم
# -------------------------------
NBSP = "\u00A0"
RLE = "\u202B"
PDF = "\u202C"
LRM = "\u200E"

def build_header_html(
    title: str,
    keyboard_labels: List[str],
    header_emoji: str = "✨",
    underline_length: int = 28,
    align: str = "center",
    arabic_indent: int = 0,
    english_indent: int = 0,
) -> str:
    # اكتشاف عربي
    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    if is_arabic:
        indent = NBSP * arabic_indent
        full_title = f"{indent}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        indent = NBSP * english_indent
        full_title = f"{indent}{LRM}{header_emoji} {title} {header_emoji}{LRM}"

    # حساب العرض المستهدف بناء على أوسع زر أو طول افتراضي
    title_width = display_width(remove_emoji(full_title))
    target_width = max(max_button_width(keyboard_labels), underline_length)
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left

    if align.lower() == "left":
        pad_left = 0
        pad_right = max(0, target_width - title_width)
    elif align.lower() == "right":
        pad_right = 0
        pad_left = max(0, target_width - title_width)

    centered_line = f"{NBSP * pad_left}<b>{full_title}</b>{NBSP * pad_right}"
    underline = "━" * underline_length
    diff = max(0, target_width - underline_length)
    pad_left_line = diff // 2
    pad_right_line = diff - pad_left_line
    underline_line = f"\n{NBSP * pad_left_line}{underline}{NBSP * pad_right_line}"

    return centered_line + underline_line

# -------------------------------
# Validation helpers
# -------------------------------
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")  # بسيط: يسمح + و 7-15 رقم

def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email.strip()))

def is_valid_phone(phone: str) -> bool:
    # قم بتنظيف الفراغات وشرطات
    p = re.sub(r"[ \-\(\)]", "", phone)
    return bool(PHONE_RE.match(p))

# -------------------------------
# وظائف عرض واجهات التسجيل (نسخ الصفقات)
# -------------------------------
def registration_status_emoji(value: Optional[str]) -> str:
    return "🟢" if value else "🔴"

async def show_registration_menu_for_query(query, context: ContextTypes.DEFAULT_TYPE):
    """
    يعرض صفحة تسجيل نسخ الصفقات (يعدل رسالة callback).
    """
    user_data = context.user_data.setdefault("reg", {})
    name = user_data.get("name")
    email = user_data.get("email")
    phone = user_data.get("phone")
    submitted = context.user_data.get("submitted", False)

    labels = [
        f"{registration_status_emoji(name)} الاسم / Name",
        f"{registration_status_emoji(email)} الايميل / Email",
        f"{registration_status_emoji(phone)} رقم الهاتف / Phone",
    ]

    title = "نسخ الصفقات" if context.user_data.get("lang", "ar") == "ar" else "Copy Trading"
    header = build_header_html(title, labels, header_emoji="🔐", underline_length=30)

    # أزرار لكل حقل
    kb = [
        [InlineKeyboardButton(f"{registration_status_emoji(name)} الاسم", callback_data="reg_name")],
        [InlineKeyboardButton(f"{registration_status_emoji(email)} الايميل", callback_data="reg_email")],
        [InlineKeyboardButton(f"{registration_status_emoji(phone)} رقم الهاتف", callback_data="reg_phone")],
    ]

    # إذا لم يتم الإرسال بعد أزرار: تعديل + إرسال (مرئية عالميًا)
    if not submitted:
        kb.append([
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data="reg_edit"),
            InlineKeyboardButton("✅ أكملت البيانات (Submit)", callback_data="reg_submit")
        ])
    else:
        # إذا تم الإرسال، أظهر زر نسخ حساب الوسيط أيضاً
        kb.append([
            InlineKeyboardButton("🔁 تعديل البيانات", callback_data="reg_edit"),
            InlineKeyboardButton("📋 نسخ حساب الوسيط", callback_data="copy_broker_account")
        ])

    # زر عودة للقائمة الرئيسية
    kb.append([InlineKeyboardButton("🔙 الرجوع للقائمة الرئيسية", callback_data="back_main")])
    reply = InlineKeyboardMarkup(kb)

    try:
        await query.edit_message_text(header, reply_markup=reply, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        # إذا لم يكن callback (أو حدث استثناء) أرسِل رسالة جديدة
        chat_id = query.message.chat_id if hasattr(query, "message") else context.chat_id
        await context.bot.send_message(chat_id=chat_id, text=header, reply_markup=reply, parse_mode="HTML", disable_web_page_preview=True)

# wrapper to call from menu_handler
async def show_registration_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await show_registration_menu_for_query(query, context)

# -------------------------------
# Handlers for registration callbacks
# -------------------------------
async def registration_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_data = context.user_data.setdefault("reg", {})

    # Pressed name/email/phone — نضع await flag ثم نطلب الإدخال
    if data == "reg_name":
        context.user_data["awaiting"] = "name"
        prompt = "الرجاء إدخال اسمك الكامل:" if context.user_data.get("lang", "ar") == "ar" else "Please enter your full name:"
        await query.edit_message_text(prompt)
        return

    if data == "reg_email":
        context.user_data["awaiting"] = "email"
        prompt = "الرجاء إدخال البريد الإلكتروني:" if context.user_data.get("lang", "ar") == "ar" else "Please enter your email address:"
        await query.edit_message_text(prompt)
        return

    if data == "reg_phone":
        context.user_data["awaiting"] = "phone"
        prompt = "الرجاء إدخال رقم الهاتف (مثال: +201XXXXXXXX):" if context.user_data.get("lang", "ar") == "ar" else "Please enter your phone number (e.g. +201XXXXXXXX):"
        await query.edit_message_text(prompt)
        return

    # تعديل: فقط نعرض القائمة مع القيم الحالية (يمكن استخدامها كـ "edit")
    if data == "reg_edit":
        # إن أردنا يمكن إرسال رسالة توضيحية ثم القائمة
        await show_registration_menu_for_query(query, context)
        return

    # submit -> تحقق ثم حفظ في DB
    if data == "reg_submit":
        # تحقق أن الثلاثة قيم موجودة وصالحة
        name = user_data.get("name")
        email = user_data.get("email")
        phone = user_data.get("phone")
        if not (name and email and phone):
            msg = "الرجاء إكمال جميع الحقول قبل التأكيد." if context.user_data.get("lang", "ar") == "ar" else "Please complete all fields before submitting."
            await query.answer(msg, show_alert=True)
            await show_registration_menu_for_query(query, context)
            return

        # تحقق بسيط من الصيغة
        if not is_valid_email(email):
            await query.answer("بريد إلكتروني غير صالح.", show_alert=True)
            await show_registration_menu_for_query(query, context)
            return
        if not is_valid_phone(phone):
            await query.answer("رقم هاتف غير صالح.", show_alert=True)
            await show_registration_menu_for_query(query, context)
            return

        # حفظ في قاعدة البيانات
        try:
            session = SessionLocal()
            reg = Registration(
                telegram_id=update.effective_user.id,
                name=name.strip(),
                email=email.strip(),
                phone=re.sub(r"[ \-\(\)]", "", phone.strip()),
                submitted_at=datetime.utcnow()
            )
            session.add(reg)
            session.commit()
            session.refresh(reg)
            session.close()
            context.user_data["submitted"] = True
        except Exception as e:
            logger.exception("Failed to save registration")
            await query.answer("حصل خطأ أثناء حفظ البيانات. حاول لاحقًا.", show_alert=True)
            await show_registration_menu_for_query(query, context)
            return

        # بعد الحفظ أعرض رسالة تأكيد + زر نسخ حساب الوسيط
        confirm_text = "✅ تم حفظ بياناتك بنجاح!" if context.user_data.get("lang", "ar") == "ar" else "✅ Your data has been saved!"
        # زر نسخ الحساب
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 نسخ حساب الوسيط", callback_data="copy_broker_account")],
            [InlineKeyboardButton("🔙 العودة", callback_data="back_main")]
        ])
        await query.edit_message_text(confirm_text, reply_markup=kb)
        return

    # copy_broker_account: نُرسل بيانات حساب تجريبي (مثال)
    if data == "copy_broker_account":
        # نتحقق إن كان المستخدم قد أرسل بيانات
        if not context.user_data.get("submitted", False):
            msg = "يجب أولاً إكمال إرسال البيانات." if context.user_data.get("lang", "ar") == "ar" else "You must submit your data first."
            await query.answer(msg, show_alert=True)
            await show_registration_menu_for_query(query, context)
            return

        # مثال: هذا هو نص حساب الوسيط الذي نعرضه -- عدّله حسب الحاجة
        broker_text = (
            "🔐 حساب الوسيط (مثال):\n"
            "اسم المستخدم: demo_user\n"
            "كلمة المرور: Demo@123\n"
            "خادم: demo.broker.example\n"
            "🔔 انسخ هذه البيانات واستخدمها في برنامجك."
        ) if context.user_data.get("lang", "ar") == "ar" else (
            "🔐 Broker Account (example):\n"
            "Username: demo_user\n"
            "Password: Demo@123\n"
            "Server: demo.broker.example\n"
            "🔔 Copy these credentials for your platform."
        )

        # نرسل رسالة خاصة (وليس تعديل)
        await query.message.reply_text(broker_text)
        return

    # أي callback آخر: لا نعرفه → نعيد القائمة
    await show_registration_menu_for_query(query, context)

# -------------------------------
# Message handler لالتقاط المدخلات النصية أثناء انتظار الحقول
# -------------------------------
async def collect_registration_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    عندما يضغط المستخدم على (الاسم/الايميل/الهاتف) نضع awaiting = 'name'/'email'/'phone'.
    ثم أي رسالة واردة تُعامل كقيمة لذلك الحقل.
    """
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        # رسالة عادية — لا نتدخل (أو يمكنك الرد بمساعدة)
        return

    text = update.message.text.strip()
    lang = context.user_data.get("lang", "ar")
    # validate and store
    if awaiting == "name":
        # قبول أي نص مع طول معقول
        if len(text) < 2:
            await update.message.reply_text("الاسم قصير جدًا. حاول مجددًا." if lang == "ar" else "Name too short. Try again.")
            return
        context.user_data.setdefault("reg", {})["name"] = text
        context.user_data.pop("awaiting", None)
        # عد إلى قائمة التسجيل المحدثة
        # حذف رسالة المستخدم لتحسين النظافة (اختياري)
        try:
            await update.message.delete()
        except Exception:
            pass
        # عرض القائمة
        # نحتاج إلى إنشاء dummy callback-like object to edit previous message — نستخدم send_message
        await show_registration_menu_after_input(update, context)
        return

    if awaiting == "email":
        if not is_valid_email(text):
            await update.message.reply_text("البريد الإلكتروني غير صالح. حاول مجددًا." if lang == "ar" else "Invalid email. Try again.")
            return
        context.user_data.setdefault("reg", {})["email"] = text
        context.user_data.pop("awaiting", None)
        try:
            await update.message.delete()
        except Exception:
            pass
        await show_registration_menu_after_input(update, context)
        return

    if awaiting == "phone":
        if not is_valid_phone(text):
            await update.message.reply_text("رقم الهاتف غير صالح. تأكد من تنسيقه، مثال: +2010XXXXXXXX" if lang == "ar" else "Invalid phone number. Use format e.g. +2010XXXXXXXX")
            return
        context.user_data.setdefault("reg", {})["phone"] = text
        context.user_data.pop("awaiting", None)
        try:
            await update.message.delete()
        except Exception:
            pass
        await show_registration_menu_after_input(update, context)
        return

async def show_registration_menu_after_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    بعد إدخال المستخدم لبيان، نُرسل/نعدل رسالة القائمة المحدثة.
    (نحاول تعديل آخر رسالة callback إذا أمكن، وإلا نرسل رسالة جديدة).
    """
    chat_id = update.effective_chat.id
    # حاول تعديل آخر رسالة callback إذا كانت موجودة في context (غير مضمون)
    # الأسهل: إرسال رسالة جديدة تعرض القائمة المحدثة
    # لكن لتفادي تكديس الرسائل يمكننا حذف رسائل سابقة أو نهج آخر - هنا نرسل رسالة جديدة
    # وإضافة ملاحظة أن المستخدم أكمل الحقل
    await show_registration_menu_by_chat(chat_id, context)

async def show_registration_menu_by_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data.setdefault("reg", {})
    name = user_data.get("name")
    email = user_data.get("email")
    phone = user_data.get("phone")
    submitted = context.user_data.get("submitted", False)

    labels = [
        f"{registration_status_emoji(name)} الاسم / Name",
        f"{registration_status_emoji(email)} الايميل / Email",
        f"{registration_status_emoji(phone)} رقم الهاتف / Phone",
    ]
    title = "نسخ الصفقات" if context.user_data.get("lang", "ar") == "ar" else "Copy Trading"
    header = build_header_html(title, labels, header_emoji="🔐", underline_length=30)
    kb = [
        [InlineKeyboardButton(f"{registration_status_emoji(name)} الاسم", callback_data="reg_name")],
        [InlineKeyboardButton(f"{registration_status_emoji(email)} الايميل", callback_data="reg_email")],
        [InlineKeyboardButton(f"{registration_status_emoji(phone)} رقم الهاتف", callback_data="reg_phone")],
    ]
    if not submitted:
        kb.append([
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data="reg_edit"),
            InlineKeyboardButton("✅ أكملت البيانات (Submit)", callback_data="reg_submit")
        ])
    else:
        kb.append([
            InlineKeyboardButton("🔁 تعديل البيانات", callback_data="reg_edit"),
            InlineKeyboardButton("📋 نسخ حساب الوسيط", callback_data="copy_broker_account")
        ])
    kb.append([InlineKeyboardButton("🔙 الرجوع للقائمة الرئيسية", callback_data="back_main")])
    reply = InlineKeyboardMarkup(kb)
    await application.bot.send_message(chat_id=chat_id, text=header, reply_markup=reply, parse_mode="HTML", disable_web_page_preview=True)

# -------------------------------
# تسجيل handlers إلى التطبيق
# -------------------------------
# handlers رئيسية من البداية (start, set_language, menu_handler موجودان سابقًا)
# سنعيد تعريف show_main_sections و menu_handler لندمج النقلة لصفحة التسجيل
# لكن لتجنب تعقيد السنوات، سنقوم بإضافة callback للـ 'forex_copy' داخل menu_handler السابق.
#
# هنا: نضيف handler جديد للتعامل مع جميع callbacks الخاصة بالتسجيل (reg_*, copy_broker_account, back_main)
application.add_handler(CallbackQueryHandler(registration_callback_handler, pattern="^reg_|^copy_broker_account$"))
# handler لالتقاط النصوص أثناء انتظار إدخال المستخدم لأي حقل
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_registration_input))

# ===============================
# إعادة تعريف/تعديل menu_handler (للتأكد أنه يبحث عن 'forex_copy' ويستدعي واجهة التسجيل)
# ===============================
# ملاحظة: إذا كان لديك وظيفة menu_handler موجودة مسبقًا، استبدل منطق حالة 'forex_copy' بها.
# فيما يلي نسخة تجمع بين استخدام context.user_data["lang"] وفتح صفحة التسجيل.
async def menu_handler_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هذا handler يعالج اختيارات القوائم الفرعية بحيث إذا اختار المستخدم 'forex_copy' 
    نفتح صفحة التسجيل. للخدمات الأخرى نعيد السلوك كما كان.
    """
    query = update.callback_query
    if not query:
        return
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    # رجوع لاختيار اللغة
    if query.data == "back_language":
        # إعادة استخدام الدالة start (التي تتعرف على callback أو message)
        await start(update, context)
        return

    # رجوع للقائمة الرئيسية
    if query.data == "back_main":
        await show_main_sections(update, context, lang)
        return

    # إذا ضغط المستخدم مباشرة على زر يحمل اسم الخدمة، في حالتنا نستخدم key names في sections_data
    # سنفحص بعض القيم: 'forex_copy' هو الزر الرئيسي ضمن forex_main
    # في حالتك الأصلية زر 'forex_copy' يظهر من show_main_sections كزر داخلي؛ لكن هنا نحن نستدعي
    # show_registration_menu عندما يضغط المستخدم على 'forex_copy' خلال menu.
    # لتوافق، سنضع حالات محددة:
    if query.data == "forex_copy" or query.data == "💹 تداول الفوركس" or query.data.lower().startswith("forex"):
        # فتح واجهة التسجيل (نسخ الصفقات)
        # تأكد أن لغة المستخدم محفوظة في context.user_data["lang"]
        await show_registration_menu_for_query(query, context)
        return

    # إذا كانت callback تساوي أحد المفاتيح الأخرى، نستخدم السلوك الافتراضي: عرض رسالة placeholder
    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}")

# استبدل handler القديم handler(menu_handler) handler جديد
# أولاً ـ نزيل أي handler قديم pattern="^menu_" إن وُجد — لكن هنا نضيف بأولوية
application.add_handler(CallbackQueryHandler(menu_handler_full, pattern="^forex_main$|^dev_main$|^agency_main$|^forex_copy$"))

# -------------------------------
# Handlers أصلية: start و set_language و show_main_sections
# نضيف handlers إذا لم تكن مُسجلة
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
# show_main_sections يُستدعى من set_language أو menu handler مباشرة

# -------------------------------
# Webhook endpoints
# -------------------------------
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
