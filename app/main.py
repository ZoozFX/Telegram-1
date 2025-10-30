import os
import re
import json
import logging
import unicodedata
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import urlencode, quote_plus
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Body, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from app.db import Base, engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String, ForeignKey, BigInteger
from sqlalchemy.orm import relationship
import asyncio
from sqlalchemy import text, inspect

ADMIN_TELEGRAM_IDS = [int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_ID", "").split(",") if x.strip()]
AGENTS_LIST = os.getenv("AGENTS_LIST", "ملك الدهب").split(",")
# -------------------------------
# logging
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------
# DB model
# -------------------------------
SessionLocal = sessionmaker(bind=engine)

class Subscriber(Base):
    __tablename__ = "subscribers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False)
    phone = Column(String(50), nullable=False)
    telegram_username = Column(String(200), nullable=True)
    telegram_id = Column(BigInteger, nullable=True, unique=True)
    lang = Column(String(8), default="ar")
    trading_accounts = relationship("TradingAccount", back_populates="subscriber", cascade="all, delete-orphan")

class TradingAccount(Base):
    __tablename__ = "trading_accounts"
    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    broker_name = Column(String(100), nullable=False)
    account_number = Column(String(100), nullable=False)
    password = Column(String(100), nullable=False)
    server = Column(String(100), nullable=False)
    initial_balance = Column(String(50), nullable=True)
    current_balance = Column(String(50), nullable=True)
    withdrawals = Column(String(50), nullable=True)
    copy_start_date = Column(String(50), nullable=True)
    agent = Column(String(100), nullable=True)
    expected_return = Column(String(100), nullable=True)
    created_at = Column(String(50), default=lambda: datetime.now().isoformat())
    status = Column(String(20), default="under_review")
    rejection_reason = Column(String(255), nullable=True)
    subscriber = relationship("Subscriber", back_populates="trading_accounts")

# الجدول الجديد المطلوب
class AccountPerformance(Base):
    __tablename__ = "account_performances"
    id = Column(Integer, primary_key=True, index=True)
    trading_account_id = Column(Integer, ForeignKey('trading_accounts.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(200), nullable=False)  # من Subscriber
    email = Column(String(200), nullable=False)  # من Subscriber
    phone = Column(String(50), nullable=False)  # من Subscriber
    telegram_username = Column(String(200), nullable=True)  # من Subscriber
    initial_balance = Column(String(50), nullable=True)  # من TradingAccount
    achieved_return = Column(String(50), nullable=True)  # محسوب (مثل: "25%")
    copy_duration = Column(String(50), nullable=True)  # محسوب (مثل: "3 أشهر")

    # علاقة مع جدول TradingAccount
    trading_account = relationship("TradingAccount")

Base.metadata.create_all(bind=engine)

# -------------------------------
# دالة جديدة لملء جدول AccountPerformance
# -------------------------------
def populate_account_performances():
    db = SessionLocal()
    try:
        # جلب جميع الحسابات النشطة
        accounts = db.query(TradingAccount).filter(TradingAccount.status == 'active').all()
        
        for account in accounts:
            subscriber = account.subscriber
            
            # التحقق من وجود البيانات اللازمة
            if not (account.initial_balance and account.current_balance and 
                    account.withdrawals and account.copy_start_date):
                continue
            
            try:
                initial = float(account.initial_balance)
                current = float(account.current_balance)
                withdrawals = float(account.withdrawals)
                
                # حساب العائد المحقق
                if initial > 0:
                    total_value = current + withdrawals
                    profit = total_value - initial
                    achieved_return = f"{(profit / initial * 100):.0f}%"
                else:
                    achieved_return = "0%"
                
                # حساب المدة
                start_date = datetime.strptime(account.copy_start_date, '%Y-%m-%d')
                today = datetime.now()
                delta = today - start_date
                total_days = delta.days
                
                months = total_days // 30
                remaining_days = total_days % 30
                
                if months > 0:
                    copy_duration = f"{months} شهر"
                    if remaining_days > 0:
                        copy_duration += f" و{remaining_days} يوم"
                else:
                    copy_duration = f"{total_days} يوم"
                
                # التحقق مما إذا كان السجل موجودًا بالفعل
                existing_perf = db.query(AccountPerformance).filter(
                    AccountPerformance.trading_account_id == account.id
                ).first()
                
                if existing_perf:
                    # تحديث السجل الموجود
                    existing_perf.name = subscriber.name
                    existing_perf.email = subscriber.email
                    existing_perf.phone = subscriber.phone
                    existing_perf.telegram_username = subscriber.telegram_username
                    existing_perf.initial_balance = account.initial_balance
                    existing_perf.achieved_return = achieved_return
                    existing_perf.copy_duration = copy_duration
                else:
                    # إنشاء سجل جديد
                    performance = AccountPerformance(
                        trading_account_id=account.id,
                        name=subscriber.name,
                        email=subscriber.email,
                        phone=subscriber.phone,
                        telegram_username=subscriber.telegram_username,
                        initial_balance=account.initial_balance,
                        achieved_return=achieved_return,
                        copy_duration=copy_duration
                    )
                    db.add(performance)
                
                db.commit()
                
            except ValueError as ve:
                logger.error(f"خطأ في تحويل القيم للحساب {account.id}: {ve}")
                continue
            
        logger.info("تم ملء جدول الأداء بنجاح!")
        
    except Exception as e:
        logger.exception(f"خطأ في ملء جدول الأداء: {e}")
    finally:
        db.close()

def reset_sequences():
    inspector = inspect(engine)
    tables = ['subscribers', 'trading_accounts', 'account_performances']  # أضف الجداول الأخرى إذا لزم
    
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            for table in tables:
                # جلب أعلى ID
                max_id_result = conn.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
                max_id = max_id_result if max_id_result is not None else 0
                
                # تحديث sqlite_sequence
                conn.execute(text(f"INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('{table}', {max_id})"))
            conn.commit()
        logger.info("✅ تم إعادة تعيين التسلسل في SQLite بنجاح!")
        
    elif engine.dialect.name == 'postgresql':
        with engine.connect() as conn:
            for table in tables:
                seq_name = f"{table}_id_seq"
                conn.execute(text(f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) + 1 FROM {table}), 1), false)"))
            conn.commit()
        logger.info("✅ تم إعادة تعيين التسلسل في PostgreSQL بنجاح!")
        
    elif engine.dialect.name == 'mysql':
        with engine.connect() as conn:
            for table in tables:
                max_id_result = conn.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
                max_id = (max_id_result or 0) + 1
                conn.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = {max_id}"))
            conn.commit()
        logger.info("✅ تم إعادة تعيين التسلسل في MySQL بنجاح!")
        
    else:
        logger.error(f"❌ نوع قاعدة البيانات غير مدعوم: {engine.dialect.name}")

# -------------------------------
# settings & app
# -------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL") or (f"{WEBHOOK_URL}/webapp" if WEBHOOK_URL else None)

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")
if not WEBAPP_URL:
    logger.warning("⚠️ WEBAPP_URL not set — WebApp button may not work without a public URL.")

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

HEADER_EMOJI = "✨"
NBSP = "\u00A0"
FORM_MESSAGES: Dict[int, Dict[str, Any]] = {}
# -------------------------------
# helpers: emoji removal / display width
# -------------------------------
NOTIFICATION_MESSAGES: Dict[int, List[Dict[str, Any]]] = {}
ADMIN_LANGUAGE: Dict[int, str] = {}

SECRET_KEY = os.getenv("SECRET_KEY", "my_secret_key")  # استخدام متغير بيئة للسيكريت كي

def set_admin_language(admin_id: int, lang: str):
    
    ADMIN_LANGUAGE[admin_id] = lang

def get_admin_language(admin_id: int) -> str:
    
    return ADMIN_LANGUAGE.get(admin_id, "ar")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await update.message.reply_text("❌ غير مصرح لك بالوصول إلى هذه الصفحة")
        return
    
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "لوحة التحكم الإدارية"
        buttons = [
            "📢 البث والرسائل",
            "📊 الإحصائيات والتقارير",
            "🏦 إدارة الحسابات",
            "⚙️ الإعدادات",
            "🚪 خروج"
        ]
    else:
        title = "Admin Control Panel"
        buttons = [
            "📢 Broadcasting & Messages",
            "📊 Statistics & Reports",
            "🏦 Accounts Management",
            "⚙️ Settings",
            "🚪 Exit"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = []
    for i in range(0, len(buttons) - 1, 2):
        row = buttons[i:i+2]
        keyboard_row = []
        for btn in row:
            if btn == "📢 البث والرسائل" or btn == "📢 Broadcasting & Messages":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_broadcast_menu"))
            elif btn == "📊 الإحصائيات والتقارير" or btn == "📊 Statistics & Reports":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_stats"))
            elif btn == "🏦 إدارة الحسابات" or btn == "🏦 Accounts Management":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_menu"))
            elif btn == "⚙️ الإعدادات" or btn == "⚙️ Settings":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_settings"))
        keyboard.append(keyboard_row)
    
    keyboard.append([InlineKeyboardButton(buttons[-1], callback_data="admin_exit")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "البث والرسائل"
        buttons = [
            "📢 بث للجميع",
            "👥 بث للمسجلين",
            "✅ بث للمقبولين",
            "🔍 بث فردي",
            "🔙 رجوع"
        ]
    else:
        title = "Broadcasting & Messages"
        buttons = [
            "📢 Broadcast to All",
            "👥 To Registered",
            "✅ To Approved",
            "🔍 Individual Message",
            "🔙 Back"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = []
    for i in range(0, len(buttons) - 1, 2):
        row = buttons[i:i+2]
        keyboard_row = []
        for btn in row:
            if btn == "📢 بث للجميع" or btn == "📢 Broadcast to All":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_broadcast_all"))
            elif btn == "👥 بث للمسجلين" or btn == "👥 To Registered":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_broadcast_registered"))
            elif btn == "✅ بث للمقبولين" or btn == "✅ To Approved":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_broadcast_approved"))
            elif btn == "🔍 بث فردي" or btn == "🔍 Individual Message":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_individual_message"))
        keyboard.append(keyboard_row)
    
    keyboard.append([InlineKeyboardButton(buttons[-1], callback_data="admin_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def admin_accounts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "إدارة الحسابات"
        buttons = [
            "⏳ الحسابات قيد المراجعة",
            "✅ الحسابات المقبولة",
            "❌ الحسابات المرفوضة",
            "🔍 بحث عن حساب",
            "🔙 رجوع"
        ]
    else:
        title = "Accounts Management"
        buttons = [
            "⏳ Under Review",
            "✅ Approved",
            "❌ Rejected",
            "🔍 Search Account",
            "🔙 Back"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = []
    for i in range(0, len(buttons) - 1, 2):
        row = buttons[i:i+2]
        keyboard_row = []
        for btn in row:
            if btn == "⏳ الحسابات قيد المراجعة" or btn == "⏳ Under Review":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_under_review"))
            elif btn == "✅ الحسابات المقبولة" or btn == "✅ Approved":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_approved"))
            elif btn == "❌ الحسابات المرفوضة" or btn == "❌ Rejected":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_rejected"))
            elif btn == "🔍 بحث عن حساب" or btn == "🔍 Search Account":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_search"))
        keyboard.append(keyboard_row)
    
    keyboard.append([InlineKeyboardButton(buttons[-1], callback_data="admin_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "الإعدادات"
        buttons = [
            "🌐 تغيير اللغة",
            "🔄 تحديث الأداء",
            "🔄 إعادة تعيين التسلسل",
            "🔙 رجوع"
        ]
    else:
        title = "Settings"
        buttons = [
            "🌐 Change Language",
            "🔄 Update Performances",
            "🔄 Reset Sequences",
            "🔙 Back"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = [
        [InlineKeyboardButton(buttons[0], callback_data="admin_change_language")],
        [InlineKeyboardButton(buttons[1], callback_data="admin_update_performances")],
        [InlineKeyboardButton(buttons[2], callback_data="admin_reset_sequences")],
        [InlineKeyboardButton(buttons[3], callback_data="admin_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def admin_update_performances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await q.edit_message_text("❌ غير مصرح لك بتنفيذ هذا الإجراء")
        return
    
    admin_lang = get_admin_language(user_id)
    
    try:
        populate_account_performances()
        success_msg = "✅ تم تحديث جدول الأداء بنجاح!" if admin_lang == "ar" else "✅ Performances table updated successfully!"
    except Exception as e:
        logger.exception(f"Failed to update performances: {e}")
        success_msg = "❌ فشل في تحديث جدول الأداء." if admin_lang == "ar" else "❌ Failed to update performances table."
    
    await q.edit_message_text(success_msg)
    await asyncio.sleep(2)
    await admin_settings(update, context)

async def admin_reset_sequences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await q.edit_message_text("❌ غير مصرح لك بتنفيذ هذا الإجراء")
        return
    
    admin_lang = get_admin_language(user_id)
    
    try:
        reset_sequences()
        success_msg = "✅ تم إعادة تعيين التسلسل بنجاح!" if admin_lang == "ar" else "✅ Sequences reset successfully!"
    except Exception as e:
        logger.exception(f"Failed to reset sequences: {e}")
        success_msg = "❌ فشل في إعادة تعيين التسلسل." if admin_lang == "ar" else "❌ Failed to reset sequences."
    
    await q.edit_message_text(success_msg)
    await asyncio.sleep(2)
    await admin_settings(update, context)

async def admin_change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "تغيير اللغة"
        buttons = [
            "🇪🇬 العربية",
            "🇺🇸 English",
            "🔙 رجوع"
        ]
    else:
        title = "Change Language"
        buttons = [
            "🇺🇸 English",
            "🇪🇬 العربية",
            "🔙 Back"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = [
        [
            InlineKeyboardButton("🇪🇬 العربية" if admin_lang == "ar" else "🇪🇬 العربية", callback_data="admin_lang_ar"),
            InlineKeyboardButton("🇺🇸 English" if admin_lang == "ar" else "🇺🇸 English", callback_data="admin_lang_en")
        ],
        [InlineKeyboardButton(buttons[-1], callback_data="admin_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def admin_set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    lang = "ar" if q.data == "admin_lang_ar" else "en"
    user_id = q.from_user.id
    set_admin_language(user_id, lang)
    
    admin_lang = lang
    
    success_msg = "✅ تم تغيير اللغة بنجاح" if admin_lang == "ar" else "✅ Language changed successfully"
    await q.edit_message_text(success_msg)
    await asyncio.sleep(1)
    await admin_panel_from_callback(update, context)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    total_subscribers = len(get_all_subscribers())
    registered_users = len(get_registered_users())
    approved_users = len(get_approved_accounts_users())
    under_review = len(get_accounts_by_status("under_review"))
    active_accounts = len(get_accounts_by_status("active"))
    rejected_accounts = len(get_accounts_by_status("rejected"))

    if admin_lang == "ar":
        title = "الإحصائيات والتقارير"
        stats_text = f"""
📊 <b>إجمالي المشتركين:</b> {total_subscribers}
👥 <b>المسجلين:</b> {registered_users}
✅ <b>أصحاب الحسابات المقبولة:</b> {approved_users}

🏦 <b>الحسابات قيد المراجعة:</b> {under_review}
✅ <b>الحسابات النشطة:</b> {active_accounts}
❌ <b>الحسابات المرفوضة:</b> {rejected_accounts}
        """
        back_btn = "🔙 رجوع"
    else:
        title = "Statistics & Reports"
        stats_text = f"""
📊 <b>Total Subscribers:</b> {total_subscribers}
👥 <b>Registered Users:</b> {registered_users}
✅ <b>Approved Account Owners:</b> {approved_users}

🏦 <b>Accounts Under Review:</b> {under_review}
✅ <b>Active Accounts:</b> {active_accounts}
❌ <b>Rejected Accounts:</b> {rejected_accounts}
        """
        back_btn = "🔙 Back"
    
    header = build_header_html(title, [back_btn], header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = [[InlineKeyboardButton(back_btn, callback_data="admin_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header + stats_text, reply_markup=reply_markup, parse_mode="HTML")

def get_accounts_by_status(status: str) -> List[TradingAccount]:
    try:
        db = SessionLocal()
        accounts = db.query(TradingAccount).filter(TradingAccount.status == status).all()
        db.close()
        return accounts
    except Exception as e:
        logger.exception(f"Failed to get accounts by status: {e}")
        return []

async def admin_accounts_under_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    accounts = get_accounts_by_status("under_review")
    
    if admin_lang == "ar":
        title = "الحسابات قيد المراجعة"
        no_accounts = "لا توجد حسابات قيد المراجعة حالياً"
        back_btn = "🔙 رجوع"
    else:
        title = "Accounts Under Review"
        no_accounts = "No accounts under review currently"
        back_btn = "🔙 Back"
    
    header = build_header_html(title, [back_btn], header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    if not accounts:
        text = header + f"\n\n{no_accounts}"
    else:
        text = header + "\n\n"
        for acc in accounts:
            sub = acc.subscriber
            text += f"🏦 {acc.broker_name} - {acc.account_number}\n👤 {sub.name} ({sub.telegram_id})\n\n"
    
    keyboard = [[InlineKeyboardButton(back_btn, callback_data="admin_accounts_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
async def admin_individual_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        message = "📝 يرجى إرسال معرف المستخدم (telegram_id) ثم الرسالة"
    else:
        message = "📝 Please send user telegram_id then the message"
    
    context.user_data['awaiting_individual_message'] = True
    
    await q.edit_message_text(message)

async def admin_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    admin_lang = get_admin_language(user_id)
    
    msg = "✅ تم الخروج من لوحة الإدارة" if admin_lang == "ar" else "✅ Exited admin panel"
    
    await q.edit_message_text(msg)
    await show_main_sections(update, context, admin_lang)

async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    user_id = update.message.from_user.id
   
    if (user_id in ADMIN_TELEGRAM_IDS and 
        'awaiting_rejection_reason' in context.user_data):
        
        reason = update.message.text.strip()
        account_id = context.user_data['awaiting_rejection_reason']
        
        if not reason:
            admin_lang = get_admin_language(user_id)
            error_msg = "⚠️ يرجى إدخال سبب الرفض" if admin_lang == "ar" else "⚠️ Please enter a rejection reason"
            await update.message.reply_text(error_msg)
            return True
        
        context.user_data.pop('broadcast_type', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('target_users', None)
        context.user_data.pop('target_name', None)
        
        success = update_account_status(account_id, "rejected", reason=reason)
        admin_lang = get_admin_language(user_id)
        
        if success:
            
            user_lang = get_user_current_language(account_id)
            await notify_user_about_account_status(account_id, "rejected", reason=reason, user_lang=user_lang)
            
            messages_to_delete = []
            if 'admin_notification_message_id' in context.user_data:
                messages_to_delete.append(context.user_data.pop('admin_notification_message_id'))
            if 'rejection_prompt_message_id' in context.user_data:
                messages_to_delete.append(context.user_data.pop('rejection_prompt_message_id'))
            
            for message_id in messages_to_delete:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                except Exception as e:
                    logger.exception(f"Failed to delete message {message_id}: {e}")
            
            context.user_data.pop('awaiting_rejection_reason', None)
            
            try:
                await update.message.delete()
            except Exception as e:
                logger.exception(f"Failed to delete rejection reason message: {e}")
               
            await delete_all_notification_messages(account_id, context)
                
            success_msg = "✅ تم رفض الحساب وإرسال الإشعار للمستخدم" if admin_lang == "ar" else "✅ Account rejected and user notified"
            sent_msg = await update.message.reply_text(success_msg)
            
            async def delete_success_msg():
                await asyncio.sleep(0)
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                except Exception:
                    pass
            
            asyncio.create_task(delete_success_msg())
            
        else:
            
            error_msg = "❌ فشل في رفض الحساب" if admin_lang == "ar" else "❌ Failed to reject account"
            await update.message.reply_text(error_msg)
            
            context.user_data.pop('awaiting_rejection_reason', None)
            context.user_data.pop('admin_notification_message_id', None)
            context.user_data.pop('rejection_prompt_message_id', None)
        
        return True
    
    return False
    
            
    
def get_all_subscribers() -> List[Dict[str, Any]]:
    
    try:
        db = SessionLocal()
        subscribers = db.query(Subscriber).all()
        result = []
        for sub in subscribers:
            result.append({
                "telegram_id": sub.telegram_id,
                "name": sub.name,
                "lang": sub.lang
            })
        db.close()
        return result
    except Exception as e:
        logger.exception(f"Failed to get all subscribers: {e}")
        return []

def get_registered_users() -> List[Dict[str, Any]]:
   
    try:
        db = SessionLocal()
       
        subscribers = db.query(Subscriber).all()
        result = []
        for sub in subscribers:
            result.append({
                "telegram_id": sub.telegram_id,
                "name": sub.name,
                "lang": sub.lang
            })
        db.close()
        return result
    except Exception as e:
        logger.exception(f"Failed to get registered users: {e}")
        return []

def get_approved_accounts_users() -> List[Dict[str, Any]]:
   
    try:
        db = SessionLocal()
        approved_accounts = db.query(TradingAccount).filter(TradingAccount.status == "active").all()
        result = []
        processed_users = set()
        
        for account in approved_accounts:
            subscriber = account.subscriber
            if subscriber.telegram_id and subscriber.telegram_id not in processed_users:
                result.append({
                    "telegram_id": subscriber.telegram_id,
                    "name": subscriber.name,
                    "lang": subscriber.lang,
                    "account_number": account.account_number,
                    "broker_name": account.broker_name
                })
                processed_users.add(subscriber.telegram_id)
        
        db.close()
        return result
    except Exception as e:
        logger.exception(f"Failed to get approved accounts users: {e}")
        return []

async def handle_admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        return
    
    admin_lang = get_admin_language(user_id)
    context.user_data['broadcast_type'] = q.data
    
    if admin_lang == "ar":
        message = "📝 يرجى إرسال الرسالة التي تريد بثها:"
        cancel_btn = "❌ إلغاء"
    else:
        message = "📝 Please send the message you want to broadcast:"
        cancel_btn = "❌ Cancel"
    
    keyboard = [[InlineKeyboardButton(cancel_btn, callback_data="admin_cancel_broadcast")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(message, reply_markup=reply_markup)

async def process_admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    user_id = update.message.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        return
    
    if 'broadcast_type' not in context.user_data:
        return
    
    broadcast_type = context.user_data['broadcast_type']
    message_text = update.message.text
    admin_lang = get_admin_language(user_id)
    
    if broadcast_type == "admin_broadcast_all":
        target_users = get_all_subscribers()
        target_name = "جميع المشتركين" if admin_lang == "ar" else "All Subscribers"
    elif broadcast_type == "admin_broadcast_registered":
        target_users = get_registered_users()
        target_name = "المسجلين ببيانات" if admin_lang == "ar" else "Registered Users"
    elif broadcast_type == "admin_broadcast_approved":
        target_users = get_approved_accounts_users()
        target_name = "أصحاب الحسابات المقبولة" if admin_lang == "ar" else "Approved Accounts Owners"
    else:
        return
    
    if admin_lang == "ar":
        confirm_text = f"""
📊 تفاصيل البث:
🎯 المستهدف: {target_name}
👥 عدد المستخدمين: {len(target_users)}
📝 الرسالة:
{message_text}

هل تريد متابعة البث؟
        """
    else:
        confirm_text = f"""
📊 Broadcast Details:
🎯 Target: {target_name}
👥 Users Count: {len(target_users)}
📝 Message:
{message_text}

Do you want to proceed with broadcasting?
        """
    
    keyboard = [
        [
            InlineKeyboardButton("✅ نعم، إرسال" if admin_lang == "ar" else "✅ Yes, Send", 
                               callback_data="admin_confirm_broadcast"),
            InlineKeyboardButton("❌ إلغاء" if admin_lang == "ar" else "❌ Cancel", 
                               callback_data="admin_cancel_broadcast")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data['broadcast_message'] = message_text
    context.user_data['target_users'] = target_users
    context.user_data['target_name'] = target_name
    
    await update.message.reply_text(confirm_text, reply_markup=reply_markup)

async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        return
    
    if 'broadcast_message' not in context.user_data or 'target_users' not in context.user_data:
        return
    
    message_text = context.user_data['broadcast_message']
    target_users = context.user_data['target_users']
    target_name = context.user_data['target_name']
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        progress_msg = await q.message.reply_text(f"⏳ جاري إرسال الرسالة لـ {len(target_users)} مستخدم...")
    else:
        progress_msg = await q.message.reply_text(f"⏳ Sending message to {len(target_users)} users...")
    
    successful = 0
    failed = 0
    
    for user in target_users:
        try:
            await application.bot.send_message(
                chat_id=user['telegram_id'],
                text=message_text
            )
            successful += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user['telegram_id']}: {e}")
            failed += 1
        
        if (successful + failed) % 10 == 0:
            if admin_lang == "ar":
                await progress_msg.edit_text(f"⏳ جاري الإرسال... {successful + failed}/{len(target_users)}")
            else:
                await progress_msg.edit_text(f"⏳ Sending... {successful + failed}/{len(target_users)}")
    
    if admin_lang == "ar":
        report_text = f"""
✅ تقرير البث:
🎯 المستهدف: {target_name}
✅ تم الإرسال بنجاح: {successful}
❌ فشل في الإرسال: {failed}
📊 الإجمالي: {len(target_users)}
        """
    else:
        report_text = f"""
✅ Broadcast Report:
🎯 Target: {target_name}
✅ Successful: {successful}
❌ Failed: {failed}
📊 Total: {len(target_users)}
        """
    
    await progress_msg.edit_text(report_text)
    
    context.user_data.pop('broadcast_type', None)
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('target_users', None)
    context.user_data.pop('target_name', None)
    
    await admin_panel_from_callback(update, context)

async def admin_panel_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    q = update.callback_query
    user_id = q.from_user.id
    
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        title = "لوحة التحكم الإدارية"
        buttons = [
            "📢 البث والرسائل",
            "📊 الإحصائيات والتقارير",
            "🏦 إدارة الحسابات",
            "⚙️ الإعدادات",
            "🚪 خروج"
        ]
    else:
        title = "Admin Control Panel"
        buttons = [
            "📢 Broadcasting & Messages",
            "📊 Statistics & Reports",
            "🏦 Accounts Management",
            "⚙️ Settings",
            "🚪 Exit"
        ]
    
    header = build_header_html(title, buttons, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
    
    keyboard = []
    for i in range(0, len(buttons) - 1, 2):
        row = buttons[i:i+2]
        keyboard_row = []
        for btn in row:
            if btn == "📢 البث والرسائل" or btn == "📢 Broadcasting & Messages":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_broadcast_menu"))
            elif btn == "📊 الإحصائيات والتقارير" or btn == "📊 Statistics & Reports":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_stats"))
            elif btn == "🏦 إدارة الحسابات" or btn == "🏦 Accounts Management":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_accounts_menu"))
            elif btn == "⚙️ الإعدادات" or btn == "⚙️ Settings":
                keyboard_row.append(InlineKeyboardButton(btn, callback_data="admin_settings"))
        keyboard.append(keyboard_row)
    
    keyboard.append([InlineKeyboardButton(buttons[-1], callback_data="admin_exit")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML")

async def handle_admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id in ADMIN_TELEGRAM_IDS:
        set_admin_language(user_id, "ar")
    
    context.user_data.pop('broadcast_type', None)
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('target_users', None)
    context.user_data.pop('target_name', None)
    
    await show_main_sections(update, context, get_admin_language(user_id))

async def handle_admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    q = update.callback_query
    await q.answer()
    
    context.user_data.pop('broadcast_type', None)
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('target_users', None)
    context.user_data.pop('target_name', None)
    
    await admin_panel_from_callback(update, context)

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    user_id = update.effective_user.id
    if user_id in ADMIN_TELEGRAM_IDS:
        await admin_panel(update, context)
    else:
        await start(update, context)

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

def build_webapp_header(title: str, lang: str, labels: List[str] = None) -> str:
    if labels is None:
        labels = []
    
    return build_header_html(
        title,
        labels,
        header_emoji=HEADER_EMOJI,
        arabic_indent=1 if lang == "ar" else 0
    )

def get_agent_username(agent_name: str) -> str:
    
    if not agent_name:
        return "@Omarkin9"
    
    agents_list = os.getenv("AGENTS_LIST", "").split(",")
    agents_link = os.getenv("AGENTS_LINK", "").split(",")
    
    agents_list = [agent.strip() for agent in agents_list if agent.strip()]
    agents_link = [link.strip() for link in agents_link if link.strip()]
    
    if len(agents_list) == len(agents_link):
        for i, agent in enumerate(agents_list):
            if agent == agent_name and i < len(agents_link):
                return agents_link[i]
    
    return "@Omarkin9"

# -------------------------------
# consistent header builder
# -------------------------------
def build_header_html(
    title: str,
    keyboard_labels: List[str],
    header_emoji: str = HEADER_EMOJI,
    underline_enabled: bool = True,
    underline_char: str = "━",
    arabic_indent: int = 0,
) -> str:
    
    NBSP = "\u00A0"
    RLE = "\u202B"
    PDF = "\u202C"
    RLM = "\u200F"
    LLM = "\u200E"
    def _strip_directionals(s: str) -> str:
        return re.sub(r'[\u200E\u200F\u202A-\u202E\u2066-\u2069\u200D\u200C]', '', s)

    MIN_TITLE_WIDTH = 29
    clean_title = remove_emoji(title)
    title_len = display_width(clean_title)
    if title_len < MIN_TITLE_WIDTH:
        extra_spaces = MIN_TITLE_WIDTH - title_len
        left_pad = extra_spaces // 2
        right_pad = extra_spaces - left_pad
        title = f"{' ' * left_pad}{title}{' ' * right_pad}"

    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    if is_arabic:
        indent = NBSP * arabic_indent
        visible_title = f"{indent}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        visible_title = f"{header_emoji} {title} {header_emoji}"

    measure_title = _strip_directionals(visible_title)
    title_width = display_width(measure_title)
    
   
    if is_arabic:
        target_width = 29
    else:
        target_width = 29
    
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left
    centered_line = f"{NBSP * pad_left}<b>{visible_title}</b>{NBSP * pad_right}"
    underline_line = ""
    if underline_enabled:
        
        if is_arabic:
            underline_line = "\n" + RLM + (underline_char * target_width)
        else:
            underline_line = "\n" + (underline_char * target_width)

    return centered_line + underline_line
# -------------------------------
# DB helpers
# -------------------------------
def save_or_update_subscriber(name: str, email: str, phone: str, lang: str = "ar", telegram_id: int = None, telegram_username: str = None) -> Tuple[str, Subscriber]:
    
    try:
        db = SessionLocal()
        subscriber = None
        
        if telegram_id:
            subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == telegram_id).first()
            if subscriber:
                subscriber.name = name
                subscriber.email = email
                subscriber.phone = phone
                subscriber.telegram_username = telegram_username
                if lang:
                    subscriber.lang = lang
                db.commit()
                result = "updated"
            else:
                subscriber = Subscriber(
                    name=name,
                    email=email,
                    phone=phone,
                    telegram_username=telegram_username,
                    telegram_id=telegram_id,
                    lang=lang or "ar"
                )
                db.add(subscriber)
                db.commit()
                result = "created"
        else:
            subscriber = Subscriber(
                name=name,
                email=email,
                phone=phone,
                telegram_username=telegram_username,
                telegram_id=telegram_id,
                lang=lang or "ar"
            )
            db.add(subscriber)
            db.commit()
            result = "created"
        
        db.refresh(subscriber)
        db.close()
        return result, subscriber
        
    except Exception as e:
        logger.exception("Failed to save_or_update subscriber: %s", e)
        return "error", None

def save_trading_account(
    subscriber_id: int, 
    broker_name: str, 
    account_number: str, 
    password: str, 
    server: str,
    initial_balance: str = None,
    current_balance: str = None,
    withdrawals: str = None,
    copy_start_date: str = None,
    agent: str = None,
    expected_return: str = None
) -> Tuple[bool, TradingAccount]:
    
    try:
       
        required_fields = {
            'broker_name': broker_name,
            'account_number': account_number,
            'password': password,
            'server': server,
            'initial_balance': initial_balance,
            'current_balance': current_balance,
            'withdrawals': withdrawals,
            'copy_start_date': copy_start_date,
            'agent': agent,
            'expected_return': expected_return
        }
        
        for field_name, field_value in required_fields.items():
            if not field_value or str(field_value).strip() == "":
                logger.error(f"Missing required field: {field_name}")
                return False, None
        
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            logger.error(f"Subscriber with id {subscriber_id} not found")
            return False, None
        
        trading_account = TradingAccount(
            subscriber_id=subscriber_id,
            broker_name=broker_name,
            account_number=account_number,
            password=password,
            server=server,
            initial_balance=initial_balance,
            current_balance=current_balance,
            withdrawals=withdrawals,
            copy_start_date=copy_start_date,
            agent=agent,
            expected_return=expected_return,
            status="under_review"
        )
        
        db.add(trading_account)
        db.commit()
        db.refresh(trading_account)
        
        account_data = {
            "id": trading_account.id,
            "broker_name": broker_name,
            "account_number": account_number,
            "password": password,
            "server": server,
            "initial_balance": initial_balance,
            "current_balance": current_balance,
            "withdrawals": withdrawals,
            "copy_start_date": copy_start_date,
            "agent": agent,
            "expected_return": expected_return
        }
        
        subscriber_data = {
            "id": subscriber.id,
            "name": subscriber.name,
            "email": subscriber.email,
            "phone": subscriber.phone,
            "telegram_username": subscriber.telegram_username,
            "telegram_id": subscriber.telegram_id
        }
        
        db.close()
        
        import asyncio
        try:
            asyncio.create_task(send_admin_notification("new_account", account_data, subscriber_data))
        except Exception as e:
            logger.exception(f"Failed to send admin notification: {e}")
        
        return True, trading_account
        
    except Exception as e:
        logger.exception("Failed to save trading account: %s", e)
        return False, None

def update_trading_account(account_id: int, **kwargs) -> Tuple[bool, TradingAccount]:
    
    try:
        
        required_fields = [
            'broker_name', 'account_number', 'password', 'server',
            'initial_balance', 'current_balance', 'withdrawals',
            'copy_start_date', 'agent', 'expected_return'
        ]
        
        for field in required_fields:
            if field in kwargs and (not kwargs[field] or str(kwargs[field]).strip() == ""):
                logger.error(f"Missing required field in update: {field}")
                return False, None
        
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return False, None
        
        old_data = {
            "broker_name": account.broker_name,
            "account_number": account.account_number,
            "server": account.server
        }
        
        for key, value in kwargs.items():
            if hasattr(account, key) and value is not None:
                setattr(account, key, value)
        
        account.status = "under_review"
        account.rejection_reason = None 
        
        db.commit()
        db.refresh(account)
        subscriber = account.subscriber
        account_data = {
            "id": account.id,
            "broker_name": account.broker_name,
            "account_number": account.account_number,
            "password": account.password,
            "server": account.server,
            "initial_balance": account.initial_balance,
            "current_balance": account.current_balance,
            "withdrawals": account.withdrawals,
            "copy_start_date": account.copy_start_date,
            "agent": account.agent,
            "expected_return": account.expected_return,
            "old_data": old_data
        }
        
        subscriber_data = {
            "id": subscriber.id,
            "name": subscriber.name,
            "email": subscriber.email,
            "phone": subscriber.phone,
            "telegram_username": subscriber.telegram_username,
            "telegram_id": subscriber.telegram_id
        }
        
        db.close()
        
        import asyncio
        try:
            asyncio.create_task(send_admin_notification("updated_account", account_data, subscriber_data))
        except Exception as e:
            logger.exception(f"Failed to send admin notification: {e}")
        
        return True, account
    except Exception as e:
        logger.exception("Failed to update trading account: %s", e)
        return False, None

def delete_trading_account(account_id: int) -> bool:
    
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return False
        
       
        if account.status == "under_review":
            db.close()
            return False
        
        db.delete(account)
        db.commit()
        db.close()
        return True
    except Exception as e:
        logger.exception("Failed to delete trading account: %s", e)
        return False

def get_subscriber_by_telegram_id(tg_id: int) -> Optional[Subscriber]:
    
    try:
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == tg_id).first()
        db.close()
        return subscriber
    except Exception as e:
        logger.exception("DB lookup failed")
        return None

def get_trading_accounts_by_telegram_id(tg_id: int) -> List[TradingAccount]:
    
    try:
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == tg_id).first()
        if subscriber:
            accounts = subscriber.trading_accounts
            db.close()
            return accounts
        db.close()
        return []
    except Exception as e:
        logger.exception("Failed to get trading accounts")
        return []

def get_subscriber_with_accounts(tg_id: int) -> Optional[Dict[str, Any]]:
    
    try:
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == tg_id).first()
        if subscriber:
            result = {
                "id": subscriber.id,
                "name": subscriber.name,
                "email": subscriber.email,
                "phone": subscriber.phone,
                "telegram_username": subscriber.telegram_username,
                "telegram_id": subscriber.telegram_id,
                "lang": subscriber.lang,
                "trading_accounts": [
                    {
                        "id": acc.id,
                        "broker_name": acc.broker_name,
                        "account_number": acc.account_number,
                        "password": acc.password,
                        "server": acc.server,
                        "initial_balance": acc.initial_balance,
                        "current_balance": acc.current_balance,
                        "withdrawals": acc.withdrawals,
                        "copy_start_date": acc.copy_start_date,
                        "agent": acc.agent,
                        "expected_return": acc.expected_return,
                        "created_at": acc.created_at,
                        "status": acc.status,
                        "rejection_reason": acc.rejection_reason
                    }
                    for acc in subscriber.trading_accounts
                ]
            }
            db.close()
            return result
        db.close()
        return None
    except Exception as e:
        logger.exception("Failed to get subscriber with accounts")
        return None
        
# -------------------------------
# helpers for form-message references
# -------------------------------
def save_form_ref(tg_id: int, chat_id: int, message_id: int, origin: str = "", lang: str = "ar"):
    try:
        FORM_MESSAGES[int(tg_id)] = {"chat_id": int(chat_id), "message_id": int(message_id), "origin": origin, "lang": lang}
    except Exception:
        logger.exception("Failed to save form ref")

def get_form_ref(tg_id: int) -> Optional[Dict[str, Any]]:
    return FORM_MESSAGES.get(int(tg_id))

def clear_form_ref(tg_id: int):
    try:
        FORM_MESSAGES.pop(int(tg_id), None)
    except Exception:
        logger.exception("Failed to clear form ref")

# -------------------------------
# validation regex
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# -------------------------------
# Admin and notification functions
# -------------------------------
def get_user_current_language(account_id: int) -> str:
    
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return "ar"
        
        subscriber = account.subscriber
        telegram_id = subscriber.telegram_id
        
        form_ref = get_form_ref(telegram_id)
        if form_ref and form_ref.get("lang"):
            db.close()
            return form_ref["lang"]
        
        db.close()
        return subscriber.lang or "ar"
    except Exception as e:
        logger.exception(f"Failed to get user current language: {e}")
        return "ar"

def update_account_status(account_id: int, status: str, reason: str = None) -> bool:
    
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return False
        
        account.status = status
        if status == "rejected":
            account.rejection_reason = reason
        else:
            account.rejection_reason = None
        
        db.commit()
        db.close()
        return True
    except Exception as e:
        logger.exception(f"Failed to update account status: {e}")
        return False

async def handle_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if not q.data:
        return
    
    user_id = q.from_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await q.message.reply_text("❌ غير مصرح لك بتنفيذ هذا الإجراء")
        return
    
    admin_lang = get_admin_language(user_id)
    
    if q.data.startswith("activate_account_"):
        account_id = int(q.data.split("_")[2])
        success = update_account_status(account_id, "active")
        if success:
            user_lang = get_user_current_language(account_id)
            await notify_user_about_account_status(account_id, "active", user_lang=user_lang)
            
            try:
                await q.message.delete()
            except Exception as e:
                logger.exception(f"Failed to delete admin message: {e}")
            
            await delete_all_notification_messages(account_id, context)
            
            success_msg = "✅ تم تفعيل الحساب وإرسال الإشعار للمستخدم" if admin_lang == "ar" else "✅ Account activated and user notified"
            sent_msg = await context.bot.send_message(chat_id=user_id, text=success_msg)
            
            async def delete_success_msg():
                await asyncio.sleep(0)
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                except Exception:
                    pass
            
            asyncio.create_task(delete_success_msg())
            
        else:
            try:
                await q.message.delete()
            except Exception as e:
                logger.exception(f"Failed to delete admin message on failure: {e}")
    
    elif q.data.startswith("reject_account_"):
        account_id = int(q.data.split("_")[2])
        context.user_data.pop('broadcast_type', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('target_users', None)
        context.user_data.pop('target_name', None)
        context.user_data['awaiting_rejection_reason'] = account_id
        context.user_data['admin_notification_message_id'] = q.message.message_id
        prompt_text = "يرجى إرسال سبب الرفض:" if admin_lang == "ar" else "Please provide the rejection reason:"
        rejection_prompt = await q.message.reply_text(prompt_text)
        context.user_data['rejection_prompt_message_id'] = rejection_prompt.message_id
            
            
async def delete_all_notification_messages(account_id: int, context: ContextTypes.DEFAULT_TYPE):
    if account_id in NOTIFICATION_MESSAGES:
        for msg_info in NOTIFICATION_MESSAGES[account_id]:
            try:
                await context.bot.delete_message(
                    chat_id=msg_info['chat_id'],
                    message_id=msg_info['message_id']
                )
            except Exception as e:
                logger.exception(f"Failed to delete notification message for admin {msg_info['admin_id']}: {e}")
        
        del NOTIFICATION_MESSAGES[account_id]
        
async def handle_notification_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    q = update.callback_query
    await q.answer()
    
    try:
        
        await q.message.delete()
    except Exception as e:
        logger.exception(f"Failed to delete notification message: {e}")

async def notify_user_about_account_status(account_id: int, status: str, reason: str = None, user_lang: str = None):
    
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return
        
        subscriber = account.subscriber
        telegram_id = subscriber.telegram_id
        
        lang = user_lang or subscriber.lang or "ar"
        
        if status == "active":
            if lang == "ar":
                title = "مــــــبــــــــــــارك"
                labels = ["✅ حسناً"]
                header = build_header_html(title, labels, header_emoji="🎉", arabic_indent=1)
                message = f"""
{header}
✅ تم ربط الحساب بخدمة النسخ

🏦 الوسيط: {account.broker_name}
🔢 رقم الحساب: {account.account_number}
🖥️ السيرفر: {account.server}

نتمنى لك التوفيق.
وشكراً علي اختيارك لنظام YesFX!
                """
            else:
                title = "Congratulations"
                labels = ["✅ OK"]
                header = build_header_html(title, labels, header_emoji="🎉", arabic_indent=0)
                message = f"""
{header}
✅ Your account is linked to the copy service️

🏦 Broker: {account.broker_name}
🔢 Account Number: {account.account_number}
🖥️ Server: {account.server}

Wishing you success.
Thanks for choosing YesFX!
                """
        else:
            
            agent_username = get_agent_username(account.agent)
            
            if lang == "ar":
                title = "لم يتم تفعيل الحساب"
                labels = ["✅ حسناً"]
                header = build_header_html(title, labels, header_emoji="❗️",  arabic_indent=1)
                reason_text = f"\n📝 السبب: {reason}" if reason else ""
                message = f"""
{header}
❌ لم يتم تفعيل حسابك{reason_text}

🏦 الوسيط: {account.broker_name}
🔢 رقم الحساب: {account.account_number}

يرجى مراجعة البيانات المقدمة
أو التواصل مع {agent_username}.
                """
            else:
                title = "Account Not Activated"
                labels = ["✅ OK"]
                header = build_header_html(title, labels, header_emoji="❗️", arabic_indent=0)
                reason_text = f"\n📝 Reason: {reason}" if reason else ""
                message = f"""
{header}
Your account was not activated ❌{reason_text}

🏦 Broker: {account.broker_name}
🔢 Account Number: {account.account_number}

Please review the submitted data
or contact {agent_username}.
                """

        keyboard = [
            [InlineKeyboardButton("✅ حسناً" if lang == "ar" else "✅ OK", 
                                callback_data=f"confirm_notification_{account_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await application.bot.send_message(
            chat_id=telegram_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        
        db.close()

        await update_user_interface_after_status_change(telegram_id, lang)
        
    except Exception as e:
        logger.exception(f"Failed to notify user about account status: {e}")

async def update_user_interface_after_status_change(telegram_id: int, lang: str):
    
    ref = get_form_ref(telegram_id)
    if ref and ref.get("origin") == "my_accounts":
        updated_data = get_subscriber_with_accounts(telegram_id)
        if updated_data:
            await refresh_user_accounts_interface(telegram_id, lang, ref["chat_id"], ref["message_id"])

async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    if await handle_rejection_reason(update, context):
        return
    
    if 'broadcast_type' in context.user_data and 'broadcast_message' not in context.user_data:
        await process_admin_broadcast(update, context)
        return
    
    user_id = update.message.from_user.id
    admin_lang = get_admin_language(user_id)
    
    if admin_lang == "ar":
        help_text = """
🎯 **أدوات المسؤول المتاحة:**

• استخدام /admin للوحة التحكم
• البث للمستخدمين عبر لوحة التحكم
• تفعيل/رفض الحسابات من خلال الإشعارات

💡 **للبث:** استخدم /admin ثم اختر نوع البث
💡 **لإدارة الحسابات:** اضغط على أزرار التفعيل/الرفض في الإشعارات
        """
    else:
        help_text = """
🎯 **Available Admin Tools:**

• Use /admin for control panel
• Broadcast to users via control panel  
• Activate/reject accounts through notifications

💡 **For broadcasting:** Use /admin then choose broadcast type
💡 **For account management:** Click activate/reject buttons in notifications
        """
    
    try:
        await update.message.reply_text(help_text, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Failed to send admin help message: {e}")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_TELEGRAM_IDS:
        return
    
    lang = "ar"
    subscriber = get_subscriber_by_telegram_id(user_id)
    if subscriber and subscriber.lang:
        lang = subscriber.lang
    else:
        lang = context.user_data.get("lang", "ar")
    
    if lang == "ar":
        response_text = "⚠️ يمكنك استخدام الأزرار في القائمة للتفاعل مع البوت"
    else:
        response_text = "⚠️ Please use the buttons in the menu to interact with the bot"
    
    try:
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.exception(f"Failed to send help message to user: {e}")

async def send_admin_notification(action_type: str, account_data: dict, subscriber_data: dict):
    
    try:
        logger.info(f"🔔 Starting admin notification for {action_type}")
        
        if not ADMIN_TELEGRAM_IDS:
            logger.warning("⚠️ ADMIN_TELEGRAM_IDS not set - admin notifications disabled")
            return
        
        account_id = account_data['id']
        if account_id not in NOTIFICATION_MESSAGES:
            NOTIFICATION_MESSAGES[account_id] = []
        
        for admin_id in ADMIN_TELEGRAM_IDS:
            try:
                logger.info(f"📤 Sending notification to admin {admin_id}")
                
                admin_lang = get_admin_language(admin_id)
                
                if action_type == "new_account":
                    if admin_lang == "ar":
                        title = "🆕 حساب تداول جديد"
                        action_desc = "تم إضافة حساب تداول جديد"
                    else:
                        title = "🆕 New Trading Account"
                        action_desc = "New trading account added"
                elif action_type == "updated_account":
                    if admin_lang == "ar":
                        title = "✏️ تعديل على حساب تداول"
                        action_desc = "تم تعديل حساب تداول"
                    else:
                        title = "✏️ Trading Account Updated"
                        action_desc = "Trading account updated"
                else:
                    if admin_lang == "ar":
                        title = "ℹ️ نشاط على حساب تداول"
                        action_desc = "نشاط على حساب تداول"
                    else:
                        title = "ℹ️ Trading Account Activity"
                        action_desc = "Trading account activity"
                
                labels = ["👤 المستخدم", "🏦 الوسيط", "✅ تفعيل الحساب", "❌ رفض الحساب"] if admin_lang == "ar" else ["👤 User", "🏦 Broker", "✅ Activate Account", "❌ Reject Account"]
                header = build_header_html(title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1 if admin_lang == "ar" else 0)
                
                if admin_lang == "ar":
                    message = f"""
{header}
<b>👤 المستخدم:</b> {subscriber_data['name']}
<b>📧 البريد:</b> {subscriber_data['email']}
<b>📞 الهاتف:</b> {subscriber_data['phone']}
<b>🌐 تيليجرام:</b> @{subscriber_data.get('telegram_username', 'N/A')} ({subscriber_data['telegram_id']})

<b>🏦 الوسيط:</b> {account_data['broker_name']}
<b>🔢 رقم الحساب:</b> {account_data['account_number']}
<b>🔐 كلمة المرور:</b> {account_data.get('password', 'N/A')}
<b>🖥️ السيرفر:</b> {account_data['server']}
<b>📈 العائد المتوقع:</b> {account_data.get('expected_return', 'N/A')}
<b>👤 الوكيل:</b> {account_data.get('agent', 'N/A')}

<b>💰 رصيد البداية:</b> {account_data.get('initial_balance', 'N/A')}
<b>💳 الرصيد الحالي:</b> {account_data.get('current_balance', 'N/A')}  
<b>💸 المسحوبات:</b> {account_data.get('withdrawals', 'N/A')}
<b>📅 تاريخ البدء:</b> {account_data.get('copy_start_date', 'N/A')}

<b>🌐 معرف الحساب:</b> {account_data['id']}
                    """
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("✅ تفعيل الحساب", callback_data=f"activate_account_{account_data['id']}"),
                            InlineKeyboardButton("❌ رفض الحساب", callback_data=f"reject_account_{account_data['id']}")
                        ]
                    ]
                else:
                    message = f"""
{header}
<b>👤 User:</b> {subscriber_data['name']}
<b>📧 Email:</b> {subscriber_data['email']}
<b>📞 Phone:</b> {subscriber_data['phone']}
<b>🌐 Telegram:</b> @{subscriber_data.get('telegram_username', 'N/A')} ({subscriber_data['telegram_id']})

<b>🏦 Broker:</b> {account_data['broker_name']}
<b>🔢 Account Number:</b> {account_data['account_number']}
<b>🔐 Password:</b> {account_data.get('password', 'N/A')}
<b>🖥️ Server:</b> {account_data['server']}
<b>📈 Expected Return:</b> {account_data.get('expected_return', 'N/A')}
<b>👤 Agent:</b> {account_data.get('agent', 'N/A')}

<b>💰 Initial Balance:</b> {account_data.get('initial_balance', 'N/A')}
<b>💳 Current Balance:</b> {account_data.get('current_balance', 'N/A')}  
<b>💸 Withdrawals:</b> {account_data.get('withdrawals', 'N/A')}
<b>📅 Start Date:</b> {account_data.get('copy_start_date', 'N/A')}

<b>🌐 Account ID:</b> {account_data['id']}
                    """
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("✅ Activate Account", callback_data=f"activate_account_{account_data['id']}"),
                            InlineKeyboardButton("❌ Reject Account", callback_data=f"reject_account_{account_data['id']}")
                        ]
                    ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                sent_message = await application.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                
                NOTIFICATION_MESSAGES[account_id].append({
                    'admin_id': admin_id,
                    'chat_id': admin_id,
                    'message_id': sent_message.message_id
                })
                
                logger.info(f"✅ Admin notification sent successfully to {admin_id}")
                
            except Exception as e:
                logger.exception(f"❌ Failed to send admin notification to {admin_id}: {e}")
                
        
        logger.info("✅ All admin notifications processed")
        
    except Exception as e:
        logger.exception(f"❌ Failed to send admin notifications: {e}")

def get_account_status_text(status: str, lang: str, reason: str = None) -> str:
    
    if lang == "ar":
        status_texts = {
            "under_review": "⏳ قيد المراجعة",
            "active": "✅ مفعل",
            "rejected": "❌ مرفوض"
        }
        reason_text = f" بسبب: {reason}" if reason else ""
    else:
        status_texts = {
            "under_review": "⏳ Under Review", 
            "active": "✅ Active",
            "rejected": "❌ Rejected"
        }
        reason_text = f" due to: {reason}" if reason else ""
    
    text = status_texts.get(status, status)
    if status == "rejected" and reason:
        text += reason_text
    return text

# ===============================
# /start + menu / language flows
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
    user_id = update.effective_user.id if update.effective_user else None
    
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
        q = update.callback_query
        await q.answer()
        try:
            await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    else:
        if update.message:
            await update.message.reply_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

async def show_main_sections(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if not update.callback_query:
        return
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if user_id in ADMIN_TELEGRAM_IDS:
        set_admin_language(user_id, lang)
    
    if lang == "ar":
       #sections = [("💹 تداول الفوركس", "forex_main"), ("💻 خدمات البرمجة", "dev_main"), ("🤝 طلب وكالة YesFX", "agency_main")]
        sections = [("💹 تداول الفوركس", "forex_main"), ("💻 خدمات البرمجة", "dev_main")]
        title = "الأقسام الرئيسية"
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
       #sections = [("💹 Forex Trading", "forex_main"), ("💻 Programming Services", "dev_main"), ("🤝 YesFX Partnership", "agency_main")]
        sections = [("💹 Forex Trading", "forex_main"), ("💻 Programming Services", "dev_main")]
        title = "Main Sections"
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=cb)] for name, cb in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)
    labels = [name for name, _ in sections] + [back_button[0]]
    header = build_header_html(title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1 if lang == "ar" else 0)
    try:
        await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = "ar" if q.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    user_id = q.from_user.id
    if user_id in ADMIN_TELEGRAM_IDS:
        set_admin_language(user_id, lang)

    subscriber = get_subscriber_by_telegram_id(user_id)
    if subscriber:
        
        await show_main_sections(update, context, lang)
    else:
       
        if lang == "ar":
            title = "من فضلك ادخل البيانات"
            back_label_text = "🔙 الرجوع للغة"
            open_label = "📝 افتح نموذج التسجيل"
            header_emoji_for_lang = HEADER_EMOJI
        else:
            title = "Please enter your data"
            back_label_text = "🔙 Back to language"
            open_label = "📝 Open registration form"
            header_emoji_for_lang = "✨"

        labels = [open_label, back_label_text]
        header = build_header_html(title, labels, header_emoji=header_emoji_for_lang, arabic_indent=1 if lang == "ar" else 0)

        keyboard = []
        if WEBAPP_URL:
            url_with_lang = f"{WEBAPP_URL}?lang={lang}"
            keyboard.append([InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))])
        else:
            fallback_text = "فتح النموذج" if lang == "ar" else "Open form"
            keyboard.append([InlineKeyboardButton(fallback_text, callback_data="fallback_open_form")])

        keyboard.append([InlineKeyboardButton(back_label_text, callback_data="back_language")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="initial_registration", lang=lang)
        except Exception:
            try:
                sent = await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                save_form_ref(user_id, sent.chat_id, sent.message_id, origin="initial_registration", lang=lang)
            except Exception:
                logger.exception("Failed to show initial registration form.")

# ===============================
# WebApp pages
# ===============================
@app.get("/webapp")
def webapp_form(request: Request):
    lang = (request.query_params.get("lang") or "ar").lower()
    is_ar = lang == "ar"
    edit_mode = request.query_params.get("edit") == "1"
    pre_name = request.query_params.get("name") or ""
    pre_email = request.query_params.get("email") or ""
    pre_phone = request.query_params.get("phone") or ""

    page_title = "🧾 من فضلك أكمل بياناتك" if is_ar else "🧾 Please complete your data"
    name_label = "الاسم" if is_ar else "Full name"
    email_label = "البريد الإلكتروني" if is_ar else "Email"
    phone_label = "رقم الهاتف (مع رمز الدولة)" if is_ar else "Phone (with country code)"
    submit_label = "إرسال" if is_ar else "Submit"
    close_label = "إغلاق" if is_ar else "Close"
    invalid_conn = "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server"

    dir_attr = "rtl" if is_ar else "ltr"
    text_align = "right" if is_ar else "left"
    input_dir = "rtl" if is_ar else "ltr"

    name_value = f'value="{pre_name}"' if pre_name else ""
    email_value = f'value="{pre_email}"' if pre_email else ""
    phone_value = f'value="{pre_phone}"' if pre_phone else ""

    html = f"""
    <!doctype html>
    <html lang="{ 'ar' if is_ar else 'en' }" dir="{dir_attr}">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>Registration Form</title>
      <style>
        body{{font-family: Arial, Helvetica, sans-serif; padding:16px; background:#f7f7f7; direction:{dir_attr};}}
        .card{{max-width:600px;margin:24px auto;padding:16px;border-radius:10px;background:white; box-shadow:0 4px 12px rgba(0,0,0,0.08)}}
        label{{display:block;margin-top:12px;font-weight:600;text-align:{text_align}}}
        input{{width:100%;padding:10px;margin-top:6px;border:1px solid #ddd;border-radius:6px;font-size:16px;direction:{input_dir}}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .small{{font-size:13px;color:#666;margin-top:6px;text-align:{text_align}}}
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="text-align:{text_align}">{page_title}</h2>
        <label style="text-align:{text_align}">{name_label}</label>
        <input id="name" placeholder="{ 'مثال: أحمد علي' if is_ar else 'e.g. Ahmed Ali' }" {name_value} />
        <label style="text-align:{text_align}">{email_label}</label>
        <input id="email" type="email" placeholder="you@example.com" {email_value} />
        <label style="text-align:{text_align}">{phone_label}</label>
        <input id="phone" placeholder="+20123 456 7890" {phone_value} />
        <div class="small">{ 'البيانات تُرسل مباشرة للبوت بعد الضغط على إرسال.' if is_ar else 'Data will be sent to the bot.' }</div>
        <div style="margin-top:12px;text-align:{text_align};">
          <button class="btn btn-primary" id="submit">{submit_label}</button>
          <button class="btn btn-ghost" id="close">{close_label}</button>
        </div>
        <div id="status" class="small" style="margin-top:10px;color:#b00;text-align:{text_align}"></div>
      </div>

      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script>
        const tg = window.Telegram.WebApp || {{}} ;
        try {{ tg.expand(); }} catch(e){{ /* ignore */ }}
        const statusEl = document.getElementById('status');

        function validateEmail(email) {{
          const re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
          return re.test(String(email).toLowerCase());
        }}
        function validatePhone(phone) {{
          const re = /^[+0-9\\-\\s]{{6,20}}$/;
          return re.test(String(phone));
        }}

        const urlParams = new URLSearchParams(window.location.search);
        const pageLang = (urlParams.get('lang') || '{ "ar" if is_ar else "en" }').toLowerCase();

        async function submitForm() {{
          const name = document.getElementById('name').value.trim();
          const email = document.getElementById('email').value.trim();
          const phone = document.getElementById('phone').value.trim();

          if (!name || name.length < 2) {{
            statusEl.textContent = '{ "الاسم قصير جدًا / Name is too short" if is_ar else "Name is too short" }';
            return;
          }}
          if (!validateEmail(email)) {{
            statusEl.textContent = '{ "بريد إلكتروني غير صالح / Invalid email" if is_ar else "Invalid email" }';
            return;
          }}
          if (!validatePhone(phone)) {{
            statusEl.textContent = '{ "رقم هاتف غير صالح / Invalid phone" if is_ar else "Invalid phone" }';
            return;
          }}

          const initUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user : null;

          const payload = {{
            name,
            email,
            phone,
            tg_user: initUser,
            lang: pageLang
          }};

          try {{
            const resp = await fetch(window.location.origin + '/webapp/submit', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(payload)
            }});
            const data = await resp.json();
            if (resp.ok) {{
              statusEl.style.color = 'green';
              statusEl.textContent = data.message || '{ "تم الإرسال. سيتم إغلاق النافذة..." if is_ar else "Sent — window will close..." }';
              try {{ setTimeout(()=>tg.close(), 700); }} catch(e){{ /* ignore */ }}
              try {{ tg.sendData(JSON.stringify({{ status: 'sent', lang: pageLang }})); }} catch(e){{}}
            }} else {{
              statusEl.textContent = data.error || '{invalid_conn}';
            }}
          }} catch (e) {{
            statusEl.textContent = '{invalid_conn}: ' + e.message;
          }}
        }}

        document.getElementById('submit').addEventListener('click', submitForm);
        document.getElementById('close').addEventListener('click', () => {{ try{{ tg.close(); }}catch(e){{}} }});
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.get("/webapp/existing-account")
def webapp_existing_account(request: Request):
    lang = (request.query_params.get("lang") or "ar").lower()
    is_ar = lang == "ar"

    page_title = "🧾 تسجيل بيانات حساب التداول" if is_ar else "🧾 Register Trading Account"
    labels = {
        "broker": "اسم الشركة" if is_ar else "Broker Name",
        "account": "رقم الحساب" if is_ar else "Account Number",
        "password": "كلمة السر" if is_ar else "Password",
        "server": "سيرفر التداول" if is_ar else "Trading Server",
        "initial_balance": "رصيد البداية" if is_ar else "Initial Balance",
        "current_balance": "الرصيد الحالي" if is_ar else "Current Balance",
        "withdrawals": "المسحوبات" if is_ar else "Withdrawals",
        "copy_start_date": "تاريخ بدء النسخ" if is_ar else "Copy Start Date",
        "agent": "الوكيل" if is_ar else "Agent",
        "expected_return": "العائد المتوقع" if is_ar else "Expected Return",
        "submit": "تسجيل" if is_ar else "Submit",
        "close": "إغلاق" if is_ar else "Close",
        "error": "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server",
        "required_field": "هذا الحقل مطلوب" if is_ar else "This field is required",
        "risk_warning": "⚠️ تنبيه: كلما ارتفع العائد المتوقع زادت المخاطر" if is_ar else "⚠️ Warning: Higher expected returns come with higher risks"
    }
    dir_attr = "rtl" if is_ar else "ltr"
    text_align = "right" if is_ar else "left"
    agents_options = "".join([f'<option value="{agent}">{agent}</option>' for agent in AGENTS_LIST])
    expected_return_options = ""
    if is_ar:
        expected_return_options = """
            <option value="">اختر العائد المتوقع</option>
            <option value="X1 = 10% - 15%">X1 = 10% - 15%</option>
            <option value="X2 = 20% - 30%">X2 = 20% - 30%</option>
            <option value="X3 = 30% - 45%">X3 = 30% - 45%</option>
            <option value="X4 = 40% - 60%">X4 = 40% - 60%</option>
        """
    else:
        expected_return_options = """
            <option value="">Select Expected Return</option>
            <option value="X1 = 10% - 15%">X1 = 10% - 15%</option>
            <option value="X2 = 20% - 30%">X2 = 20% - 30%</option>
            <option value="X3 = 30% - 45%">X3 = 30% - 45%</option>
            <option value="X4 = 40% - 60%">X4 = 40% - 60%</option>
        """

    form_labels = [
        labels['broker'],
        labels['account'],
        labels['password'], 
        labels['server'],
        labels['initial_balance'],
        labels['current_balance'],
        labels['withdrawals'],
        labels['copy_start_date'],
        labels['agent'],
        labels['expected_return']
    ]
    header_html = build_header_html(page_title, form_labels, header_emoji=HEADER_EMOJI, underline_enabled=False,arabic_indent=1 if lang == "ar" else 0)

    html = f"""
    <!doctype html>
    <html lang="{ 'ar' if is_ar else 'en' }" dir="{dir_attr}">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>{page_title}</title>
      <style>
        body{{font-family:Arial;padding:16px;background:#f7f7f7;direction:{dir_attr};}}
        .card{{max-width:600px;margin:24px auto;padding:16px;border-radius:10px;background:white;box-shadow:0 4px 12px rgba(0,0,0,0.1)}}
        label{{display:block;margin-top:10px;font-weight:600;text-align:{text_align}}}
        input, select{{width:100%;padding:10px;margin-top:6px;border:1px solid #ccc;border-radius:6px;font-size:16px;}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .small{{font-size:13px;color:#666;text-align:{text_align}}}
        .form-row{{display:flex;gap:10px;margin-top:10px;}}
        .form-row > div{{flex:1;}}
        .risk-warning{{font-size:12px;color:#ff6b35;margin-top:4px;text-align:{text_align};font-weight:500;}}
        .header-container{{text-align:{text_align}; margin-bottom:20px;}}
        .required{{color:#ff4444;}}
        .field-error{{color:#ff4444;font-size:12px;margin-top:2px;display:none;}}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header-container">
          {header_html}
        </div>
        
        <label>{labels['broker']} <span class="required">*</span></label>
        <select id="broker" required>
          <option value="">{ 'اختر الشركة' if is_ar else 'Select Broker' }</option>
          <option value="Oneroyal">Oneroyal</option>
          <option value="Scope">Scope</option>
        </select>
        <div id="broker_error" class="field-error">{labels['required_field']}</div>

        <div class="form-row">
          <div>
            <label>{labels['account']} <span class="required">*</span></label>
            <input id="account" placeholder="123456" required />
            <div id="account_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['password']} <span class="required">*</span></label>
            <input id="password" type="password" placeholder="••••••••" required />
            <div id="password_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <label>{labels['server']} <span class="required">*</span></label>
        <input id="server" placeholder="Oneroyal-Live" required />
        <div id="server_error" class="field-error">{labels['required_field']}</div>

        <div class="form-row">
          <div>
            <label>{labels['initial_balance']} <span class="required">*</span></label>
            <input id="initial_balance" type="number" placeholder="0.00" step="0.01" required />
            <div id="initial_balance_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['current_balance']} <span class="required">*</span></label>
            <input id="current_balance" type="number" placeholder="0.00" step="0.01" required />
            <div id="current_balance_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <div class="form-row">
          <div>
            <label>{labels['withdrawals']} <span class="required">*</span></label>
            <input id="withdrawals" type="number" placeholder="0.00" step="0.01" required />
            <div id="withdrawals_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['copy_start_date']} <span class="required">*</span></label>
            <input id="copy_start_date" type="date" required />
            <div id="copy_start_date_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <label>{labels['agent']} <span class="required">*</span></label>
        <select id="agent" required>
          <option value="">{ 'اختر الوكيل' if is_ar else 'Select Agent' }</option>
          {agents_options}
        </select>
        <div id="agent_error" class="field-error">{labels['required_field']}</div>

        <label>{labels['expected_return']} <span class="required">*</span></label>
        <select id="expected_return" required>
          {expected_return_options}
        </select>
        <div id="expected_return_error" class="field-error">{labels['required_field']}</div>
        <div class="risk-warning">{labels['risk_warning']}</div>

        <div style="margin-top:12px;text-align:{text_align}">
          <button class="btn btn-primary" id="submit">{labels['submit']}</button>
          <button class="btn btn-ghost" id="close">{labels['close']}</button>
        </div>
        <div id="status" class="small" style="margin-top:10px;color:#b00;"></div>
      </div>

      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script>
        const tg = window.Telegram.WebApp || {{}};
        try{{tg.expand();}}catch(e){{}}
        const statusEl = document.getElementById('status');

        // دالة للتحقق من الحقول المطلوبة
        function validateForm() {{
          const fields = [
            {{id: 'broker', name: '{labels['broker']}'}},
            {{id: 'account', name: '{labels['account']}'}},
            {{id: 'password', name: '{labels['password']}'}},
            {{id: 'server', name: '{labels['server']}'}},
            {{id: 'initial_balance', name: '{labels['initial_balance']}'}},
            {{id: 'current_balance', name: '{labels['current_balance']}'}},
            {{id: 'withdrawals', name: '{labels['withdrawals']}'}},
            {{id: 'copy_start_date', name: '{labels['copy_start_date']}'}},
            {{id: 'agent', name: '{labels['agent']}'}},
            {{id: 'expected_return', name: '{labels['expected_return']}'}}
          ];

          let isValid = true;
          
          // إخفاء جميع رسائل الخطأ أولاً
          fields.forEach(field => {{
            const errorEl = document.getElementById(field.id + '_error');
            if (errorEl) errorEl.style.display = 'none';
          }});

          // التحقق من كل حقل
          fields.forEach(field => {{
            const inputEl = document.getElementById(field.id);
            const value = inputEl.value.trim();
            
            if (!value) {{
              const errorEl = document.getElementById(field.id + '_error');
              if (errorEl) {{
                errorEl.style.display = 'block';
                errorEl.textContent = '{labels['required_field']}';
              }}
              isValid = false;
              
              // إضافة تأثير للخطأ
              inputEl.style.borderColor = '#ff4444';
            }} else {{
              inputEl.style.borderColor = '#ccc';
            }}
          }});

          return isValid;
        }}

        async function submitForm(){{
          // التحقق من جميع الحقول أولاً
          if (!validateForm()) {{
            statusEl.textContent = '{ "يرجى ملء جميع الحقول المطلوبة" if is_ar else "Please fill all required fields" }';
            statusEl.style.color = '#ff4444';
            return;
          }}

          const broker = document.getElementById('broker').value.trim();
          const account = document.getElementById('account').value.trim();
          const password = document.getElementById('password').value.trim();
          const server = document.getElementById('server').value.trim();
          const initial_balance = document.getElementById('initial_balance').value.trim();
          const current_balance = document.getElementById('current_balance').value.trim();
          const withdrawals = document.getElementById('withdrawals').value.trim();
          const copy_start_date = document.getElementById('copy_start_date').value.trim();
          const agent = document.getElementById('agent').value.trim();
          const expected_return = document.getElementById('expected_return').value.trim();

          const initUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user : null;
          const payload = {{
            broker,
            account,
            password,
            server,
            initial_balance,
            current_balance,
            withdrawals,
            copy_start_date,
            agent,
            expected_return,
            tg_user: initUser,
            lang:"{lang}"
          }};

          try{{
            statusEl.textContent = '{ "جاري الحفظ..." if is_ar else "Saving..." }';
            statusEl.style.color = '#1E90FF';
            
            const resp = await fetch(window.location.origin + '/webapp/existing-account/submit', {{
              method:'POST',
              headers:{{'Content-Type':'application/json'}},
              body:JSON.stringify(payload)
            }});
            const data = await resp.json();
            if(resp.ok){{
              statusEl.style.color='green';
              statusEl.textContent=data.message||'{ "تم الحفظ بنجاح" if is_ar else "Saved successfully" }';
              setTimeout(()=>{{try{{tg.close();}}catch(e){{}}}},1500);
              try{{tg.sendData(JSON.stringify({{status:'sent',type:'existing_account'}}));}}catch(e){{}}
            }}else{{
              statusEl.textContent=data.error||'{labels["error"]}';
              statusEl.style.color='#ff4444';
            }}
          }}catch(e){{
            statusEl.textContent='{labels["error"]}: '+e.message;
            statusEl.style.color='#ff4444';
          }}
        }}

        // إضافة مستمعين للأحداث للتحقق الفوري
        document.querySelectorAll('input, select').forEach(element => {{
          element.addEventListener('blur', validateForm);
          element.addEventListener('input', function() {{
            const value = this.value.trim();
            if (value) {{
              this.style.borderColor = '#ccc';
              const errorEl = document.getElementById(this.id + '_error');
              if (errorEl) errorEl.style.display = 'none';
            }}
          }});
        }});

        document.getElementById('submit').addEventListener('click',submitForm);
        document.getElementById('close').addEventListener('click',()=>{{try{{tg.close();}}catch(e){{}}}});
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.get("/webapp/edit-accounts")
def webapp_edit_accounts(request: Request):
    lang = (request.query_params.get("lang") or "ar").lower()
    is_ar = lang == "ar"

    page_title = "✏️ تعديل حسابات التداول" if is_ar else "✏️ Edit Trading Accounts"
    labels = {
        "select_account": "اختر الحساب" if is_ar else "Select Account",
        "broker": "اسم الشركة" if is_ar else "Broker Name",
        "account": "رقم الحساب" if is_ar else "Account Number",
        "password": "كلمة السر" if is_ar else "Password",
        "server": "سيرفر التداول" if is_ar else "Trading Server",
        "initial_balance": "رصيد البداية" if is_ar else "Initial Balance",
        "current_balance": "الرصيد الحالي" if is_ar else "Current Balance",
        "withdrawals": "المسحوبات" if is_ar else "Withdrawals",
        "copy_start_date": "تاريخ بدء النسخ" if is_ar else "Copy Start Date",
        "agent": "الوكيل" if is_ar else "Agent",
        "expected_return": "العائد المتوقع" if is_ar else "Expected Return",
        "save": "حفظ التغييرات" if is_ar else "Save Changes",
        "delete": "حذف الحساب" if is_ar else "Delete Account",
        "close": "إغلاق" if is_ar else "Close",
        "error": "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server",
        "required_field": "هذا الحقل مطلوب" if is_ar else "This field is required",
        "no_accounts": "لا توجد حسابات" if is_ar else "No accounts found",
        "account_under_review": "⚠️ الحساب قيد المراجعة - لا يمكن التعديل" if is_ar else "⚠️ Account under review - cannot edit",
        "account_under_review_delete": "⚠️ الحساب قيد المراجعة - لا يمكن الحذف" if is_ar else "⚠️ Account under review - cannot delete",
        "risk_warning": "⚠️ تنبيه: كلما ارتفع العائد المتوقع زادت المخاطر" if is_ar else "⚠️ Warning: Higher expected returns come with higher risks"
    }
    dir_attr = "rtl" if is_ar else "ltr"
    text_align = "right" if is_ar else "left"
    agents_options = "".join([f'<option value="{agent}">{agent}</option>' for agent in AGENTS_LIST])
    expected_return_options = ""
    if is_ar:
        expected_return_options = """
            <option value="">اختر العائد المتوقع</option>
            <option value="X1 = 10% - 15%">X1 = 10% - 15%</option>
            <option value="X2 = 20% - 30%">X2 = 20% - 30%</option>
            <option value="X3 = 30% - 45%">X3 = 30% - 45%</option>
            <option value="X4 = 40% - 60%">X4 = 40% - 60%</option>
        """
    else:
        expected_return_options = """
            <option value="">Select Expected Return</option>
            <option value="X1 = 10% - 15%">X1 = 10% - 15%</option>
            <option value="X2 = 20% - 30%">X2 = 20% - 30%</option>
            <option value="X3 = 30% - 45%">X3 = 30% - 45%</option>
            <option value="X4 = 40% - 60%">X4 = 40% - 60%</option>
        """

    form_labels = [
        labels['select_account'],
        labels['broker'],
        labels['account'],
        labels['password'],
        labels['server'],
        labels['save'],
        labels['delete']
    ]
    header_html = build_header_html(page_title, form_labels, header_emoji=HEADER_EMOJI, underline_enabled=False,arabic_indent=1 if lang == "ar" else 0)

    html = f"""
    <!doctype html>
    <html lang="{ 'ar' if is_ar else 'en' }" dir="{dir_attr}">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>{page_title}</title>
      <style>
        body{{font-family:Arial;padding:16px;background:#f7f7f7;direction:{dir_attr};}}
        .card{{max-width:600px;margin:24px auto;padding:16px;border-radius:10px;background:white;box-shadow:0 4px 12px rgba(0,0,0,0.1)}}
        label{{display:block;margin-top:10px;font-weight:600;text-align:{text_align}}}
        input, select{{width:100%;padding:10px;margin-top:6px;border:1px solid #ccc;border-radius:6px;font-size:16px;}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-danger{{background:#FF4500;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .btn-disabled{{background:#ccc;color:#666;cursor:not-allowed}}
        .small{{font-size:13px;color:#666;text-align:{text_align}}}
        .form-row{{display:flex;gap:10px;margin-top:10px;}}
        .form-row > div{{flex:1;}}
        .hidden{{display:none;}}
        .status-message{{padding:10px;margin:10px 0;border-radius:6px;text-align:{text_align}}}
        .status-warning{{background:#fff3cd;border:1px solid #ffeaa7;color:#856404}}
        .risk-warning{{font-size:12px;color:#ff6b35;margin-top:4px;text-align:{text_align};font-weight:500;}}
        .header-container{{text-align:{text_align}; margin-bottom:20px;}}
        .required{{color:#ff4444;}}
        .field-error{{color:#ff4444;font-size:12px;margin-top:2px;display:none;}}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header-container">
          {header_html}
        </div>
        
        <label>{labels['select_account']}</label>
        <select id="account_select">
          <option value="">{ 'جاري التحميل...' if is_ar else 'Loading...' }</option>
        </select>

        <!-- إضافة حقل مخفي لتخزين معرف الحساب الحالي -->
        <input type="hidden" id="current_account_id" value="">
        <input type="hidden" id="current_account_status" value="">

        <div id="status_message" class="status-message hidden"></div>

        <label>{labels['broker']} <span class="required">*</span></label>
        <select id="broker" required>
          <option value="">{ 'اختر الشركة' if is_ar else 'Select Broker' }</option>
          <option value="Oneroyal">Oneroyal</option>
          <option value="Scope">Scope</option>
        </select>
        <div id="broker_error" class="field-error">{labels['required_field']}</div>

        <div class="form-row">
          <div>
            <label>{labels['account']} <span class="required">*</span></label>
            <input id="account" placeholder="123456" required />
            <div id="account_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['password']} <span class="required">*</span></label>
            <input id="password" type="password" placeholder="••••••••" required />
            <div id="password_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <label>{labels['server']} <span class="required">*</span></label>
        <input id="server" placeholder="Oneroyal-Live" required />
        <div id="server_error" class="field-error">{labels['required_field']}</div>

        <div class="form-row">
          <div>
            <label>{labels['initial_balance']} <span class="required">*</span></label>
            <input id="initial_balance" type="number" placeholder="0.00" step="0.01" required />
            <div id="initial_balance_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['current_balance']} <span class="required">*</span></label>
            <input id="current_balance" type="number" placeholder="0.00" step="0.01" required />
            <div id="current_balance_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <div class="form-row">
          <div>
            <label>{labels['withdrawals']} <span class="required">*</span></label>
            <input id="withdrawals" type="number" placeholder="0.00" step="0.01" required />
            <div id="withdrawals_error" class="field-error">{labels['required_field']}</div>
          </div>
          <div>
            <label>{labels['copy_start_date']} <span class="required">*</span></label>
            <input id="copy_start_date" type="date" required />
            <div id="copy_start_date_error" class="field-error">{labels['required_field']}</div>
          </div>
        </div>

        <label>{labels['agent']} <span class="required">*</span></label>
        <select id="agent" required>
          <option value="">{ 'اختر الوكيل' if is_ar else 'Select Agent' }</option>
          {agents_options}
        </select>
        <div id="agent_error" class="field-error">{labels['required_field']}</div>

        <label>{labels['expected_return']} <span class="required">*</span></label>
        <select id="expected_return" required>
          {expected_return_options}
        </select>
        <div id="expected_return_error" class="field-error">{labels['required_field']}</div>
        <div class="risk-warning">{labels['risk_warning']}</div>

        <div style="margin-top:12px;text-align:{text_align}">
          <button class="btn btn-primary" id="save">{labels['save']}</button>
          <button class="btn btn-danger" id="delete">{labels['delete']}</button>
          <button class="btn btn-ghost" id="close">{labels['close']}</button>
        </div>
        <div id="status" class="small" style="margin-top:10px;color:#b00;"></div>
      </div>

      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script>
        const tg = window.Telegram.WebApp || {{}};
        try{{tg.expand();}}catch(e){{}}
        const statusEl = document.getElementById('status');
        const statusMessageEl = document.getElementById('status_message');
        let currentAccountId = null;
        let currentAccountStatus = null;

        // دالة للتحقق من الحقول المطلوبة
        function validateForm() {{
          const fields = [
            {{id: 'broker', name: '{labels['broker']}'}},
            {{id: 'account', name: '{labels['account']}'}},
            {{id: 'password', name: '{labels['password']}'}},
            {{id: 'server', name: '{labels['server']}'}},
            {{id: 'initial_balance', name: '{labels['initial_balance']}'}},
            {{id: 'current_balance', name: '{labels['current_balance']}'}},
            {{id: 'withdrawals', name: '{labels['withdrawals']}'}},
            {{id: 'copy_start_date', name: '{labels['copy_start_date']}'}},
            {{id: 'agent', name: '{labels['agent']}'}},
            {{id: 'expected_return', name: '{labels['expected_return']}'}}
          ];

          let isValid = true;
          
          // إخفاء جميع رسائل الخطأ أولاً
          fields.forEach(field => {{
            const errorEl = document.getElementById(field.id + '_error');
            if (errorEl) errorEl.style.display = 'none';
          }});

          // التحقق من كل حقل
          fields.forEach(field => {{
            const inputEl = document.getElementById(field.id);
            const value = inputEl.value.trim();
            
            if (!value) {{
              const errorEl = document.getElementById(field.id + '_error');
              if (errorEl) {{
                errorEl.style.display = 'block';
                errorEl.textContent = '{labels['required_field']}';
              }}
              isValid = false;
              
              // إضافة تأثير للخطأ
              inputEl.style.borderColor = '#ff4444';
            }} else {{
              inputEl.style.borderColor = '#ccc';
            }}
          }});

          return isValid;
        }}

        // دالة لتحميل الحسابات
        async function loadAccounts() {{
          const initUser = tg.initDataUnsafe.user;
          if (!initUser) {{
            statusEl.textContent = 'Unable to get user info';
            return;
          }}
          try {{
            const resp = await fetch(`${{window.location.origin}}/api/trading_accounts?tg_id=${{initUser.id}}`);
            const accounts = await resp.json();
            const select = document.getElementById('account_select');
            select.innerHTML = '';
            
            if (accounts.length === 0) {{
              select.innerHTML = `<option value="">{labels['no_accounts']}</option>`;
              disableForm();
              return;
            }}
            
            // إضافة خيار افتراضي
            select.innerHTML = `<option value="">{ 'اختر حساب للتعديل' if is_ar else 'Select account to edit' }</option>`;
            
            accounts.forEach(acc => {{
              const option = document.createElement('option');
              option.value = acc.id;
              option.textContent = `${{acc.broker_name}} - ${{acc.account_number}} (${{acc.status}})`;
              select.appendChild(option);
            }});
          }} catch (e) {{
            statusEl.textContent = '{labels["error"]}: ' + e.message;
          }}
        }}

        // دالة لتعطيل النموذج
        function disableForm() {{
          document.getElementById('broker').disabled = true;
          document.getElementById('account').disabled = true;
          document.getElementById('password').disabled = true;
          document.getElementById('server').disabled = true;
          document.getElementById('initial_balance').disabled = true;
          document.getElementById('current_balance').disabled = true;
          document.getElementById('withdrawals').disabled = true;
          document.getElementById('copy_start_date').disabled = true;
          document.getElementById('agent').disabled = true;
          document.getElementById('expected_return').disabled = true;
          document.getElementById('save').disabled = true;
          document.getElementById('delete').disabled = true;
        }}

        // دالة لتمكين النموذج
        function enableForm() {{
          document.getElementById('broker').disabled = false;
          document.getElementById('account').disabled = false;
          document.getElementById('password').disabled = false;
          document.getElementById('server').disabled = false;
          document.getElementById('initial_balance').disabled = false;
          document.getElementById('current_balance').disabled = false;
          document.getElementById('withdrawals').disabled = false;
          document.getElementById('copy_start_date').disabled = false;
          document.getElementById('agent').disabled = false;
          document.getElementById('expected_return').disabled = false;
          document.getElementById('save').disabled = false;
          document.getElementById('delete').disabled = false;
        }}

        // دالة لإدارة حالة الأزرار بناءً على حالة الحساب
        function updateButtonsBasedOnStatus() {{
          const saveBtn = document.getElementById('save');
          const deleteBtn = document.getElementById('delete');
          
          if (currentAccountStatus === 'under_review') {{
            // إذا كان الحساب قيد المراجعة، تعطيل الأزرار وإظهار رسالة
            saveBtn.disabled = true;
            saveBtn.classList.add('btn-disabled');
            deleteBtn.disabled = true;
            deleteBtn.classList.add('btn-disabled');
            
            statusMessageEl.innerHTML = `<div class="status-warning">{labels['account_under_review']}</div>`;
            statusMessageEl.classList.remove('hidden');
          }} else {{
            // إذا كان الحساب مفعل أو مرفوض، تمكين الأزرار
            saveBtn.disabled = false;
            saveBtn.classList.remove('btn-disabled');
            deleteBtn.disabled = false;
            deleteBtn.classList.remove('btn-disabled');
            statusMessageEl.classList.add('hidden');
          }}
        }}

        // دالة لتفريغ النموذج
        function clearForm() {{
          document.getElementById('broker').value = '';
          document.getElementById('account').value = '';
          document.getElementById('password').value = '';
          document.getElementById('server').value = '';
          document.getElementById('initial_balance').value = '';
          document.getElementById('current_balance').value = '';
          document.getElementById('withdrawals').value = '';
          document.getElementById('copy_start_date').value = '';
          document.getElementById('agent').value = '';
          document.getElementById('expected_return').value = '';
          document.getElementById('current_account_id').value = '';
          document.getElementById('current_account_status').value = '';
          currentAccountId = null;
          currentAccountStatus = null;
          statusMessageEl.classList.add('hidden');
        }}

        // دالة لتحميل تفاصيل الحساب
        async function loadAccountDetails(accountId) {{
          if (!accountId) {{
            clearForm();
            disableForm();
            return;
          }}
          
          try {{
            const initUser = tg.initDataUnsafe.user;
            const resp = await fetch(`${{window.location.origin}}/api/trading_accounts?tg_id=${{initUser.id}}`);
            const accounts = await resp.json();
            const acc = accounts.find(a => a.id == accountId);
            
            if (acc) {{
              // تعيين معرف الحساب الحالي وحالته
              currentAccountId = acc.id;
              currentAccountStatus = acc.status;
              document.getElementById('current_account_id').value = acc.id;
              document.getElementById('current_account_status').value = acc.status;
              document.getElementById('broker').value = acc.broker_name || '';
              document.getElementById('account').value = acc.account_number || '';
              document.getElementById('password').value = acc.password || '';
              document.getElementById('server').value = acc.server || '';
              document.getElementById('initial_balance').value = acc.initial_balance || '';
              document.getElementById('current_balance').value = acc.current_balance || '';
              document.getElementById('withdrawals').value = acc.withdrawals || '';
              document.getElementById('copy_start_date').value = acc.copy_start_date || '';
              document.getElementById('agent').value = acc.agent || '';
              document.getElementById('expected_return').value = acc.expected_return || '';
              
              enableForm();
              updateButtonsBasedOnStatus();
              
              statusEl.textContent = '';
              statusEl.style.color = '#b00';
            }} else {{
              statusEl.textContent = '{ "الحساب غير موجود" if is_ar else "Account not found" }';
              clearForm();
              disableForm();
            }}
          }} catch (e) {{
            statusEl.textContent = '{labels["error"]}: ' + e.message;
            clearForm();
            disableForm();
          }}
        }}

        // دالة لحفظ التغييرات
        async function saveChanges() {{
          const accountId = document.getElementById('current_account_id').value;
          const accountStatus = document.getElementById('current_account_status').value;
          
          if (!accountId) {{
            statusEl.textContent = '{ "يرجى اختيار حساب أولاً" if is_ar else "Please select an account first" }';
            statusEl.style.color = '#ff4444';
            return;
          }}

          // التحقق مما إذا كان الحساب قيد المراجعة
          if (accountStatus === 'under_review') {{
            statusEl.textContent = '{labels["account_under_review"]}';
            statusEl.style.color = '#ff4444';
            return;
          }}

          // التحقق من جميع الحقول المطلوبة
          if (!validateForm()) {{
            statusEl.textContent = '{ "يرجى ملء جميع الحقول المطلوبة" if is_ar else "Please fill all required fields" }';
            statusEl.style.color = '#ff4444';
            return;
          }}

          const payload = {{
            id: parseInt(accountId),
            broker_name: document.getElementById('broker').value.trim(),
            account_number: document.getElementById('account').value.trim(),
            password: document.getElementById('password').value.trim(),
            server: document.getElementById('server').value.trim(),
            initial_balance: document.getElementById('initial_balance').value.trim(),
            current_balance: document.getElementById('current_balance').value.trim(),
            withdrawals: document.getElementById('withdrawals').value.trim(),
            copy_start_date: document.getElementById('copy_start_date').value.trim(),
            agent: document.getElementById('agent').value.trim(),
            expected_return: document.getElementById('expected_return').value.trim(),
            tg_user: tg.initDataUnsafe.user,
            lang: "{lang}"
          }};

          try {{
            statusEl.textContent = '{ "جاري الحفظ..." if is_ar else "Saving..." }';
            statusEl.style.color = '#1E90FF';
            
            const resp = await fetch(`${{window.location.origin}}/api/update_trading_account`, {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify(payload)
            }});
            
            const data = await resp.json();
            
            if (data.success) {{
              statusEl.style.color = 'green';
              statusEl.textContent = '{ "تم حفظ التغييرات بنجاح" if is_ar else "Changes saved successfully" }';
              
              // إعادة تحميل الحسابات لتحديث القائمة
              await loadAccounts();
              
              setTimeout(() => {{ 
                try{{ 
                  tg.close(); 
                }}catch(e){{
                  console.log('Telegram WebApp closed');
                }}
              }}, 1500);
            }} else {{
              statusEl.style.color = '#ff4444';
              statusEl.textContent = data.detail || '{labels["error"]}';
            }}
          }} catch (e) {{
            statusEl.style.color = '#ff4444';
            statusEl.textContent = '{labels["error"]}: ' + e.message;
          }}
        }}

        // دالة لحذف الحساب
        async function deleteAccount() {{
          const accountId = document.getElementById('current_account_id').value;
          const accountStatus = document.getElementById('current_account_status').value;
          
          if (!accountId) {{
            statusEl.textContent = '{ "يرجى اختيار حساب أولاً" if is_ar else "Please select an account first" }';
            statusEl.style.color = '#ff4444';
            return;
          }}

          // التحقق مما إذا كان الحساب قيد المراجعة
          if (accountStatus === 'under_review') {{
            statusEl.textContent = '{labels["account_under_review_delete"]}';
            statusEl.style.color = '#ff4444';
            return;
          }}

          if (!confirm('{ "هل أنت متأكد من حذف هذا الحساب؟" if is_ar else "Are you sure you want to delete this account?" }')) {{
            return;
          }}

          const payload = {{
            id: parseInt(accountId),
            tg_user: tg.initDataUnsafe.user,
            lang: "{lang}"
          }};

          try {{
            statusEl.textContent = '{ "جاري الحذف..." if is_ar else "Deleting..." }';
            statusEl.style.color = '#1E90FF';
            
            const resp = await fetch(`${{window.location.origin}}/api/delete_trading_account`, {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify(payload)
            }});
            
            const data = await resp.json();
            
            if (data.success) {{
              statusEl.style.color = 'green';
              statusEl.textContent = '{ "تم حذف الحساب بنجاح" if is_ar else "Account deleted successfully" }';
              
              // إعادة تحميل الحسابات وتفريغ النموذج
              await loadAccounts();
              clearForm();
              disableForm();
              
              setTimeout(() => {{ 
                try{{ 
                  tg.close(); 
                }}catch(e){{
                  console.log('Telegram WebApp closed');
                }}
              }}, 1500);
            }} else {{
              statusEl.style.color = '#ff4444';
              statusEl.textContent = data.detail || '{labels["error"]}';
            }}
          }} catch (e) {{
            statusEl.style.color = '#ff4444';
            statusEl.textContent = '{labels["error"]}: ' + e.message;
          }}
        }}

        // تهيئة الصفحة
        document.addEventListener('DOMContentLoaded', function() {{
          // تحميل الحسابات أولاً
          loadAccounts();
          
          // تعطيل النموذج في البداية
          disableForm();

          // إضافة مستمعين للتحقق من الحقول
          document.querySelectorAll('input, select').forEach(element => {{
            element.addEventListener('blur', validateForm);
            element.addEventListener('input', function() {{
              const value = this.value.trim();
              if (value) {{
                this.style.borderColor = '#ccc';
                const errorEl = document.getElementById(this.id + '_error');
                if (errorEl) errorEl.style.display = 'none';
              }}
            }});
          }});
        }});

        // إضافة المستمعين للأحداث
        document.getElementById('account_select').addEventListener('change', function(e) {{
          loadAccountDetails(e.target.value);
        }});
        
        document.getElementById('save').addEventListener('click', saveChanges);
        document.getElementById('delete').addEventListener('click', deleteAccount);
        document.getElementById('close').addEventListener('click', function() {{ 
          try{{ 
            tg.close(); 
          }}catch(e){{
            console.log('Telegram WebApp closed');
          }}
        }});
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

# ===============================
# API for trading accounts
# ===============================
@app.get("/api/trading_accounts")
def api_get_trading_accounts(tg_id: int):
    user_data = get_subscriber_with_accounts(tg_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    return user_data["trading_accounts"]

@app.post("/api/update_trading_account")
async def api_update_trading_account(payload: dict = Body(...)):
    try:
        tg_user = payload.get("tg_user") or {}
        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        lang = (payload.get("lang") or "ar").lower()
        account_id = payload.get("id")
        if not telegram_id or not account_id:
            raise HTTPException(status_code=400, detail="Missing required fields")

        accounts = get_trading_accounts_by_telegram_id(telegram_id)
        if not any(acc.id == account_id for acc in accounts):
            raise HTTPException(status_code=403, detail="Account not owned by user")

        update_data = {k: v for k, v in payload.items() if k not in ["id", "tg_user", "lang", "created_at"]}

        success, _ = update_trading_account(account_id, **update_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update account")

        ref = get_form_ref(telegram_id)
        if ref and ref.get("origin") == "my_accounts":
            await refresh_user_accounts_interface(telegram_id, lang, ref["chat_id"], ref["message_id"])

        return {"success": True}
    except Exception as e:
        logger.exception(f"Error in api_update_trading_account: {e}")
        raise HTTPException(status_code=500, detail="Server error")

@app.post("/api/delete_trading_account")
async def api_delete_trading_account(payload: dict = Body(...)):
    try:
        tg_user = payload.get("tg_user") or {}
        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        lang = (payload.get("lang") or "ar").lower()
        account_id = payload.get("id")
        if not telegram_id or not account_id:
            raise HTTPException(status_code=400, detail="Missing required fields")

        accounts = get_trading_accounts_by_telegram_id(telegram_id)
        if not any(acc.id == account_id for acc in accounts):
            raise HTTPException(status_code=403, detail="Account not owned by user")

        success = delete_trading_account(account_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete account")

        ref = get_form_ref(telegram_id)
        if ref and ref.get("origin") == "my_accounts":
            await refresh_user_accounts_interface(telegram_id, lang, ref["chat_id"], ref["message_id"])

        return {"success": True}
    except Exception as e:
        logger.exception(f"Error in api_delete_trading_account: {e}")
        raise HTTPException(status_code=500, detail="Server error")

async def refresh_user_accounts_interface(telegram_id: int, lang: str, chat_id: int, message_id: int):
    
    updated_data = get_subscriber_with_accounts(telegram_id)
    if not updated_data:
        return

    if lang == "ar":
        header_title = "👤 بياناتي وحساباتي"
        add_account_label = "➕ إضافة حساب تداول"
        edit_accounts_label = "✏️ تعديل حساباتي" if len(updated_data['trading_accounts']) > 0 else None
        edit_data_label = "✏️ تعديل بياناتي"
        back_label = "🔙 الرجوع لتداول الفوركس"
        labels = [header_title, add_account_label]
        if edit_accounts_label:
            labels.append(edit_accounts_label)
        labels.extend([edit_data_label, back_label])
        header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1)
        user_info = f"👤 <b>الاسم:</b> {updated_data['name']}\n📧 <b>البريد:</b> {updated_data['email']}\n📞 <b>الهاتف:</b> {updated_data['phone']}"
        accounts_header = "\n\n🏦 <b>حسابات التداول:</b>"
        no_accounts = "\nلا توجد حسابات مسجلة بعد."
    else:
        header_title = "👤 My Data & Accounts"
        add_account_label = "➕ Add Trading Account"
        edit_accounts_label = "✏️ Edit My Accounts" if len(updated_data['trading_accounts']) > 0 else None
        edit_data_label = "✏️ Edit my data"
        back_label = "🔙 Back to Forex"
        labels = [header_title, add_account_label]
        if edit_accounts_label:
            labels.append(edit_accounts_label)
        labels.extend([edit_data_label, back_label])
        header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, arabic_indent=0)
        user_info = f"👤 <b>Name:</b> {updated_data['name']}\n📧 <b>Email:</b> {updated_data['email']}\n📞 <b>Phone:</b> {updated_data['phone']}"
        accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
        no_accounts = "\nNo trading accounts registered yet."

    updated_message = f"{header}\n\n{user_info}{accounts_header}\n"
    
    today = datetime.now()
    
    if updated_data['trading_accounts']:
        for i, acc in enumerate(updated_data['trading_accounts'], 1):
            status_text = get_account_status_text(acc['status'], lang, acc.get('rejection_reason'))
            if lang == "ar":
                account_text = f"\n\u200F{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   \u200F🖥️ {acc['server']}\n   📊 <b>الحالة:</b> {status_text}\n"
                if acc.get('initial_balance'):
                    account_text += f"   💰 رصيد البداية: {acc['initial_balance']}\n"
                if acc.get('current_balance'):
                    account_text += f"   💳 الرصيد الحالي: {acc['current_balance']}\n"
                if acc.get('withdrawals'):
                    account_text += f"   💸 المسحوبات: {acc['withdrawals']}\n"
                if acc.get('copy_start_date'):
                    account_text += f"   📅 تاريخ البدء: {acc['copy_start_date']}\n"
                if acc.get('agent'):
                    account_text += f"   👤 الوكيل: {acc['agent']}\n"
                if acc.get('expected_return'):
                    account_text += f"   📈 العائد المتوقع: {acc['expected_return']}\n"
                
                if acc.get('initial_balance') and acc.get('current_balance') and acc.get('withdrawals') and acc.get('copy_start_date'):
                    try:
                        initial = float(acc['initial_balance'])
                        current = float(acc['current_balance'])
                        withdrawals = float(acc['withdrawals'])
                        start_date_str = acc['copy_start_date']
                        
                        if 'T' in start_date_str:
                            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                        else:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                        
                        delta = today - start_date
                        total_days = delta.days
                        
                        months = total_days // 30
                        remaining_days = total_days % 30
                        
                        period_text = ""
                        if months > 0:
                            period_text += f"{months} شهر"
                            if remaining_days > 0:
                                period_text += f" و{remaining_days} يوم"
                        else:
                            period_text += f"{total_days} يوم"
                        
                        if initial > 0:
                            total_value = current + withdrawals
                            profit_amount = total_value - initial
                            profit_percentage = (profit_amount / initial) * 100
                            
                            account_text += f"   📈 <b>العائد المحقق:</b> {profit_percentage:.0f}% خلال {period_text}\n"
                            
                    except (ValueError, TypeError) as e:
                        account_text += f"   📈 <b>العائد المحقق:</b> جاري الحساب\n"
                else:
                    account_text += f"   📈 <b>العائد المحقق:</b> يتطلب بيانات كاملة\n"
                    
            else:
                account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n   📊 <b>Status:</b> {status_text}\n"
                if acc.get('initial_balance'):
                    account_text += f"   💰 Initial Balance: {acc['initial_balance']}\n"
                if acc.get('current_balance'):
                    account_text += f"   💳 Current Balance: {acc['current_balance']}\n"
                if acc.get('withdrawals'):
                    account_text += f"   💸 Withdrawals: {acc['withdrawals']}\n"
                if acc.get('copy_start_date'):
                    account_text += f"   📅 Start Date: {acc['copy_start_date']}\n"
                if acc.get('agent'):
                    account_text += f"   👤 Agent: {acc['agent']}\n"
                if acc.get('expected_return'):
                    account_text += f"   📈 Expected Return: {acc['expected_return']}\n"
                
                
                if acc.get('initial_balance') and acc.get('current_balance') and acc.get('withdrawals') and acc.get('copy_start_date'):
                    try:
                        initial = float(acc['initial_balance'])
                        current = float(acc['current_balance'])
                        withdrawals = float(acc['withdrawals'])
                        start_date_str = acc['copy_start_date']
                        
                        if 'T' in start_date_str:
                            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                        else:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                        
                        delta = today - start_date
                        total_days = delta.days
                        
                        months = total_days // 30
                        remaining_days = total_days % 30
                        
                        period_text = ""
                        if months > 0:
                            period_text += f"{months} month"
                            if months > 1:
                                period_text += "s"
                            if remaining_days > 0:
                                period_text += f" and {remaining_days} day"
                                if remaining_days > 1:
                                    period_text += "s"
                        else:
                            period_text += f"{total_days} day"
                            if total_days > 1:
                                period_text += "s"
                        
                        if initial > 0:
                            total_value = current + withdrawals
                            profit_amount = total_value - initial
                            profit_percentage = (profit_amount / initial) * 100
                            
                            account_text += f"   📈 <b>Achieved Return:</b> {profit_percentage:.0f}% over {period_text}\n"
                            
                    except (ValueError, TypeError) as e:
                        account_text += f"   📈 <b>Achieved Return:</b> Calculating...\n"
                else:
                    account_text += f"   📈 <b>Achieved Return:</b> Requires complete data\n"
                    
            updated_message += account_text
    else:
        updated_message += f"\n{no_accounts}"

    keyboard = []
    if WEBAPP_URL:
        url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
        keyboard.append([InlineKeyboardButton(add_account_label, web_app=WebAppInfo(url=url_with_lang))])
    if WEBAPP_URL and len(updated_data['trading_accounts']) > 0:
        edit_accounts_url = f"{WEBAPP_URL}/edit-accounts?lang={lang}"
        keyboard.append([InlineKeyboardButton(edit_accounts_label, web_app=WebAppInfo(url=edit_accounts_url))])
    if WEBAPP_URL:
        params = {"lang": lang, "edit": "1", "name": updated_data['name'], "email": updated_data['email'], "phone": updated_data['phone']}
        edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
        keyboard.append([InlineKeyboardButton(edit_data_label, web_app=WebAppInfo(url=edit_url))])
    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=updated_message,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        save_form_ref(telegram_id, chat_id, message_id, origin="my_accounts", lang=lang)
    except Exception as e:
        logger.exception(f"Failed to refresh user interface: {e}")
        
       
        try:
            sent = await application.bot.send_message(
                chat_id=telegram_id,
                text=updated_message,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="my_accounts", lang=lang)
        except Exception as fallback_error:
            logger.exception("Failed to send fallback refresh message: {fallback_error}")

# ===============================
# POST endpoint: receive form submission from WebApp (original registration)
# ===============================
@app.post("/webapp/submit")
async def webapp_submit(payload: dict = Body(...)):
    try:
        name = (payload.get("name") or "").strip()
        email = (payload.get("email") or "").strip()
        phone = (payload.get("phone") or "").strip()
        tg_user = payload.get("tg_user") or {}
        page_lang = (payload.get("lang") or "").lower() or None

        # التحقق من صحة البيانات
        if not name or len(name) < 2:
            return JSONResponse(status_code=400, content={"error": "Name too short or missing."})
        if not EMAIL_RE.match(email):
            return JSONResponse(status_code=400, content={"error": "Invalid email."})
        if not PHONE_RE.match(phone):
            return JSONResponse(status_code=400, content={"error": "Invalid phone."})

        # تحديد اللغة - إصلاح المنطق هنا
        detected_lang = "ar"  # الافتراضي عربي
        if page_lang in ("ar", "en"):
            detected_lang = page_lang
        else:
            lang_code = tg_user.get("language_code") if isinstance(tg_user, dict) else None
            if lang_code and str(lang_code).startswith("en"):
                detected_lang = "en"

        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        telegram_username = tg_user.get("username") if isinstance(tg_user, dict) else None

        # حفظ أو تحديث بيانات المشترك
        result, subscriber = save_or_update_subscriber(
            name=name, 
            email=email, 
            phone=phone, 
            lang=detected_lang, 
            telegram_id=telegram_id, 
            telegram_username=telegram_username
        )

        # استخدام اللغة المحددة من الصفحة كأولوية
        display_lang = page_lang if page_lang in ("ar", "en") else detected_lang

        # الحصول على المرجع إذا كان موجوداً
        ref = get_form_ref(telegram_id) if telegram_id else None
        
        # إذا كان تعديل بيانات من قسم "بياناتي وحساباتي"
        is_edit_mode = payload.get("edit") == "1"
        if ref and ref.get("origin") == "my_accounts" and (is_edit_mode or result == "updated"):
            await refresh_user_accounts_interface(telegram_id, display_lang, ref["chat_id"], ref["message_id"])
            return JSONResponse(content={"message": "Updated successfully."})
            
        # إذا كان التسجيل من طلب EA
        if ref and ref.get("origin") == "open_form_ea":
            ea_link = "https://t.me/Nagyfx"
            if display_lang == "ar":
                title = "طلب اختبار أنظمة YesFX (الوكلاء فقط)"
                message_text = ""
                button_text = "🤖 طلب اختبار أنظمة YesFX (الوكلاء فقط)"
                back_button = "🔙 الرجوع لتداول الفوركس"
            else:
                title = "Request to Test YesFX Systems (Agents Only)"
                message_text = ""
                button_text = "🤖 Request to Test YesFX Systems (Agents Only)"
                back_button = "🔙 Back to Forex"

            labels = [button_text, back_button]
            header = build_header_html(title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang == "ar" else 0)

            keyboard = [
                [InlineKeyboardButton(button_text, url=ea_link)],
                [InlineKeyboardButton(back_button, callback_data="forex_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await application.bot.edit_message_text(
                    text=header + f"\n\n{message_text}",
                    chat_id=ref["chat_id"], 
                    message_id=ref["message_id"],
                    reply_markup=reply_markup, 
                    parse_mode="HTML", 
                    disable_web_page_preview=True
                )
                clear_form_ref(telegram_id)
            except Exception:
                logger.exception("Failed to edit EA request message")
                
            return JSONResponse(content={"message": "Sent successfully."})

        # إذا كان التسجيل الأولي من نموذج اللغة
        elif ref and ref.get("origin") == "initial_registration":
            # عرض القوائم الرئيسية بعد التسجيل الناجح
            if telegram_id:
                try:
                    if display_lang == "ar":
                        sections = [("💹 تداول الفوركس", "forex_main"), ("💻 خدمات البرمجة", "dev_main")]
                        title = "الأقسام الرئيسية"
                        back_button = ("🔙 الرجوع للغة", "back_language")
                    else:
                        sections = [("💹 Forex Trading", "forex_main"), ("💻 Programming Services", "dev_main")]
                        title = "Main Sections"
                        back_button = ("🔙 Back to language", "back_language")

                    keyboard = [[InlineKeyboardButton(name, callback_data=cb)] for name, cb in sections]
                    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    labels = [name for name, _ in sections] + [back_button[0]]
                    header = build_header_html(title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang == "ar" else 0)
                    
                    try:
                        await application.bot.edit_message_text(
                            text=header,
                            chat_id=ref["chat_id"], 
                            message_id=ref["message_id"],
                            reply_markup=reply_markup, 
                            parse_mode="HTML", 
                            disable_web_page_preview=True
                        )
                        clear_form_ref(telegram_id)
                    except Exception:
                        # إذا فشل التعديل، إرسال رسالة جديدة
                        sent = await application.bot.send_message(
                            chat_id=telegram_id,
                            text=header,
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="main_sections", lang=display_lang)
                        
                except Exception as e:
                    logger.exception(f"Failed to show main sections after initial registration: {e}")
                    
            return JSONResponse(content={"message": "Registered successfully."})

        else:
            # الحالة الافتراضية: عرض وسيطي التداول بعد التسجيل
            if display_lang == "ar":
                header_title = "اختر وسيطك الآن"
                brokers_title = "🎉 تم تسجيل بياناتك بنجاح! يمكنك الآن فتح حساب تداول مع أحد الوسيطين المعتمدين:"
                back_label = "🔙 الرجوع لتداول الفوركس"
                accounts_label = "👤 بياناتي وحساباتي"
            else:
                header_title = "Choose your broker now"
                brokers_title = "🎉 Your data has been registered successfully! You can now open a trading account with one of our approved brokers:"
                back_label = "🔙 Back to Forex"
                accounts_label = "👤 My Data & Accounts"
            
            keyboard = [
                [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
                 InlineKeyboardButton("🏦 Scope", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
            ]

            keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
            keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            # محاولة تعديل الرسالة الأصلية إذا كان هناك مرجع
            edited = False
            if ref:
                try:
                    await application.bot.edit_message_text(
                        text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}",
                        chat_id=ref["chat_id"], 
                        message_id=ref["message_id"],
                        reply_markup=reply_markup, 
                        parse_mode="HTML", 
                        disable_web_page_preview=True
                    )
                    edited = True
                    clear_form_ref(telegram_id)
                except Exception:
                    logger.exception("Failed to edit original form message")

            # إذا لم يتم التعديل، إرسال رسالة جديدة
            if not edited and telegram_id:
                try:
                    sent = await application.bot.send_message(
                        chat_id=telegram_id, 
                        text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", 
                        reply_markup=reply_markup, 
                        parse_mode="HTML", 
                        disable_web_page_preview=True
                    )
                    save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
                except Exception:
                    logger.exception("Failed to send brokers message to user")

        # إرجاع الاستجابة النهائية
        if result == "created":
            return JSONResponse(content={"message": "Registered successfully."})
        elif result == "updated":
            return JSONResponse(content={"message": "Updated successfully."})
        else:
            return JSONResponse(content={"message": "Processed successfully."})
            
    except Exception as e:
        logger.exception("Error in webapp_submit: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

async def show_user_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, lang: str):
    user_data = get_subscriber_with_accounts(telegram_id)
    
    if not user_data:
        
        header = build_header_html(
            "⚠️" + (" تنبيه" if lang == "ar" else " Alert"),
            [],
            header_emoji="⚠️",
            arabic_indent=1 if lang == "ar" else 0
        )
        
        text = "⚠️ لم تقم بالتسجيل بعد. يرجى التسجيل أولاً." if lang == "ar" else "⚠️ You haven't registered yet. Please register first."
        
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(header + f"\n\n{text}")
        else:
            await context.bot.send_message(chat_id=telegram_id, text=header + f"\n\n{text}")
        return

    if lang == "ar":
        header_title = "👤 بياناتي وحساباتي"
        add_account_label = "➕ إضافة حساب تداول"
        edit_accounts_label = "✏️ تعديل حساباتي" if len(user_data['trading_accounts']) > 0 else None
        edit_data_label = "✏️ تعديل بياناتي"
        back_label = "🔙 الرجوع لتداول الفوركس"
        labels = [header_title, add_account_label]
        if edit_accounts_label:
            labels.append(edit_accounts_label)
        labels.extend([edit_data_label, back_label])
        header = build_header_html(
            header_title, 
            labels,
            header_emoji=HEADER_EMOJI,
            arabic_indent=1
        )
        
        user_info = f"👤 <b>الاسم:</b> {user_data['name']}\n📧 <b>البريد:</b> {user_data['email']}\n📞 <b>الهاتف:</b> {user_data['phone']}"
        accounts_header = "\n\n🏦 <b>حسابات التداول:</b>"
        no_accounts = "\nلا توجد حسابات مسجلة بعد."
        
    else:
        header_title = "👤 My Data & Accounts"
        add_account_label = "➕ Add Trading Account"
        edit_accounts_label = "✏️ Edit My Accounts" if len(user_data['trading_accounts']) > 0 else None
        edit_data_label = "✏️ Edit my data"
        back_label = "🔙 Back to Forex"
        labels = [header_title, add_account_label]
        if edit_accounts_label:
            labels.append(edit_accounts_label)
        labels.extend([edit_data_label, back_label])
        header = build_header_html(
            header_title, 
            labels,
            header_emoji=HEADER_EMOJI,
            arabic_indent=0
        )
     
        user_info = f"👤 <b>Name:</b> {user_data['name']}\n📧 <b>Email:</b> {user_data['email']}\n📞 <b>Phone:</b> {user_data['phone']}"
        accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
        no_accounts = "\nNo trading accounts registered yet."

    message = f"{header}\n\n{user_info}{accounts_header}\n"
    
    today = datetime.now()  
    
    if user_data['trading_accounts']:
        for i, acc in enumerate(user_data['trading_accounts'], 1):
            status_text = get_account_status_text(acc['status'], lang, acc.get('rejection_reason'))
            
            if lang == "ar":
                account_text = f"\n\u200F{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   \u200F🖥️ {acc['server']}\n   📊 <b>الحالة:</b> {status_text}\n"
                
                if acc.get('initial_balance'):
                    account_text += f"   💰 رصيد البداية: {acc['initial_balance']}\n"
                if acc.get('current_balance'):
                    account_text += f"   💳 الرصيد الحالي: {acc['current_balance']}\n"
                if acc.get('withdrawals'):
                    account_text += f"   💸 المسحوبات: {acc['withdrawals']}\n"
                if acc.get('copy_start_date'):
                    account_text += f"   📅 تاريخ بدء النسخ: {acc['copy_start_date']}\n"
                if acc.get('agent'):
                    account_text += f"   👤 الوكيل: {acc['agent']}\n"
                if acc.get('expected_return'):
                    account_text += f"   📈 العائد المتوقع: {acc['expected_return']}\n"
                
               
                if acc.get('initial_balance') and acc.get('current_balance') and acc.get('withdrawals') and acc.get('copy_start_date'):
                    try:
                        initial = float(acc['initial_balance'])
                        current = float(acc['current_balance'])
                        withdrawals = float(acc['withdrawals'])
                        start_date_str = acc['copy_start_date']
                        
                        
                        if 'T' in start_date_str:
                            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                        else:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                        
                       
                        delta = today - start_date
                        total_days = delta.days
                        
                        
                        months = total_days // 30
                        remaining_days = total_days % 30
                        
                        
                        period_text = ""
                        if months > 0:
                            period_text += f"{months} شهر"
                            if remaining_days > 0:
                                period_text += f" و{remaining_days} يوم"
                        else:
                            period_text += f"{total_days} يوم"
                        
                        
                        if initial > 0:
                            total_value = current + withdrawals
                            profit_amount = total_value - initial
                            profit_percentage = (profit_amount / initial) * 100
                            
                            
                            account_text += f"   📈 <b>العائد المحقق:</b> {profit_percentage:.0f}% خلال {period_text}\n"
                            
                    except (ValueError, TypeError) as e:
                        
                        account_text += f"   📈 <b>العائد المحقق:</b> جاري الحساب\n"
                else:
                    
                    account_text += f"   📈 <b>العائد المحقق:</b> يتطلب بيانات كاملة\n"
                    
            else:
                account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n   📊 <b>Status:</b> {status_text}\n"
                
                if acc.get('initial_balance'):
                    account_text += f"   💰 Initial Balance: {acc['initial_balance']}\n"
                if acc.get('current_balance'):
                    account_text += f"   💳 Current Balance: {acc['current_balance']}\n"
                if acc.get('withdrawals'):
                    account_text += f"   💸 Withdrawals: {acc['withdrawals']}\n"
                if acc.get('copy_start_date'):
                    account_text += f"   📅 Start Date: {acc['copy_start_date']}\n"
                if acc.get('agent'):
                    account_text += f"   👤 Agent: {acc['agent']}\n"
                if acc.get('expected_return'):
                    account_text += f"   📈 Expected Return: {acc['expected_return']}\n"
                
                
                if acc.get('initial_balance') and acc.get('current_balance') and acc.get('withdrawals') and acc.get('copy_start_date'):
                    try:
                        initial = float(acc['initial_balance'])
                        current = float(acc['current_balance'])
                        withdrawals = float(acc['withdrawals'])
                        start_date_str = acc['copy_start_date']
                        
                       
                        if 'T' in start_date_str:
                            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                        else:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                        
                       
                        delta = today - start_date
                        total_days = delta.days
                        
                        
                        months = total_days // 30
                        remaining_days = total_days % 30
                        
                        
                        period_text = ""
                        if months > 0:
                            period_text += f"{months} month"
                            if months > 1:
                                period_text += "s"
                            if remaining_days > 0:
                                period_text += f" and {remaining_days} day"
                                if remaining_days > 1:
                                    period_text += "s"
                        else:
                            period_text += f"{total_days} day"
                            if total_days > 1:
                                period_text += "s"
                        
                        
                        if initial > 0:
                            total_value = current + withdrawals
                            profit_amount = total_value - initial
                            profit_percentage = (profit_amount / initial) * 100
                            
                           
                            account_text += f"   📈 <b>Achieved Return:</b> {profit_percentage:.0f}% over {period_text}\n"
                            
                    except (ValueError, TypeError) as e:
                       
                        account_text += f"   📈 <b>Achieved Return:</b> Calculating...\n"
                else:
                   
                    account_text += f"   📈 <b>Achieved Return:</b> Requires complete data\n"
                    
            message += account_text
    else:
        message += f"\n{no_accounts}"

    keyboard = []
    
    if WEBAPP_URL:
        url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
        keyboard.append([InlineKeyboardButton(add_account_label, web_app=WebAppInfo(url=url_with_lang))])
    
    if WEBAPP_URL and len(user_data['trading_accounts']) > 0:
        edit_accounts_url = f"{WEBAPP_URL}/edit-accounts?lang={lang}"
        keyboard.append([InlineKeyboardButton(edit_accounts_label, web_app=WebAppInfo(url=edit_accounts_url))])
    
    if WEBAPP_URL:
        params = {
            "lang": lang,
            "edit": "1",
            "name": user_data['name'],
            "email": user_data['email'],
            "phone": user_data['phone']
        }
        edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
        keyboard.append([InlineKeyboardButton(edit_data_label, web_app=WebAppInfo(url=edit_url))])
    
    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(
                message, 
                reply_markup=reply_markup, 
                parse_mode="HTML", 
                disable_web_page_preview=True
            )
            
            save_form_ref(telegram_id, update.callback_query.message.chat_id, update.callback_query.message.message_id, origin="my_accounts", lang=lang)
        else:
            sent = await context.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
            save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="my_accounts", lang=lang)
    except Exception as e:
        logger.exception("Failed to show user accounts: %s", e)
        
       
        try:
            sent = await context.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
           
            save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="my_accounts", lang=lang)
        except Exception as fallback_error:
            logger.exception("Failed to send fallback message for user accounts: %s", fallback_error)
# ===============================
# menu_handler
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    q = update.callback_query
    await q.answer()
    if not q.message:
        logger.error("No message in callback_query")
        return
    user_id = q.from_user.id
    
    lang = context.user_data.get("lang", "ar")

    if q.data == "my_accounts":
        await show_user_accounts(update, context, user_id, lang)
        return

    if q.data == "add_trading_account":
        if WEBAPP_URL:
            url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
            
            try:
                await q.edit_message_text(
                    "⏳ جاري فتح نموذج إضافة الحساب..." if lang == "ar" else "⏳ Opening account form...",
                    parse_mode="HTML"
                )
                
                open_label = "🧾 افتح نموذج إضافة الحساب" if lang == "ar" else "🧾 Open Account Form"
                keyboard = [[InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text="اضغط لفتح نموذج إضافة الحساب:" if lang == "ar" else "Click to open account form:",
                    reply_markup=reply_markup
                )
            except Exception:
                logger.exception("Failed to open account form directly")
        else:
            text = "⚠️ لا يمكن فتح النموذج حالياً." if lang == "ar" else "⚠️ Cannot open form at the moment."
            await q.edit_message_text(text)
        return

    if q.data == "edit_my_data":
        subscriber = get_subscriber_by_telegram_id(user_id)
        if not subscriber:
            text = "⚠️ لم تقم بالتسجيل بعد." if lang == "ar" else "⚠️ You haven't registered yet."
            await q.edit_message_text(text)
            return

        if WEBAPP_URL:
            params = {
                "lang": lang,
                "edit": "1",
                "name": subscriber.name,
                "email": subscriber.email,
                "phone": subscriber.phone
            }
            url_with_prefill = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
            
            try:
                await q.edit_message_text(
                    "⏳ جاري فتح نموذج التعديل..." if lang == "ar" else "⏳ Opening edit form...",
                    parse_mode="HTML"
                )
                
                open_label = "✏️ افتح نموذج التعديل" if lang == "ar" else "✏️ Open Edit Form"
                keyboard = [[InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_prefill))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text="اضغط لفتح نموذج تعديل البيانات:" if lang == "ar" else "Click to open edit form:",
                    reply_markup=reply_markup
                )
            except Exception:
                logger.exception("Failed to open edit form directly")
        else:
            text = "⚠️ لا يمكن فتح النموذج حالياً." if lang == "ar" else "⚠️ Cannot open form at the moment."
            await q.edit_message_text(text)
        return

    if q.data == "back_language":
        await start(update, context)
        return
        
    if q.data == "back_main":
        await show_main_sections(update, context, lang)
        return
        
    sections_data = {
        "forex_main": {
            "ar": ["📊 نسخ الصفقات", "🤖 طلب اختبار أنظمة YesFX (الوكلاء فقط)"],
            "en": ["📊 Copy Trading", "🤖 Request to Test YesFX Systems (Agents Only)"],
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

    if q.data in sections_data:
        data = sections_data[q.data]
        options = data[lang]
        title = data[f"title_{lang}"]
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"
        labels = options + [back_label]
        header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"
        box = build_header_html(title, labels, header_emoji=header_emoji_for_lang, arabic_indent=1 if lang=="ar" else 0)
        keyboard = []
        for name in options:
            if name in ("🤖 طلب اختبار أنظمة YesFX (الوكلاء فقط)", "🤖 Request to Test YesFX Systems (Agents Only)"):
                keyboard.append([InlineKeyboardButton(name, url="https://t.me/Nagyfx")])
            else:
                keyboard.append([InlineKeyboardButton(name, callback_data=name)])
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await q.edit_message_text(box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin=q.data, lang=lang)
        except Exception:
            await context.bot.send_message(chat_id=q.message.chat_id, text=box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    if q.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
        display_lang = lang
        if display_lang == "ar":
            header_title = "اختر وسيطك الآن"
            brokers_title = ""
            back_label = "🔙 الرجوع لتداول الفوركس"
            accounts_label = "👤 بياناتي وحساباتي"
        else:
            header_title = "Choose your broker now"
            brokers_title = ""
            back_label = "🔙 Back to Forex"
            accounts_label = "👤 My Data & Accounts"

        keyboard = [
            [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
             InlineKeyboardButton("🏦 Scope", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
        ]

        keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
        keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await q.edit_message_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="brokers", lang=display_lang)
        except Exception:
            try:
                sent = await context.bot.send_message(chat_id=q.message.chat_id, text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                save_form_ref(user_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
            except Exception:
                logger.exception("Failed to show congrats screen for already-registered user.")
        return

    if q.data in ("👤 بياناتي وحساباتي", "👤 My Data & Accounts"):
        await show_user_accounts(update, context, user_id, lang)
        return

    # =============================================
    # NEW: Handle all service buttons with proper formatting
    # =============================================
    
    
    service_titles = {
        "📈 برمجة المؤشرات": {"ar": "برمجة المؤشرات", "en": "Indicators Programming"},
        "📈 Indicators": {"ar": "برمجة المؤشرات", "en": "Indicators Programming"},
        "🤖 برمجة الاكسبيرتات": {"ar": "برمجة الاكسبيرتات", "en": "Expert Advisors Programming"},
        "🤖 Expert Advisors": {"ar": "برمجة الاكسبيرتات", "en": "Expert Advisors Programming"},
        "💬 بوتات التليجرام": {"ar": "بوتات التليجرام", "en": "Telegram Bots"},
        "💬 Telegram Bots": {"ar": "بوتات التليجرام", "en": "Telegram Bots"},
        "🌐 مواقع الويب": {"ar": "مواقع الويب", "en": "Web Development"},
        "🌐 Web Development": {"ar": "مواقع الويب", "en": "Web Development"},
        
        
        "📄 طلب وكالة YesFX": {"ar": "طلب وكالة YesFX", "en": "YesFX Partnership Request"},
        "📄 Request YesFX Partnership": {"ar": "طلب وكالة YesFX", "en": "YesFX Partnership Request"},
        
        
        "💬 قناة التوصيات": {"ar": "قناة التوصيات", "en": "Signals Channel"},
        "💬 Signals Channel": {"ar": "قناة التوصيات", "en": "Signals Channel"},
        "📰 الأخبار الاقتصادية": {"ar": "الأخبار الاقتصادية", "en": "Economic News"},
        "📰 Economic News": {"ar": "الأخبار الاقتصادية", "en": "Economic News"}
    }
    
    if q.data in service_titles:
        service_title = service_titles[q.data][lang]
        
        if lang == "ar":
            support_label = "💬 التواصل مع الدعم"
            back_label = "🔙 الرجوع"
            description = f"""
نحن هنا لمساعدتك في {service_title}!

<b>📞 للاستفسار أو الطلب:</b>
• اضغط على زر التواصل مع الدعم
• سيتم ربطك مباشرة مع فريق الدعم
• قدم متطلباتك وسنساعدك فوراً

<b>⏰ أوقات الدعم:</b>
• كل أيام الأسبوع
• من 9 صباحاً حتى 6 مساءً
            """
        else:
            support_label = "💬 Contact Support"
            back_label = "🔙 Back"
            description = f"""
We're here to help you with {service_title}!

<b>📞 For inquiries or orders:</b>
• Click the Contact Support button
• You'll be connected directly with our support team
• Provide your requirements and we'll assist you immediately

<b>⏰ Support Hours:</b>
• Every day of the week
• From 9 AM to 6 PM
            """
        
        back_callback = "dev_main" if q.data in ["📈 برمجة المؤشرات", "📈 Indicators", "🤖 برمجة الاكسبيرتات", "🤖 Expert Advisors", "💬 بوتات التليجرام", "💬 Telegram Bots", "🌐 مواقع الويب", "🌐 Web Development"] else "agency_main"
        
        labels = [service_title, support_label, back_label]
        header = build_header_html(service_title, labels, header_emoji=HEADER_EMOJI, arabic_indent=1 if lang == "ar" else 0)
        
        keyboard = [
            [InlineKeyboardButton(support_label, url="https://t.me/Nagyfx")],
            [InlineKeyboardButton(back_label, callback_data=back_callback)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await q.edit_message_text(
                header + f"\n\n{description}",
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=header + f"\n\n{description}",
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        return

    if lang == "ar":
        placeholder = "تم اختيار الخدمة"
        details = "سيتم إضافة التفاصيل قريبًا..."
    else:
        placeholder = "Service selected"
        details = "Details will be added soon..."
    
    labels_for_header = [q.data]
    header_box = build_header_html(placeholder, labels_for_header, header_emoji=HEADER_EMOJI if lang=="ar" else "✨", arabic_indent=1 if lang=="ar" else 0)
    
    if lang == "ar":
        support_label = "💬 التواصل مع الدعم"
        back_label = "🔙 الرجوع"
    else:
        support_label = "💬 Contact Support"
        back_label = "🔙 Back"
    
    keyboard = [
        [InlineKeyboardButton(support_label, url="https://t.me/Nagyfx")],
        [InlineKeyboardButton(back_label, callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await q.edit_message_text(
            header_box + f"\n\n{details}",
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=header_box + f"\n\n{details}",
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

# ===============================
# web_app_message_handler fallback
# ===============================
async def web_app_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    web_app_data = getattr(msg, "web_appData", None) or getattr(msg, "web_app_data", None)
    if not web_app_data:
        return
    try:
        payload = json.loads(web_app_data.data)
    except Exception:
        await msg.reply_text("❌ Invalid data received.")
        return

    name = payload.get("name", "").strip()
    email = payload.get("email", "").strip()
    phone = payload.get("phone", "").strip()
    page_lang = (payload.get("lang") or "").lower()
    lang = "ar" if page_lang not in ("en",) else "en"

    if not name or len(name) < 2:
        await msg.reply_text("⚠️ الاسم قصير جدًا." if lang == "ar" else "⚠️ Name is too short.")
        return
    if not EMAIL_RE.match(email):
        await msg.reply_text("⚠️ البريد الإلكتروني غير صالح." if lang == "ar" else "⚠️ Invalid email address.")
        return
    if not PHONE_RE.match(phone):
        await msg.reply_text("⚠️ رقم الهاتف غير صالح." if lang == "ar" else "⚠️ Invalid phone number.")
        return

    try:
       
        result, subscriber = save_or_update_subscriber(
            name=name,
            email=email,
            phone=phone,
            lang=lang,
            telegram_id=getattr(msg.from_user, "id", None),
            telegram_username=getattr(msg.from_user, "username", None)
        )
    except Exception:
        logger.exception("Error saving subscriber from web_app message fallback")
        result = "error"

    success_msg = ("✅ تم حفظ بياناتك بنجاح! شكراً." if lang == "ar" else "✅ Your data has been saved successfully! Thank you.") if result != "error" else ("⚠️ حدث خطأ أثناء الحفظ." if lang == "ar" else "⚠️ Error while saving.")
    try:
        await msg.reply_text(success_msg)
    except Exception:
        pass

    if lang == "ar":
        header_title = "اختر وسيطك الآن"
        brokers_title = ""
        back_label = "🔙 الرجوع لتداول الفوركس"
        edit_label = "✏️ تعديل بياناتي"
        accounts_label = "👤 بياناتي وحساباتي"
    else:
        header_title = "Choose your broker now"
        brokers_title = ""
        back_label = "🔙 Back to Forex"
        edit_label = "✏️ Edit my data"
        accounts_label = "👤 My Data & Accounts"

    keyboard = [
        [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
         InlineKeyboardButton("🏦 Scope", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
    ]

    user_id = getattr(msg.from_user, "id", None)
    

    keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    try:
        edited = False
        ref = get_form_ref(user_id) if user_id else None
        if ref:
            try:
                await msg.bot.edit_message_text(text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
                edited = True
                clear_form_ref(user_id)
            except Exception:
                logger.exception("Failed to edit form message in fallback path")
        if not edited:
            sent = await msg.reply_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Scope", back_label, accounts_label], header_emoji=HEADER_EMOJI, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
            try:
                if user_id:
                    save_form_ref(user_id, sent.chat_id, sent.message_id, origin="brokers", lang=lang)
            except Exception:
                logger.exception("Could not save form message reference (fallback response).")
    except Exception:
        logger.exception("Failed to send brokers to user (fallback).")

# ===============================
# New: endpoint to receive existing-account form submissions
# ===============================
@app.post("/webapp/existing-account/submit")
async def submit_existing_account(payload: dict = Body(...)):
    try:
        tg_user = payload.get("tg_user") or {}
        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        broker = (payload.get("broker") or "").strip()
        account = (payload.get("account") or "").strip()
        password = (payload.get("password") or "").strip()
        server = (payload.get("server") or "").strip()
        initial_balance = (payload.get("initial_balance") or "").strip()
        current_balance = (payload.get("current_balance") or "").strip()
        withdrawals = (payload.get("withdrawals") or "").strip()
        copy_start_date = (payload.get("copy_start_date") or "").strip()
        agent = (payload.get("agent") or "").strip()
        expected_return = (payload.get("expected_return") or "").strip()
        lang = (payload.get("lang") or "ar").lower()

        required_fields = {
            'broker': broker,
            'account': account,
            'password': password,
            'server': server,
            'initial_balance': initial_balance,
            'current_balance': current_balance,
            'withdrawals': withdrawals,
            'copy_start_date': copy_start_date,
            'agent': agent,
            'expected_return': expected_return
        }
        
        missing_fields = []
        for field_name, field_value in required_fields.items():
            if not field_value:
                missing_fields.append(field_name)
        
        if missing_fields:
            error_message = "Missing required fields: " + ", ".join(missing_fields)
            return JSONResponse(status_code=400, content={"error": error_message})

        if not all([telegram_id, broker, account, password, server]):
            return JSONResponse(status_code=400, content={"error": "Missing required fields."})

        subscriber = get_subscriber_by_telegram_id(telegram_id)
        if not subscriber:
            return JSONResponse(status_code=404, content={"error": "User not found. Please complete registration first."})

        success, _ = save_trading_account(
            subscriber_id=subscriber.id,
            broker_name=broker,
            account_number=account,
            password=password,
            server=server,
            initial_balance=initial_balance,
            current_balance=current_balance,
            withdrawals=withdrawals,
            copy_start_date=copy_start_date,
            agent=agent,
            expected_return=expected_return
        )

        if not success:
            return JSONResponse(status_code=500, content={"error": "Failed to save trading account."})

        ref = get_form_ref(telegram_id)
        
        if ref:
            await refresh_user_accounts_interface(telegram_id, lang, ref["chat_id"], ref["message_id"])
        else:
            if lang == "ar":
                msg_text = "✅ تم تسجيل حساب التداول بنجاح!"
            else:
                msg_text = "✅ Trading account registered successfully!"
            
            try:
                await application.bot.send_message(
                    chat_id=telegram_id, 
                    text=msg_text, 
                    parse_mode="HTML", 
                    disable_web_page_preview=True
                )
            except Exception:
                logger.exception("Failed to send confirmation message")

        return JSONResponse(content={"message": "Saved successfully."})
    except Exception as e:
        logger.exception("Error saving trading account: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

# ===============================
# Handlers registration - CORRECTED ORDER
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("admin", admin_start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_TELEGRAM_IDS), admin_text_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.Regex(r'.*'), web_app_message_handler))
application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast_menu$"))
application.add_handler(CallbackQueryHandler(admin_accounts_menu, pattern="^admin_accounts_menu$"))
application.add_handler(CallbackQueryHandler(admin_settings, pattern="^admin_settings$"))
application.add_handler(CallbackQueryHandler(admin_change_language, pattern="^admin_change_language$"))
application.add_handler(CallbackQueryHandler(admin_set_language, pattern="^admin_lang_"))
application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
application.add_handler(CallbackQueryHandler(admin_accounts_under_review, pattern="^admin_accounts_under_review$"))
application.add_handler(CallbackQueryHandler(admin_individual_message, pattern="^admin_individual_message$"))
application.add_handler(CallbackQueryHandler(admin_exit, pattern="^admin_exit$"))
application.add_handler(CallbackQueryHandler(admin_panel_from_callback, pattern="^admin_main$"))
application.add_handler(CallbackQueryHandler(handle_admin_broadcast, pattern="^admin_broadcast_"))
application.add_handler(CallbackQueryHandler(execute_broadcast, pattern="^admin_confirm_broadcast$"))
application.add_handler(CallbackQueryHandler(handle_admin_cancel, pattern="^admin_cancel_broadcast$"))
application.add_handler(CallbackQueryHandler(handle_admin_back, pattern="^admin_back$"))
application.add_handler(CallbackQueryHandler(handle_admin_actions, pattern="^(activate_account_|reject_account_)"))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(handle_notification_confirmation, pattern="^confirm_notification_"))
application.add_handler(CallbackQueryHandler(admin_update_performances, pattern="^admin_update_performances$"))
application.add_handler(CallbackQueryHandler(admin_reset_sequences, pattern="^admin_reset_sequences$"))
application.add_handler(CallbackQueryHandler(menu_handler))
# ===============================
# Webhook setup
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot is running"}

@app.get("/update-performances")
def update_performances(key: str):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    
    populate_account_performances()
    return {"message": "Performances updated successfully"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        logger.debug("Incoming update: %s", data)
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
        try:
            await application.bot.set_webhook(full_url)
            logger.info(f"✅ Webhook set to {full_url}")
        except Exception:
            logger.exception("Failed to set webhook")
    else:
        logger.warning("⚠️ WEBHOOK_URL or BOT_WEBHOOK_PATH not set; running without webhook setup")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await application.shutdown()
