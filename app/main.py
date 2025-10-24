import os
import re
import json
import logging
import unicodedata
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import urlencode, quote_plus
from datetime import datetime  # ⬅️ أضف هذا
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
from app.db import Base, engine  # ⬅️ استخدم Base من app.db فقط
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
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
    telegram_id = Column(Integer, nullable=True, unique=True)
    lang = Column(String(8), default="ar")
    
    # العلاقة مع حسابات التداول
    trading_accounts = relationship("TradingAccount", back_populates="subscriber", cascade="all, delete-orphan")

class TradingAccount(Base):
    __tablename__ = "trading_accounts"
    id = Column(Integer, primary_key=True, index=True)
    # مفتاح خارجي يرتبط بالـ Subscriber
    subscriber_id = Column(Integer, ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    broker_name = Column(String(100), nullable=False)
    account_number = Column(String(100), nullable=False)
    password = Column(String(100), nullable=False)
    server = Column(String(100), nullable=False)
    created_at = Column(String(50), default=lambda: datetime.now().isoformat())
    
    # العلاقة مع المستخدم
    subscriber = relationship("Subscriber", back_populates="trading_accounts")

Base.metadata.create_all(bind=engine)
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
# FIXED underline length used across all headers (enforced)
FIXED_UNDERLINE_LENGTH = 25

# -------------------------------
# FORM_MESSAGES mapping:
# telegram_id -> dict with chat_id, message_id, origin (callback_data or label), lang (language of that message)
# -------------------------------
FORM_MESSAGES: Dict[int, Dict[str, Any]] = {}

# -------------------------------
# helpers: emoji removal / display width
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
# consistent header builder (all titles use the same system)
# -------------------------------
def build_header_html(
    title: str,
    keyboard_labels: List[str],
    header_emoji: str = HEADER_EMOJI,
    underline_min: int = 20,
    underline_enabled: bool = True,
    underline_char: str = "━",
    arabic_indent: int = 0,
) -> str:
    """
    Unified centered header with perfectly aligned underline of fixed length (20).
    Works for both Arabic (RTL) and English (LTR) titles in Telegram.
    """
    NBSP = "\u00A0"
    RLE = "\u202B"
    PDF = "\u202C"

    # إزالة رموز الاتجاه والتحكم عند القياس
    def _strip_directionals(s: str) -> str:
        return re.sub(r'[\u200E\u200F\u202A-\u202E\u2066-\u2069\u200D\u200C]', '', s)

    # ✨ هنا الجزء الجديد لتثبيت طول العنوان
    MIN_TITLE_WIDTH = 20
    clean_title = remove_emoji(title)
    title_len = display_width(clean_title)
    if title_len < MIN_TITLE_WIDTH:
        extra_spaces = MIN_TITLE_WIDTH - title_len
        left_pad = extra_spaces // 2
        right_pad = extra_spaces - left_pad
        title = f"{' ' * left_pad}{title}{' ' * right_pad}"

    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    # نص العنوان المرئي
    if is_arabic:
        indent = NBSP * arabic_indent
        visible_title = f"{indent}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        visible_title = f"{header_emoji} {title} {header_emoji}"

    # نحسب عرض النص بعد إزالة رموز الاتجاه
    measure_title = _strip_directionals(visible_title)
    title_width = display_width(measure_title)

    # الطول الثابت للخط (لا يتغير أبداً)
    target_width = FIXED_UNDERLINE_LENGTH  # يمكنك أيضًا جعله 20 إن أردت توحيد الطول مع العنوان

    # نحسب الفراغات لتوسيط العنوان
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left

    centered_line = f"{NBSP * pad_left}<b>{visible_title}</b>{NBSP * pad_right}"

    underline_line = ""
    if underline_enabled:
        underline_line = "\n" + (underline_char * target_width)

    return centered_line + underline_line

# -------------------------------
# DB helpers
# -------------------------------
def save_or_update_subscriber(name: str, email: str, phone: str, lang: str = "ar", telegram_id: int = None, telegram_username: str = None) -> Tuple[str, Subscriber]:
    """
    حفظ أو تحديث بيانات المستخدم الأساسية
    يُرجع الحالة وكائن المستخدم
    """
    try:
        db = SessionLocal()
        subscriber = None
        
        if telegram_id:
            subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == telegram_id).first()
            if subscriber:
                # تحديث البيانات الحالية
                subscriber.name = name
                subscriber.email = email
                subscriber.phone = phone
                subscriber.telegram_username = telegram_username
                if lang:
                    subscriber.lang = lang
                db.commit()
                result = "updated"
            else:
                # مستخدم جديد
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
            # بدون telegram_id - مستخدم جديد
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

def save_trading_account(subscriber_id: int, broker_name: str, account_number: str, password: str, server: str) -> bool:
    """حفظ حساب تداول جديد مرتبط بالمستخدم"""
    try:
        db = SessionLocal()
        
        # التحقق من وجود المستخدم
        subscriber = db.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            logger.error(f"Subscriber with id {subscriber_id} not found")
            return False
        
        # إنشاء حساب التداول
        trading_account = TradingAccount(
            subscriber_id=subscriber_id,
            broker_name=broker_name,
            account_number=account_number,
            password=password,
            server=server
        )
        
        db.add(trading_account)
        db.commit()
        db.close()
        return True
        
    except Exception as e:
        logger.exception("Failed to save trading account: %s", e)
        return False

def get_subscriber_by_telegram_id(tg_id: int) -> Optional[Subscriber]:
    """الحصول على بيانات المستخدم مع جميع حسابات التداول المرتبطة"""
    try:
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == tg_id).first()
        db.close()
        return subscriber
    except Exception as e:
        logger.exception("DB lookup failed")
        return None
def get_trading_accounts_by_telegram_id(tg_id: int) -> List[TradingAccount]:
    """الحصول على جميع حسابات التداول للمستخدم"""
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
    """الحصول على بيانات المستخدم مع حسابات التداول في شكل dictionary"""
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
                        "server": acc.server,
                        "created_at": acc.created_at
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
        
def list_subscribers(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        db = SessionLocal()
        rows = db.query(Subscriber).limit(limit).all()
        db.close()
        return [
            {"id": r.id, "name": r.name, "email": r.email, "phone": r.phone, "telegram_username": r.telegram_username, "telegram_id": r.telegram_id, "lang": r.lang}
            for r in rows
        ]
    except Exception as e:
        logger.exception("Failed to list subscribers")
        return []

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
# small helper to send or edit a "congrats / brokers" message and save ref
# -------------------------------
async def present_brokers_for_user(telegram_id: int, header_title: str, brokers_title: str, back_label: str, edit_label: str, lang: str, reply_to_chat_id: Optional[int]=None, reply_to_message_id: Optional[int]=None):
    # labels for width calculation
    ar_already = "بالفعل لدي حساب بالشركة"
    en_already = "I already have an account"
    already_label = ar_already if lang == "ar" else en_already

    labels = ["🏦 Oneroyall", "🏦 Tickmill", back_label, already_label]
    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0)
    keyboard = [
        [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
         InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
    ]

    # ❌ تم إزالة زر التعديل من هنا

    # add "already have account" as callback
    keyboard.append([InlineKeyboardButton(already_label, callback_data="already_has_account")])

    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # try edit existing message if reference exists
    edited = False
    ref = get_form_ref(telegram_id)
    if ref:
        try:
            await application.bot.edit_message_text(text=header + f"\n\n{brokers_title}", chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            edited = True
            clear_form_ref(telegram_id)
        except Exception:
            logger.exception("Failed to edit referenced message in present_brokers_for_user")

    # if not edited, send new message and save its ref
    if not edited:
        try:
            target_chat = telegram_id if telegram_id else reply_to_chat_id
            if target_chat:
                sent = await application.bot.send_message(chat_id=target_chat, text=header + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                try:
                    save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="brokers", lang=lang)
                except Exception:
                    logger.exception("Could not save form message reference after sending congrats.")
        except Exception:
            logger.exception("Failed to send brokers message to user (present_brokers_for_user).")

# ===============================
# /start + menu / language flows
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
    if lang == "ar":
        sections = [("💹 تداول الفوركس", "forex_main"), ("💻 خدمات البرمجة", "dev_main"), ("🤝 طلب وكالة YesFX", "agency_main")]
        title = "الأقسام الرئيسية"
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        sections = [("💹 Forex Trading", "forex_main"), ("💻 Programming Services", "dev_main"), ("🤝 YesFX Partnership", "agency_main")]
        title = "Main Sections"
        back_button = ("🔙 Back to language", "back_language")

    keyboard = [[InlineKeyboardButton(name, callback_data=cb)] for name, cb in sections]
    keyboard.append([InlineKeyboardButton(back_button[0], callback_data=back_button[1])])
    reply_markup = InlineKeyboardMarkup(keyboard)
    labels = [name for name, _ in sections] + [back_button[0]]
    header = build_header_html(title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang == "ar" else 0)
    try:
        await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = "ar" if q.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, context, lang)

# ===============================
# WebApp page (unchanged behavior except small cleanup)
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

# ===============================
# New WebApp: existing-account form (for users who already have a broker account)
# ===============================
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
        "submit": "تسجيل" if is_ar else "Submit",
        "close": "إغلاق" if is_ar else "Close",
        "error": "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server"
    }
    dir_attr = "rtl" if is_ar else "ltr"
    text_align = "right" if is_ar else "left"

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
        input{{width:100%;padding:10px;margin-top:6px;border:1px solid #ccc;border-radius:6px;font-size:16px;}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .small{{font-size:13px;color:#666;text-align:{text_align}}}
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="text-align:{text_align}">{page_title}</h2>
        <label>{labels['broker']}</label>
        <input id="broker" placeholder="Oneroyal / Tickmill" />
        <label>{labels['account']}</label>
        <input id="account" placeholder="123456" />
        <label>{labels['password']}</label>
        <input id="password" type="password" placeholder="••••••••" />
        <label>{labels['server']}</label>
        <input id="server" placeholder="Oneroyal-Live" />
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

        async function submitForm(){{
          const broker = document.getElementById('broker').value.trim();
          const account = document.getElementById('account').value.trim();
          const password = document.getElementById('password').value.trim();
          const server = document.getElementById('server').value.trim();
          if(!broker || !account || !password || !server){{
            statusEl.textContent = '{ "يرجى ملء جميع الحقول" if is_ar else "Please fill all fields" }';
            return;
          }}
          const initUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user : null;
          const payload = {{broker,account,password,server,tg_user:initUser,lang:"{lang}"}};
          try{{
            const resp = await fetch(window.location.origin + '/webapp/existing-account/submit', {{
              method:'POST',
              headers:{{'Content-Type':'application/json'}},
              body:JSON.stringify(payload)
            }});
            const data = await resp.json();
            if(resp.ok){{
              statusEl.style.color='green';
              statusEl.textContent=data.message||'تم الحفظ بنجاح';
              setTimeout(()=>{{try{{tg.close();}}catch(e){{}}}},700);
              try{{tg.sendData(JSON.stringify({{status:'sent',type:'existing_account'}}));}}catch(e){{}}
            }}else{{
              statusEl.textContent=data.error||'{labels["error"]}';
            }}
          }}catch(e){{
            statusEl.textContent='{labels["error"]}: '+e.message;
          }}
        }}
        document.getElementById('submit').addEventListener('click',submitForm);
        document.getElementById('close').addEventListener('click',()=>{{try{{tg.close();}}catch(e){{}}}});
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

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

        # validation
        if not name or len(name) < 2:
            return JSONResponse(status_code=400, content={"error": "Name too short or missing."})
        if not EMAIL_RE.match(email):
            return JSONResponse(status_code=400, content={"error": "Invalid email."})
        if not PHONE_RE.match(phone):
            return JSONResponse(status_code=400, content={"error": "Invalid phone."})

        # determine language from payload if explicitly provided, else fallback to tg_user language
        detected_lang = None
        if page_lang in ("ar", "en"):
            detected_lang = page_lang
        else:
            lang_code = tg_user.get("language_code") if isinstance(tg_user, dict) else None
            detected_lang = "en" if (lang_code and str(lang_code).startswith("en")) else "ar"

        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        telegram_username = tg_user.get("username") if isinstance(tg_user, dict) else None

        # ⬅️ التصحيح هنا: استقبل كلا القيمتين من الدالة
        result, subscriber = save_or_update_subscriber(
            name=name, 
            email=email, 
            phone=phone, 
            lang=detected_lang, 
            telegram_id=telegram_id, 
            telegram_username=telegram_username
        )

        # Determine the display language for the congrats screen:
        display_lang = detected_lang
        ref = get_form_ref(telegram_id) if telegram_id else None
        if page_lang in ("ar", "en"):
            display_lang = page_lang
        elif ref and ref.get("lang"):
            display_lang = ref.get("lang")
        else:
            display_lang = detected_lang

        # Prepare congrats strings based on display_lang
        if display_lang == "ar":
            header_title = "🎉 مبروك — اختر وسيطك الآن"
            brokers_title = ""
            back_label = "🔙 الرجوع لتداول الفوركس"
            edit_label = "✏️ تعديل بياناتي"
        else:
            header_title = "🎉 Congrats — Choose your broker now"
            brokers_title = ""
            back_label = "🔙 Back to Forex"
            edit_label = "✏️ Edit my data"

        # Build keyboard for the message (❌ إزالة زر التعديل من هنا)
        ar_already = "بالفعل لدي حساب بالشركة"
        en_already = "I already have an account"
        already_label = ar_already if display_lang == "ar" else en_already

        keyboard = [
            [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
             InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
        ]

        # ❌ تم إزالة زر التعديل من هنا

        keyboard.append([InlineKeyboardButton(already_label, callback_data="already_has_account")])
        keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Try to edit original form message if we have reference (and prefer to edit)
        edited = False
        if telegram_id and ref:
            try:
                await application.bot.edit_message_text(
                    text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label], 
                    header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, 
                    arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}",
                    chat_id=ref["chat_id"], 
                    message_id=ref["message_id"],
                    reply_markup=reply_markup, 
                    parse_mode="HTML", 
                    disable_web_page_preview=True
                )
                edited = True
                clear_form_ref(telegram_id)
            except Exception:
                logger.exception("Failed to edit original form message; will send a fallback message.")

        if not edited:
            if telegram_id:
                try:
                    sent = await application.bot.send_message(
                        chat_id=telegram_id, 
                        text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label], 
                        header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, 
                        arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", 
                        reply_markup=reply_markup, 
                        parse_mode="HTML", 
                        disable_web_page_preview=True
                    )
                    # save reference for future edits
                    save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
                except Exception:
                    logger.exception("Failed to send congrats message to user.")
            else:
                logger.info("No telegram_id available from WebApp payload; skipping Telegram notification.")

        # ⬅️ التصحيح هنا: استخدم result الصحيح
        if result == "created":
            return JSONResponse(content={"message": "Saved successfully."})
        elif result == "updated":
            return JSONResponse(content={"message": "Updated successfully."})
        else:
            return JSONResponse(content={"message": "Saved (unknown state)."})
    except Exception as e:
        logger.exception("Error in webapp_submit: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

# ===============================
# menu_handler
# ===============================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    # prefer current context language if available, else default to 'ar'
    lang = context.user_data.get("lang", "ar")

    # handle "already has account" callback by opening WebApp existing-account form
    if q.data == "already_has_account":
        # open WebApp form for existing account if we have WEBAPP_URL
        if WEBAPP_URL:
            url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
            open_label = "🧾 تسجيل بيانات حسابي" if lang == "ar" else "🧾 Register My Account"
            back_label = "🔙 الرجوع لتداول الفوركس" if lang == "ar" else "🔙 Back to Forex"
            
            # ✅ الحفاظ على زر تعديل البيانات في شاشة "لدي حساب بالفعل"
            edit_label = "✏️ تعديل بياناتي" if lang == "ar" else "✏️ Edit my data"
            subscriber = get_subscriber_by_telegram_id(user_id)
            if subscriber and WEBAPP_URL:
                params = {
                    "lang": lang,
                    "edit": "1",
                    "name": subscriber.name,
                    "email": subscriber.email,
                    "phone": subscriber.phone
                }
                edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
            
            labels = [open_label, edit_label, back_label]
            header = build_header_html("بيانات الحساب" if lang == "ar" else "Account Details", labels, header_emoji=HEADER_EMOJI, underline_enabled=True, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang == "ar" else 0)
            keyboard = [
                [InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))],
            ]
            
            # ✅ إضافة زر تعديل البيانات إذا كان المستخدم مسجلاً
            if subscriber:
                keyboard.append([InlineKeyboardButton(edit_label, web_app=WebAppInfo(url=edit_url))])
                
            keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="existing_account", lang=lang)
            except Exception:
                try:
                    await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                    save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="existing_account", lang=lang)
                except Exception:
                    logger.exception("Failed to show existing-account webapp button to user.")
        else:
            # fallback: respond with text and keep previous behavior
            display_lang = lang
            if display_lang == "ar":
                text = "✅ تم تسجيل أنك لديك حساب بالفعل لدى الوسيط. شكرًا لك!"
                back_label = "🔙 الرجوع لتداول الفوركس"
            else:
                text = "✅ Noted — you already have an account with the broker. Thank you!"
                back_label = "🔙 Back to Forex"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(back_label, callback_data="forex_main")]])
            try:
                await q.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                try:
                    await context.bot.send_message(chat_id=q.message.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                except Exception:
                    logger.exception("Failed to respond to already_has_account action")
        return

    # عرض بيانات المستخدم وحسابات التداول
    if q.data == "my_accounts":
        await show_user_accounts(update, context, user_id, lang)
        return

    # إضافة حساب تداول جديد - ❌ فتح النموذج مباشرة
    if q.data == "add_trading_account":
        if WEBAPP_URL:
            url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
            
            # ❌ فتح النموذج مباشرة بدون رسالة وسيطة
            try:
                await q.edit_message_text(
                    "⏳ جاري فتح نموذج إضافة الحساب..." if lang == "ar" else "⏳ Opening account form...",
                    parse_mode="HTML"
                )
                # إرسال رسالة مع زر لفتح النموذج
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

    # تعديل بيانات المستخدم الأساسية - ❌ فتح النموذج مباشرة
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
            
            # ❌ فتح النموذج مباشرة بدون رسالة وسيطة
            try:
                await q.edit_message_text(
                    "⏳ جاري فتح نموذج التعديل..." if lang == "ar" else "⏳ Opening edit form...",
                    parse_mode="HTML"
                )
                # إرسال رسالة مع زر لفتح النموذج
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

    # mapping for sections - التعديل هنا: إزالة "بياناتي وحساباتي" من قسم الفوركس
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

    # If user clicked section entry (forex_main, dev_main, agency_main)
    if q.data in sections_data:
        data = sections_data[q.data]
        options = data[lang]
        title = data[f"title_{lang}"]
        back_label = "🔙 الرجوع للقائمة الرئيسية" if lang == "ar" else "🔙 Back to main menu"
        labels = options + [back_label]
        header_emoji_for_lang = HEADER_EMOJI if lang == "ar" else "✨"
        box = build_header_html(title, labels, header_emoji=header_emoji_for_lang, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0)
        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await q.edit_message_text(box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            # save ref so that forms opened from here can edit this same message later
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin=q.data, lang=lang)
        except Exception:
            await context.bot.send_message(chat_id=q.message.chat_id, text=box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    # If user clicked "Copy Trading" (or its Arabic label), handle registration flow
    if q.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
        # check persistent registration
        existing = get_subscriber_by_telegram_id(user_id)
        if existing:
            # prefer current interface language (context.user_data) over DB stored lang
            display_lang = context.user_data.get("lang") or existing.lang or "ar"
            if display_lang == "ar":
                header_title = "🎉 مبروك — اختر وسيطك الآن"
                brokers_title = ""
                back_label = "🔙 الرجوع لتداول الفوركس"
                edit_label = "✏️ تعديل بياناتي"
                accounts_label = "👤 بياناتي وحساباتي"
            else:
                header_title = "🎉 Congrats — Choose your broker now"
                brokers_title = ""
                back_label = "🔙 Back to Forex"
                edit_label = "✏️ Edit my data"
                accounts_label = "👤 My Data & Accounts"

            ar_already = "بالفعل لدي حساب بالشركة"
            en_already = "I already have an account"
            already_label = ar_already if display_lang == "ar" else en_already

            # create keyboard (❌ إزالة زر التعديل من هنا)
            keyboard = [
                [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
                 InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
            ]

            # ❌ تم إزالة زر التعديل من هنا

            keyboard.append([InlineKeyboardButton(already_label, callback_data="already_has_account")])
            keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
            keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                await q.edit_message_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                # Save reference for future edits (so edit button can return to this message)
                save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="brokers", lang=display_lang)
            except Exception:
                # fallback: send new message and save its reference
                try:
                    sent = await context.bot.send_message(chat_id=q.message.chat_id, text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                    save_form_ref(user_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
                except Exception:
                    logger.exception("Failed to show congrats screen for already-registered user.")
            return

        # not registered -> show WebApp button (open form)
        context.user_data["registration"] = {"lang": lang}
        if lang == "ar":
            title = "من فضلك ادخل البيانات"
            back_label_text = "🔙 الرجوع لتداول الفوركس"
            open_label = "📝 افتح نموذج التسجيل"
            header_emoji_for_lang = HEADER_EMOJI
        else:
            title = "Please enter your data"
            back_label_text = "🔙 Back to Forex"
            open_label = "📝 Open registration form"
            header_emoji_for_lang = "✨"

        labels = [open_label, back_label_text]
        header = build_header_html(title, labels, header_emoji=header_emoji_for_lang, underline_enabled=True, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang == "ar" else 0)

        keyboard = []
        if WEBAPP_URL:
            url_with_lang = f"{WEBAPP_URL}?lang={lang}"
            keyboard.append([InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))])
        else:
            fallback_text = "فتح النموذج" if lang == "ar" else "Open form"
            keyboard.append([InlineKeyboardButton(fallback_text, callback_data="fallback_open_form")])

        keyboard.append([InlineKeyboardButton(back_label_text, callback_data="forex_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await q.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="open_form", lang=lang)
        except Exception:
            try:
                sent = await context.bot.send_message(chat_id=q.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                save_form_ref(user_id, sent.chat_id, sent.message_id, origin="open_form", lang=lang)
            except Exception:
                logger.exception("Failed to show webapp button to user.")
        return

    # If user clicked "My Data & Accounts" or "بياناتي وحساباتي"
    if q.data in ("👤 بياناتي وحساباتي", "👤 My Data & Accounts"):
        await show_user_accounts(update, context, user_id, lang)
        return

    # fallback: generic selected service
    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    # Use build_header_html to ensure unified header formatting (fixed underline length enforced)
    labels_for_header = [q.data]
    header_box = build_header_html(placeholder, labels_for_header, header_emoji=HEADER_EMOJI if lang=="ar" else "✨", underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0)
    try:
        await q.edit_message_text(header_box + f"\n\n{details}", parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=q.message.chat_id, text=header_box + f"\n\n{details}", disable_web_page_preview=True)
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
        # ⬅️ التصحيح هنا: استقبل كلا القيمتين
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

    # prepare brokers screen (allow editing)
    if lang == "ar":
        header_title = "🎉 مبروك — اختر وسيطك الآن"
        brokers_title = ""
        back_label = "🔙 الرجوع لتداول الفوركس"
        edit_label = "✏️ تعديل بياناتي"
    else:
        header_title = "🎉 Congrats — Choose your broker now"
        brokers_title = ""
        back_label = "🔙 Back to Forex"
        edit_label = "✏️ Edit my data"

    ar_already = "بالفعل لدي حساب بالشركة"
    en_already = "I already have an account"
    already_label = ar_already if lang == "ar" else en_already

    keyboard = [
        [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
         InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
    ]

    user_id = getattr(msg.from_user, "id", None)
    # ❌ إزالة زر التعديل من هنا أيضاً
    # if WEBAPP_URL and user_id:
    #     params = {"lang": lang, "edit": "1", "name": name, "email": email, "phone": phone}
    #     url_with_prefill = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
    #     keyboard.append([InlineKeyboardButton(edit_label, web_app=WebAppInfo(url=url_with_prefill))])

    keyboard.append([InlineKeyboardButton(already_label, callback_data="already_has_account")])

    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    try:
        edited = False
        ref = get_form_ref(user_id) if user_id else None
        if ref:
            try:
                await msg.bot.edit_message_text(text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
                edited = True
                clear_form_ref(user_id)
            except Exception:
                logger.exception("Failed to edit form message in fallback path")
        if not edited:
            sent = await msg.reply_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, already_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
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
        lang = (payload.get("lang") or "ar").lower()

        if not all([telegram_id, broker, account, password, server]):
            return JSONResponse(status_code=400, content={"error": "Missing fields."})

        # البحث عن المستخدم أولاً
        subscriber = get_subscriber_by_telegram_id(telegram_id)
        if not subscriber:
            return JSONResponse(status_code=404, content={"error": "User not found. Please complete registration first."})

        # حفظ حساب التداول
        success = save_trading_account(
            subscriber_id=subscriber.id,
            broker_name=broker,
            account_number=account,
            password=password,
            server=server
        )

        if not success:
            return JSONResponse(status_code=500, content={"error": "Failed to save trading account."})

        # إرسال رسالة التأكيد
        ref = get_form_ref(telegram_id)
        msg_text = "✅ تم تسجيل الحساب بنجاح! يمكنك إضافة المزيد من الحسابات." if lang == "ar" else "✅ Account registered successfully! You can add more accounts."
        back_label = "🔙 الرجوع لتداول الفوركس" if lang == "ar" else "🔙 Back to Forex"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(back_label, callback_data="forex_main")]])

        if ref:
            try:
                await application.bot.edit_message_text(
                    chat_id=ref["chat_id"],
                    message_id=ref["message_id"],
                    text=msg_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                clear_form_ref(telegram_id)
            except Exception:
                logger.exception("Failed to edit user message after trading account save")
                try:
                    await application.bot.send_message(chat_id=telegram_id, text=msg_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                except Exception:
                    logger.exception("Failed to send fallback confirmation")

        return JSONResponse(content={"message": "Saved successfully."})
    except Exception as e:
        logger.exception("Error saving trading account: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

async def show_user_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, lang: str):
    """عرض بيانات المستخدم مع جميع حسابات التداول - بنفس تنسيق صفحة 'تداول الفوركس'"""
    user_data = get_subscriber_with_accounts(telegram_id)
    
    if not user_data:
        # إذا لم يكن مسجلاً، نطلب التسجيل
        if lang == "ar":
            text = "⚠️ لم تقم بالتسجيل بعد. يرجى التسجيل أولاً."
        else:
            text = "⚠️ You haven't registered yet. Please register first."
        await update.callback_query.edit_message_text(text)
        return

    # بناء رسالة العرض بنفس تنسيق صفحة تداول الفوركس
    if lang == "ar":
        header_title = "بياناتي وحساباتي"
        
        # نفس الأزرار المستخدمة في صفحة "بالفعل لدي حساب"
        open_label = "🧾 تسجيل بيانات حسابي"
        edit_label = "✏️ تعديل بياناتي"
        back_label = "🔙 الرجوع لتداول الفوركس"
        button_labels = [open_label, edit_label, back_label]
        
        # استخدام التنسيق الموحد للعناوين بنفس الطريقة
        header = build_header_html(
            header_title, 
            button_labels, 
            header_emoji=HEADER_EMOJI,
            underline_min=FIXED_UNDERLINE_LENGTH,
            arabic_indent=1
        )
        
        # بناء محتوى الرسالة
        user_info = f"👤 <b>الاسم:</b> {user_data['name']}\n📧 <b>البريد:</b> {user_data['email']}\n📞 <b>الهاتف:</b> {user_data['phone']}"
        accounts_header = "\n🏦 <b>حسابات التداول:</b>"
        no_accounts = "\nلا توجد حسابات مسجلة بعد."
        
    else:
        header_title = "My Data & Accounts"
        
        # نفس الأزرار المستخدمة في صفحة "بالفعل لدي حساب"
        open_label = "🧾 Register My Account"
        edit_label = "✏️ Edit my data"
        back_label = "🔙 Back to Forex"
        button_labels = [open_label, edit_label, back_label]
        
        # استخدام التنسيق الموحد للعناوين بنفس الطريقة
        header = build_header_html(
            header_title, 
            button_labels, 
            header_emoji=HEADER_EMOJI,
            underline_min=FIXED_UNDERLINE_LENGTH,
            arabic_indent=0
        )
        
        # بناء محتوى الرسالة
        user_info = f"👤 <b>Name:</b> {user_data['name']}\n📧 <b>Email:</b> {user_data['email']}\n📞 <b>Phone:</b> {user_data['phone']}"
        accounts_header = "\n🏦 <b>Trading Accounts:</b>"
        no_accounts = "\nNo trading accounts registered yet."

    # بناء الرسالة الكاملة
    message = f"{header}\n\n{user_info}{accounts_header}"
    
    if user_data['trading_accounts']:
        for i, acc in enumerate(user_data['trading_accounts'], 1):
            if lang == "ar":
                message += f"\n\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}"
            else:
                message += f"\n\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}"
    else:
        message += f"{no_accounts}"

    # أزرار الإجراءات - نفس أزرار صفحة "بالفعل لدي حساب"
    keyboard = []
    
    # زر تسجيل حساب جديد
    if WEBAPP_URL:
        url_with_lang = f"{WEBAPP_URL}/existing-account?lang={lang}"
        keyboard.append([InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))])
    
    # زر تعديل البيانات الأساسية
    if WEBAPP_URL:
        params = {
            "lang": lang,
            "edit": "1",
            "name": user_data['name'],
            "email": user_data['email'],
            "phone": user_data['phone']
        }
        edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
        keyboard.append([InlineKeyboardButton(edit_label, web_app=WebAppInfo(url=edit_url))])
    
    # زر الرجوع
    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.edit_message_text(
            message, 
            reply_markup=reply_markup, 
            parse_mode="HTML", 
            disable_web_page_preview=True
        )
    except Exception:
        # في حالة فشل التعديل، إرسال رسالة جديدة
        await context.bot.send_message(
            chat_id=telegram_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
# ===============================
# Handlers registration
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))
application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.Regex(r'.*'), web_app_message_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: None))

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
