import os
import re
import logging
import unicodedata
from typing import List, Optional
import math
from datetime import datetime

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

# -------------------------------
# قاعدة البيانات (SQLAlchemy)
# -------------------------------
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import sessionmaker
from app.db import Base, engine

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# نموذج المستخدم للاشتراك في "نسخ الصفقات"
class CopyTradingUser(Base):
    __tablename__ = "copy_trading_users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, index=True, nullable=False)
    name = Column(String(200), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    lang = Column(String(5), default="ar")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# -------------------------------
# إعداد البوت و FastAPI
# -------------------------------
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
# build_header_html (محسّن)
# -------------------------------
def build_header_html(...):
    pass

# ===============================
# 1. /start → اختيار اللغة
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇺🇸 English", callback_data="lang_en"),
            InlineKeyboardButton("🇪🇬 العربية", callback_data="lang_ar"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    labels = ["🇺🇸 English", "🇪🇬 العربية"]

    header = "<b>Language | اللغة</b>"

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
# 2. الأقسام الرئيسية (معدلة لتستخدم callback_data ثابتة)
# ===============================
async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"

    if lang == "ar":
        sections = [
            ("💹 تداول الفوركس", "copy_trading"),
            ("💻 خدمات البرمجة", "dev_main"),
            ("🤝 طلب وكالة YesFX", "agency_main"),
        ]
        back_button = ("🔙 الرجوع للغة", "back_language")
        title = "الأقسام الرئيسية"
    else:
        sections = [
            ("💹 Forex Trading", "copy_trading"),
            ("💻 Programming Services", "dev_main"),
            ("🤝 YesFX Partnership", "agency_main"),
        ]
        back_button = ("🔙 Back to language", "back_language")
        title = "Main Sections"

    labels = [name for name, _ in sections]
    header = f"<b>{title}</b>"

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

# ===============================
# 4. منطق قائمة الأقسام وبدء تسجيل "نسخ الصفقات"
# ===============================
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"[0-9+()\-\s]{6,25}$")

async def start_copy_trading_flow(query, context: ContextTypes.DEFAULT_TYPE):
    """ابدأ جمع بيانات المستخدم: name -> email -> phone"""
    user = query.from_user
    lang = context.user_data.get("lang", "ar")

    # تأكد أن لدينا سجل مستخدم (أو أنشئ واحدًا)
    db = SessionLocal()
    try:
        db_user = db.query(CopyTradingUser).filter(CopyTradingUser.telegram_id == user.id).first()
        if not db_user:
            db_user = CopyTradingUser(telegram_id=user.id, lang=lang)
            db.add(db_user)
            db.commit()
            db.refresh(db_user)
    finally:
        db.close()

    # نحفظ حالة الجمع في سياق المستخدم
    context.user_data["copy_trading_flow"] = {
        "step": "name",
        "editing": False,
    }

    prompt = "الرجاء إدخال اسمك الكامل:" if lang == "ar" else "Please enter your full name:"
    try:
        await query.edit_message_text(prompt, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=prompt)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذا المعالج يتعامل مع جميع أزرار الـ callback_data
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")

    if query.data == "back_language":
        await start(update, context)
        return
    if query.data == "back_main":
        await show_main_sections(update, context, lang)
        return

    if query.data == "copy_trading":
        await start_copy_trading_flow(query, context)
        return

    # التحرير: عرض خيارات التعديل بعد التسجيل
    if query.data.startswith("edit_"):
        field = query.data.split("edit_")[-1]
        context.user_data["copy_trading_flow"] = {"step": field, "editing": True}
        prompt_map = {
            "name": ("الرجاء إرسال الاسم الجديد:", "Send new name:"),
            "email": ("الرجاء إرسال البريد الإلكتروني الجديد:", "Send new email:"),
            "phone": ("الرجاء إرسال رقم الهاتف الجديد:", "Send new phone:"),
        }
        prompt = prompt_map[field][0] if lang == "ar" else prompt_map[field][1]
        try:
            await query.edit_message_text(prompt)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=prompt)
        return

    # زر "عرض/تعديل بياناتي"
    if query.data == "view_my_data":
        db = SessionLocal()
        try:
            db_user = db.query(CopyTradingUser).filter(CopyTradingUser.telegram_id == query.from_user.id).first()
            if not db_user:
                text = "لا توجد بيانات مسجلة. اضغط على \"نسخ الصفقات\" للانضمام." if lang == "ar" else "No data found. Press 'Copy Trading' to join."
                await query.edit_message_text(text)
                return

            lines = []
            if lang == "ar":
                lines = [f"<b>الاسم:</b> {db_user.name or '—'}", f"<b>البريد:</b> {db_user.email or '—'}", f"<b>الهاتف:</b> {db_user.phone or '—'}"]
                txt = "\n".join(lines)
                kb = [
                    [InlineKeyboardButton("✏️ تعديل الاسم", callback_data="edit_name")],
                    [InlineKeyboardButton("✏️ تعديل البريد", callback_data="edit_email")],
                    [InlineKeyboardButton("✏️ تعديل الهاتف", callback_data="edit_phone")],
                    [InlineKeyboardButton("🔙 الرجوع", callback_data="back_main")],
                ]
            else:
                lines = [f"<b>Name:</b> {db_user.name or '—'}", f"<b>Email:</b> {db_user.email or '—'}", f"<b>Phone:</b> {db_user.phone or '—'}"]
                txt = "\n".join(lines)
                kb = [
                    [InlineKeyboardButton("✏️ Edit name", callback_data="edit_name")],
                    [InlineKeyboardButton("✏️ Edit email", callback_data="edit_email")],
                    [InlineKeyboardButton("✏️ Edit phone", callback_data="edit_phone")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                ]

            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", disable_web_page_preview=True)
        finally:
            db.close()
        return

    # بقية الأزرار المعروفة (أقسام فرعية)
    sections_data = {
        "dev_main": {
            "ar": ["📈 برمجة المؤشرات", "🤖 برمجة الاكسبيرتات", "💬 بوتات التليجرام", "🌐 مواقع الويب"],
            "en": ["📈 Indicators", "🤖 Expert Advisors", "💬 Telegram Bots", "🌐 Web Development"],
            "title_ar": "خدمات البرمجة",
            "title_en": "Programming Services",
        },
        "agency_main": {
            "ar": ["📄 طلب وكالة YesFX"],
            "en": ["📄 Request YesFX Partnership"],
            "title_ar": "طلب وكالة",
            "title_en": "Partnership",
        },
    }

    if query.data in sections_data:
        data = sections_data[query.data]
        options = data[lang]
        title = data[f"title_{lang}"]

        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"
        labels = options + [back_label]

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        box = f"<b>{title}</b>"
        try:
            await query.edit_message_text(box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    # افتراضي: عرض رسالة اختيار الخدمة
    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    try:
        await query.edit_message_text(f"🔹 {placeholder}: {query.data}\n\n{details}", parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"🔹 {placeholder}: {query.data}\n\n{details}", disable_web_page_preview=True)

# ===============================
# معالج الرسائل النصية: استقبال اسم/بريد/هاتف
# ===============================
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.message.from_user
    data = context.user_data.get("copy_trading_flow")
    if not data:
        return  # لا يوجد جمع بيانات جارٍ

    step = data.get("step")
    editing = data.get("editing", False)
    text = update.message.text.strip()
    lang = context.user_data.get("lang", "ar")

    db = SessionLocal()
    try:
        db_user = db.query(CopyTradingUser).filter(CopyTradingUser.telegram_id == user.id).first()
        if not db_user:
            db_user = CopyTradingUser(telegram_id=user.id, lang=lang)
            db.add(db_user)
            db.commit()
            db.refresh(db_user)

        # خطوة الاسم
        if step == "name":
            if len(text) < 2:
                await update.message.reply_text("الاسم قصير جدًا. حاول مرة أخرى." if lang == "ar" else "Name too short. Try again.")
                return
            db_user.name = text
            db.commit()
            context.user_data["copy_trading_flow"]["step"] = "email"
            prompt = "الآن أدخل بريدك الإلكتروني:" if lang == "ar" else "Now enter your email:"
            await update.message.reply_text(prompt)
            return

        if step == "email":
            if not EMAIL_RE.match(text):
                await update.message.reply_text("البريد غير صالح. حاول مرة أخرى." if lang == "ar" else "Invalid email. Try again.")
                return
            db_user.email = text
            db.commit()
            context.user_data["copy_trading_flow"]["step"] = "phone"
            prompt = "الآن أدخل رقم هاتفك (يمكنك إضافة رمز الدولة):" if lang == "ar" else "Now enter your phone number (include country code):"
            await update.message.reply_text(prompt)
            return

        if step == "phone":
            if not PHONE_RE.search(text):
                await update.message.reply_text("رقم الهاتف غير صالح. حاول مرة أخرى." if lang == "ar" else "Invalid phone. Try again.")
                return
            db_user.phone = text
            db.commit()

            # انتهى الجمع — أظهر ملخصًا مع أزرار للتعديل
            if lang == "ar":
                txt = f"✅ تم تسجيلك للنسخ:\n\n<b>الاسم:</b> {db_user.name}\n<b>البريد:</b> {db_user.email}\n<b>الهاتف:</b> {db_user.phone}"
                kb = [
                    [InlineKeyboardButton("✏️ تعديل الاسم", callback_data="edit_name")],
                    [InlineKeyboardButton("✏️ تعديل البريد", callback_data="edit_email")],
                    [InlineKeyboardButton("✏️ تعديل الهاتف", callback_data="edit_phone")],
                    [InlineKeyboardButton("🔙 الرجوع", callback_data="back_main")],
                ]
            else:
                txt = f"✅ You are registered for copy trading:\n\n<b>Name:</b> {db_user.name}\n<b>Email:</b> {db_user.email}\n<b>Phone:</b> {db_user.phone}"
                kb = [
                    [InlineKeyboardButton("✏️ Edit name", callback_data="edit_name")],
                    [InlineKeyboardButton("✏️ Edit email", callback_data="edit_email")],
                    [InlineKeyboardButton("✏️ Edit phone", callback_data="edit_phone")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                ]

            # مسح حالة الجمع
            context.user_data.pop("copy_trading_flow", None)
            await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", disable_web_page_preview=True)
            return

        # إذا كان المستخدم في وضع التحرير editing
        if editing and step in ("name", "email", "phone"):
            field = step
            if field == "email" and not EMAIL_RE.match(text):
                await update.message.reply_text("البريد غير صالح. حاول مرة أخرى." if lang == "ar" else "Invalid email. Try again.")
                return
            if field == "phone" and not PHONE_RE.search(text):
                await update.message.reply_text("رقم الهاتف غير صالح. حاول مرة أخرى." if lang == "ar" else "Invalid phone. Try again.")
                return

            setattr(db_user, field, text)
            db.commit()
            context.user_data.pop("copy_trading_flow", None)

            done_msg = "تم تحديث بياناتك." if lang == "ar" else "Your data has been updated."
            await update.message.reply_text(done_msg)
            # عرض البيانات المحدثة
            await show_user_data_quick(update.message.chat_id, user.id, context, lang)
            return

    finally:
        db.close()

async def show_user_data_quick(chat_id: int, telegram_id: int, context: ContextTypes.DEFAULT_TYPE, lang: str):
    db = SessionLocal()
    try:
        db_user = db.query(CopyTradingUser).filter(CopyTradingUser.telegram_id == telegram_id).first()
        if not db_user:
            return
        if lang == "ar":
            lines = [f"<b>الاسم:</b> {db_user.name or '—'}", f"<b>البريد:</b> {db_user.email or '—'}", f"<b>الهاتف:</b> {db_user.phone or '—'}"]
            txt = "\n".join(lines)
            kb = [
                [InlineKeyboardButton("✏️ تعديل الاسم", callback_data="edit_name")],
                [InlineKeyboardButton("✏️ تعديل البريد", callback_data="edit_email")],
                [InlineKeyboardButton("✏️ تعديل الهاتف", callback_data="edit_phone")],
                [InlineKeyboardButton("🔙 الرجوع", callback_data="back_main")],
            ]
        else:
            lines = [f"<b>Name:</b> {db_user.name or '—'}", f"<b>Email:</b> {db_user.email or '—'}", f"<b>Phone:</b> {db_user.phone or '—'}"]
            txt = "\n".join(lines)
            kb = [
                [InlineKeyboardButton("✏️ Edit name", callback_data="edit_name")],
                [InlineKeyboardButton("✏️ Edit email", callback_data="edit_email")],
                [InlineKeyboardButton("✏️ Edit phone", callback_data="edit_phone")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]

        await context.bot.send_message(chat_id=chat_id, text=txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", disable_web_page_preview=True)
    finally:
        db.close()

# ===============================
# Handlers registration
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

# ===============================
# Webhook setup (مثل السابق)
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
