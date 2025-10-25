import os
import re
import json
import logging
import unicodedata
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import urlencode, quote_plus
from datetime import datetime 
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
    trading_accounts = relationship("TradingAccount", back_populates="subscriber", cascade="all, delete-orphan")

class TradingAccount(Base):
    __tablename__ = "trading_accounts"
    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    broker_name = Column(String(100), nullable=False)
    account_number = Column(String(100), nullable=False)
    password = Column(String(100), nullable=False)
    server = Column(String(100), nullable=False)
    # الحقول الجديدة
    initial_balance = Column(String(50), nullable=True)
    current_balance = Column(String(50), nullable=True)
    withdrawals = Column(String(50), nullable=True)
    copy_start_date = Column(String(50), nullable=True)
    agent = Column(String(100), nullable=True)
    created_at = Column(String(50), default=lambda: datetime.now().isoformat())
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
FIXED_UNDERLINE_LENGTH = 25
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

    def _strip_directionals(s: str) -> str:
        return re.sub(r'[\u200E\u200F\u202A-\u202E\u2066-\u2069\u200D\u200C]', '', s)

    MIN_TITLE_WIDTH = 20
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
    target_width = FIXED_UNDERLINE_LENGTH
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
    agent: str = None
) -> bool:
    """حفظ حساب تداول جديد مرتبط بالمستخدم"""
    try:
        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            logger.error(f"Subscriber with id {subscriber_id} not found")
            return False
        
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
            agent=agent
        )
        
        db.add(trading_account)
        db.commit()
        db.close()
        return True
        
    except Exception as e:
        logger.exception("Failed to save trading account: %s", e)
        return False

def update_trading_account(account_id: int, **kwargs) -> bool:
    """تحديث بيانات حساب تداول موجود"""
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
            db.close()
            return False
        
        for key, value in kwargs.items():
            if hasattr(account, key) and value is not None:
                setattr(account, key, value)
        
        db.commit()
        db.close()
        return True
    except Exception as e:
        logger.exception("Failed to update trading account: %s", e)
        return False

def delete_trading_account(account_id: int) -> bool:
    """حذف حساب تداول"""
    try:
        db = SessionLocal()
        account = db.query(TradingAccount).filter(TradingAccount.id == account_id).first()
        if not account:
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
                        "password": acc.password,
                        "server": acc.server,
                        "initial_balance": acc.initial_balance,
                        "current_balance": acc.current_balance,
                        "withdrawals": acc.withdrawals,
                        "copy_start_date": acc.copy_start_date,
                        "agent": acc.agent,
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
    accounts_label = "👤 بياناتي وحساباتي" if lang == "ar" else "👤 My Data & Accounts"

    labels = ["🏦 Oneroyall", "🏦 Tickmill", back_label, accounts_label]  # ⬅️ إزالة already_label
    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0)
    keyboard = [
        [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
         InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
    ]

    keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])

    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    edited = False
    ref = get_form_ref(telegram_id)
    if ref:
        try:
            await application.bot.edit_message_text(text=header + f"\n\n{brokers_title}", chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            edited = True
            clear_form_ref(telegram_id)
        except Exception:
            logger.exception("Failed to edit referenced message in present_brokers_for_user")
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
        #sections = [("💹 تداول الفوركس", "forex_main"), ("💻 خدمات البرمجة", "dev_main"), ("🤝 طلب وكالة YesFX", "agency_main")]
        sections = [("💹 تداول الفوركس", "forex_main")]
        title = "الأقسام الرئيسية"
        back_button = ("🔙 الرجوع للغة", "back_language")
    else:
        #sections = [("💹 Forex Trading", "forex_main"), ("💻 Programming Services", "dev_main"), ("🤝 YesFX Partnership", "agency_main")]
        sections = [("💹 Forex Trading", "forex_main")]
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
        "initial_balance": "رصيد البداية" if is_ar else "Initial Balance",
        "current_balance": "الرصيد الحالي" if is_ar else "Current Balance",
        "withdrawals": "المسحوبات" if is_ar else "Withdrawals",
        "copy_start_date": "تاريخ بدء النسخ" if is_ar else "Copy Start Date",
        "agent": "الوكيل" if is_ar else "Agent",
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
        input, select{{width:100%;padding:10px;margin-top:6px;border:1px solid #ccc;border-radius:6px;font-size:16px;}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .small{{font-size:13px;color:#666;text-align:{text_align}}}
        .form-row{{display:flex;gap:10px;margin-top:10px;}}
        .form-row > div{{flex:1;}}
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="text-align:{text_align}">{page_title}</h2>
        
        <label>{labels['broker']}</label>
        <select id="broker">
          <option value="">{ 'اختر الشركة' if is_ar else 'Select Broker' }</option>
          <option value="Oneroyal">Oneroyal</option>
          <option value="Tickmill">Tickmill</option>
        </select>

        <div class="form-row">
          <div>
            <label>{labels['account']}</label>
            <input id="account" placeholder="123456" />
          </div>
          <div>
            <label>{labels['password']}</label>
            <input id="password" type="password" placeholder="••••••••" />
          </div>
        </div>

        <label>{labels['server']}</label>
        <input id="server" placeholder="Oneroyal-Live" />

        <div class="form-row">
          <div>
            <label>{labels['initial_balance']}</label>
            <input id="initial_balance" type="number" placeholder="0.00" step="0.01" />
          </div>
          <div>
            <label>{labels['current_balance']}</label>
            <input id="current_balance" type="number" placeholder="0.00" step="0.01" />
          </div>
        </div>

        <div class="form-row">
          <div>
            <label>{labels['withdrawals']}</label>
            <input id="withdrawals" type="number" placeholder="0.00" step="0.01" />
          </div>
          <div>
            <label>{labels['copy_start_date']}</label>
            <input id="copy_start_date" type="date" />
          </div>
        </div>

        <label>{labels['agent']}</label>
        <select id="agent">
          <option value="">{ 'اختر الوكيل' if is_ar else 'Select Agent' }</option>
          <option value="ملك الدهب">ملك الدهب</option>
        </select>

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
          const initial_balance = document.getElementById('initial_balance').value.trim();
          const current_balance = document.getElementById('current_balance').value.trim();
          const withdrawals = document.getElementById('withdrawals').value.trim();
          const copy_start_date = document.getElementById('copy_start_date').value.trim();
          const agent = document.getElementById('agent').value.trim();

          if(!broker || !account || !password || !server){{
            statusEl.textContent = '{ "يرجى ملئ جميع الحقول الأساسية" if is_ar else "Please fill all required fields" }';
            return;
          }}

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
            tg_user: initUser,
            lang:"{lang}"
          }};

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
# New WebApp: edit-accounts form
# ===============================
# ===============================
# New WebApp: edit-accounts form - FIXED VERSION
# ===============================
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
        "save": "حفظ التغييرات" if is_ar else "Save Changes",
        "delete": "حذف الحساب" if is_ar else "Delete Account",
        "close": "إغلاق" if is_ar else "Close",
        "error": "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server",
        "no_accounts": "لا توجد حسابات" if is_ar else "No accounts found"
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
        input, select{{width:100%;padding:10px;margin-top:6px;border:1px solid #ccc;border-radius:6px;font-size:16px;}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-danger{{background:#FF4500;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc}}
        .small{{font-size:13px;color:#666;text-align:{text_align}}}
        .form-row{{display:flex;gap:10px;margin-top:10px;}}
        .form-row > div{{flex:1;}}
        .hidden{{display:none;}}
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="text-align:{text_align}">{page_title}</h2>
        
        <label>{labels['select_account']}</label>
        <select id="account_select">
          <option value="">{ 'جاري التحميل...' if is_ar else 'Loading...' }</option>
        </select>

        <!-- إضافة حقل مخفي لتخزين معرف الحساب الحالي -->
        <input type="hidden" id="current_account_id" value="">

        <label>{labels['broker']}</label>
        <select id="broker">
          <option value="">{ 'اختر الشركة' if is_ar else 'Select Broker' }</option>
          <option value="Oneroyal">Oneroyal</option>
          <option value="Tickmill">Tickmill</option>
        </select>

        <div class="form-row">
          <div>
            <label>{labels['account']}</label>
            <input id="account" placeholder="123456" />
          </div>
          <div>
            <label>{labels['password']}</label>
            <input id="password" type="password" placeholder="••••••••" />
          </div>
        </div>

        <label>{labels['server']}</label>
        <input id="server" placeholder="Oneroyal-Live" />

        <div class="form-row">
          <div>
            <label>{labels['initial_balance']}</label>
            <input id="initial_balance" type="number" placeholder="0.00" step="0.01" />
          </div>
          <div>
            <label>{labels['current_balance']}</label>
            <input id="current_balance" type="number" placeholder="0.00" step="0.01" />
          </div>
        </div>

        <div class="form-row">
          <div>
            <label>{labels['withdrawals']}</label>
            <input id="withdrawals" type="number" placeholder="0.00" step="0.01" />
          </div>
          <div>
            <label>{labels['copy_start_date']}</label>
            <input id="copy_start_date" type="date" />
          </div>
        </div>

        <label>{labels['agent']}</label>
        <select id="agent">
          <option value="">{ 'اختر الوكيل' if is_ar else 'Select Agent' }</option>
          <option value="ملك الدهب">ملك الدهب</option>
        </select>

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
        let currentAccountId = null;

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
              option.textContent = `${{acc.broker_name}} - ${{acc.account_number}}`;
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
          document.getElementById('save').disabled = false;
          document.getElementById('delete').disabled = false;
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
          document.getElementById('current_account_id').value = '';
          currentAccountId = null;
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
              // تعيين معرف الحساب الحالي
              currentAccountId = acc.id;
              document.getElementById('current_account_id').value = acc.id;
              
              // تعبئة الحقول بالبيانات
              document.getElementById('broker').value = acc.broker_name || '';
              document.getElementById('account').value = acc.account_number || '';
              document.getElementById('password').value = acc.password || '';
              document.getElementById('server').value = acc.server || '';
              document.getElementById('initial_balance').value = acc.initial_balance || '';
              document.getElementById('current_balance').value = acc.current_balance || '';
              document.getElementById('withdrawals').value = acc.withdrawals || '';
              document.getElementById('copy_start_date').value = acc.copy_start_date || '';
              document.getElementById('agent').value = acc.agent || '';
              
              // تمكين النموذج
              enableForm();
              
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
          
          if (!accountId) {{
            statusEl.textContent = '{ "يرجى اختيار حساب أولاً" if is_ar else "Please select an account first" }';
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
            tg_user: tg.initDataUnsafe.user,
            lang: "{lang}"
          }};

          // التحقق من الحقول المطلوبة
          if (!payload.broker_name || !payload.account_number || !payload.password || !payload.server) {{
            statusEl.textContent = '{ "يرجى ملء جميع الحقول المطلوبة" if is_ar else "Please fill all required fields" }';
            return;
          }}

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
              statusEl.style.color = '#b00';
              statusEl.textContent = data.detail || '{labels["error"]}';
            }}
          }} catch (e) {{
            statusEl.style.color = '#b00';
            statusEl.textContent = '{labels["error"]}: ' + e.message;
          }}
        }}

        // دالة لحذف الحساب
        async function deleteAccount() {{
          const accountId = document.getElementById('current_account_id').value;
          
          if (!accountId) {{
            statusEl.textContent = '{ "يرجى اختيار حساب أولاً" if is_ar else "Please select an account first" }';
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
              statusEl.style.color = '#b00';
              statusEl.textContent = data.detail || '{labels["error"]}';
            }}
          }} catch (e) {{
            statusEl.style.color = '#b00';
            statusEl.textContent = '{labels["error"]}: ' + e.message;
          }}
        }}

        // تهيئة الصفحة
        document.addEventListener('DOMContentLoaded', function() {{
          // تحميل الحسابات أولاً
          loadAccounts();
          
          // تعطيل النموذج في البداية
          disableForm();
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

        # Validate ownership
        accounts = get_trading_accounts_by_telegram_id(telegram_id)
        if not any(acc.id == account_id for acc in accounts):
            raise HTTPException(status_code=403, detail="Account not owned by user")

        # Remove non-updatable fields
        update_data = {k: v for k, v in payload.items() if k not in ["id", "tg_user", "lang", "created_at"]}

        success = update_trading_account(account_id, **update_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update account")

        # Update the message in Telegram
        ref = get_form_ref(telegram_id)
        if ref and ref.get("origin") == "my_accounts":
            updated_data = get_subscriber_with_accounts(telegram_id)
            if updated_data:
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
                    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1)
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
                    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=0)
                    user_info = f"👤 <b>Name:</b> {updated_data['name']}\n📧 <b>Email:</b> {updated_data['email']}\n📞 <b>Phone:</b> {updated_data['phone']}"
                    accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
                    no_accounts = "\nNo trading accounts registered yet."

                updated_message = f"{header}\n\n{user_info}{accounts_header}\n"
                
                if updated_data['trading_accounts']:
                    for i, acc in enumerate(updated_data['trading_accounts'], 1):
                        if lang == "ar":
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
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
                        else:
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
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
                        chat_id=ref["chat_id"],
                        message_id=ref["message_id"],
                        text=updated_message,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    save_form_ref(telegram_id, ref["chat_id"], ref["message_id"], origin="my_accounts", lang=lang)
                except Exception as e:
                    logger.exception(f"Failed to edit message after update: {e}")

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

        # Validate ownership
        accounts = get_trading_accounts_by_telegram_id(telegram_id)
        if not any(acc.id == account_id for acc in accounts):
            raise HTTPException(status_code=403, detail="Account not owned by user")

        success = delete_trading_account(account_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete account")

        # Update the message in Telegram
        ref = get_form_ref(telegram_id)
        if ref and ref.get("origin") == "my_accounts":
            updated_data = get_subscriber_with_accounts(telegram_id)
            if updated_data:
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
                    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1)
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
                    header = build_header_html(header_title, labels, header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=0)
                    user_info = f"👤 <b>Name:</b> {updated_data['name']}\n📧 <b>Email:</b> {updated_data['email']}\n📞 <b>Phone:</b> {updated_data['phone']}"
                    accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
                    no_accounts = "\nNo trading accounts registered yet."

                updated_message = f"{header}\n\n{user_info}{accounts_header}\n"
                
                if updated_data['trading_accounts']:
                    for i, acc in enumerate(updated_data['trading_accounts'], 1):
                        if lang == "ar":
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
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
                        else:
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
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
                        chat_id=ref["chat_id"],
                        message_id=ref["message_id"],
                        text=updated_message,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    save_form_ref(telegram_id, ref["chat_id"], ref["message_id"], origin="my_accounts", lang=lang)
                except Exception as e:
                    logger.exception(f"Failed to edit message after delete: {e}")

        return {"success": True}
    except Exception as e:
        logger.exception(f"Error in api_delete_trading_account: {e}")
        raise HTTPException(status_code=500, detail="Server error")

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

        # determine language
        detected_lang = None
        if page_lang in ("ar", "en"):
            detected_lang = page_lang
        else:
            lang_code = tg_user.get("language_code") if isinstance(tg_user, dict) else None
            detected_lang = "en" if (lang_code and str(lang_code).startswith("en")) else "ar"

        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        telegram_username = tg_user.get("username") if isinstance(tg_user, dict) else None

        result, subscriber = save_or_update_subscriber(
            name=name, 
            email=email, 
            phone=phone, 
            lang=detected_lang, 
            telegram_id=telegram_id, 
            telegram_username=telegram_username
        )

        is_edit_mode = payload.get("edit") == "1" or "edit" in (payload.get("params") or {})
        ref = get_form_ref(telegram_id) if telegram_id else None
        if ref and ref.get("origin") == "my_accounts" and (is_edit_mode or result == "updated"):
            updated_data = get_subscriber_with_accounts(telegram_id)
            
            if updated_data:
                lang = detected_lang
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
                    header = build_header_html(
                        header_title, 
                        labels,
                        header_emoji=HEADER_EMOJI,
                        underline_min=FIXED_UNDERLINE_LENGTH,
                        arabic_indent=1
                    )
                    
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
                    header = build_header_html(
                        header_title, 
                        labels,
                        header_emoji=HEADER_EMOJI,
                        underline_min=FIXED_UNDERLINE_LENGTH,
                        arabic_indent=0
                    )
                    
                    user_info = f"👤 <b>Name:</b> {updated_data['name']}\n📧 <b>Email:</b> {updated_data['email']}\n📞 <b>Phone:</b> {updated_data['phone']}"
                    accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
                    no_accounts = "\nNo trading accounts registered yet."

                updated_message = f"{header}\n\n{user_info}{accounts_header}\n"
                
                if updated_data['trading_accounts']:
                    for i, acc in enumerate(updated_data['trading_accounts'], 1):
                        if lang == "ar":
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
                        else:
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
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
                    params = {
                        "lang": lang,
                        "edit": "1",
                        "name": updated_data['name'],
                        "email": updated_data['email'],
                        "phone": updated_data['phone']
                    }
                    edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
                    keyboard.append([InlineKeyboardButton(edit_data_label, web_app=WebAppInfo(url=edit_url))])
                
                keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    await application.bot.edit_message_text(
                        chat_id=ref["chat_id"],
                        message_id=ref["message_id"],
                        text=updated_message,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    save_form_ref(telegram_id, ref["chat_id"], ref["message_id"], origin="my_accounts", lang=lang)
                    return JSONResponse(content={"message": "Updated successfully."})
                except Exception:
                    logger.exception("Failed to update my accounts message after edit")
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
            accounts_label = "👤 بياناتي وحساباتي"
        else:
            header_title = "🎉 Congrats — Choose your broker now"
            brokers_title = ""
            back_label = "🔙 Back to Forex"
            edit_label = "✏️ Edit my data"
            accounts_label = "👤 My Data & Accounts"

        keyboard = [
            [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
             InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
        ]

        keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
        keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        edited = False
        if telegram_id and ref:
            try:
                await application.bot.edit_message_text(
                    text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], 
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
                        text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], 
                        header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, 
                        arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", 
                        reply_markup=reply_markup, 
                        parse_mode="HTML", 
                        disable_web_page_preview=True
                    )
                    save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
                except Exception:
                    logger.exception("Failed to send congrats message to user.")
            else:
                logger.info("No telegram_id available from WebApp payload; skipping Telegram notification.")

        
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
            #"ar": ["📊 نسخ الصفقات", "💬 قناة التوصيات", "📰 الأخبار الاقتصادية"],
            "ar": ["📊 نسخ الصفقات"],
            #"en": ["📊 Copy Trading", "💬 Signals Channel", "📰 Economic News"],
            "en": ["📊 Copy Trading"],
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
        box = build_header_html(title, labels, header_emoji=header_emoji_for_lang, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0)
        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in options]
        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await q.edit_message_text(box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
           
            save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin=q.data, lang=lang)
        except Exception:
            await context.bot.send_message(chat_id=q.message.chat_id, text=box, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    if q.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
      
        existing = get_subscriber_by_telegram_id(user_id)
        if existing:
          
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

            keyboard = [
                [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
                 InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
            ]

          
            keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
            keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                await q.edit_message_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
               
                save_form_ref(user_id, q.message.chat_id, q.message.message_id, origin="brokers", lang=display_lang)
            except Exception:
                
                try:
                    sent = await context.bot.send_message(chat_id=q.message.chat_id, text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if display_lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                    save_form_ref(user_id, sent.chat_id, sent.message_id, origin="brokers", lang=display_lang)
                except Exception:
                    logger.exception("Failed to show congrats screen for already-registered user.")
            return

       
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

    if q.data in ("👤 بياناتي وحساباتي", "👤 My Data & Accounts"):
        await show_user_accounts(update, context, user_id, lang)
        return

    placeholder = "تم اختيار الخدمة" if lang == "ar" else "Service selected"
    details = "سيتم إضافة التفاصيل قريبًا..." if lang == "ar" else "Details will be added soon..."
    
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

    keyboard = [
        [InlineKeyboardButton("🏦 Oneroyall", url="https://vc.cabinet.oneroyal.com/ar/links/go/10118"),
         InlineKeyboardButton("🏦 Tickmill", url="https://my.tickmill.com?utm_campaign=ib_link&utm_content=IB60363655&utm_medium=Open+Account&utm_source=link&lp=https%3A%2F%2Fmy.tickmill.com%2Far%2Fsign-up%2F")]
    ]

    user_id = getattr(msg.from_user, "id", None)
    

    keyboard.append([InlineKeyboardButton(accounts_label, callback_data="my_accounts")])
    keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
    try:
        edited = False
        ref = get_form_ref(user_id) if user_id else None
        if ref:
            try:
                await msg.bot.edit_message_text(text=build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
                edited = True
                clear_form_ref(user_id)
            except Exception:
                logger.exception("Failed to edit form message in fallback path")
        if not edited:
            sent = await msg.reply_text(build_header_html(header_title, ["🏦 Oneroyall","🏦 Tickmill", back_label, accounts_label], header_emoji=HEADER_EMOJI, underline_min=FIXED_UNDERLINE_LENGTH, arabic_indent=1 if lang=="ar" else 0) + f"\n\n{brokers_title}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
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
        # الحقول الجديدة
        initial_balance = (payload.get("initial_balance") or "").strip()
        current_balance = (payload.get("current_balance") or "").strip()
        withdrawals = (payload.get("withdrawals") or "").strip()
        copy_start_date = (payload.get("copy_start_date") or "").strip()
        agent = (payload.get("agent") or "").strip()
        lang = (payload.get("lang") or "ar").lower()

        if not all([telegram_id, broker, account, password, server]):
            return JSONResponse(status_code=400, content={"error": "Missing fields."})

        subscriber = get_subscriber_by_telegram_id(telegram_id)
        if not subscriber:
            return JSONResponse(status_code=404, content={"error": "User not found. Please complete registration first."})

        success = save_trading_account(
            subscriber_id=subscriber.id,
            broker_name=broker,
            account_number=account,
            password=password,
            server=server,
            initial_balance=initial_balance,
            current_balance=current_balance,
            withdrawals=withdrawals,
            copy_start_date=copy_start_date,
            agent=agent
        )

        if not success:
            return JSONResponse(status_code=500, content={"error": "Failed to save trading account."})

        ref = get_form_ref(telegram_id)
        
        if ref:
            updated_data = get_subscriber_with_accounts(telegram_id)
            
            if updated_data:
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
                    header = build_header_html(
                        header_title, 
                        labels,
                        header_emoji=HEADER_EMOJI,
                        underline_min=FIXED_UNDERLINE_LENGTH,
                        arabic_indent=1
                    )
                    
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
                    header = build_header_html(
                        header_title, 
                        labels,
                        header_emoji=HEADER_EMOJI,
                        underline_min=FIXED_UNDERLINE_LENGTH,
                        arabic_indent=0
                    )
                    
                    user_info = f"👤 <b>Name:</b> {updated_data['name']}\n📧 <b>Email:</b> {updated_data['email']}\n📞 <b>Phone:</b> {updated_data['phone']}"
                    accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
                    no_accounts = "\nNo trading accounts registered yet."

                updated_message = f"{header}\n\n{user_info}{accounts_header}\n"
                
                if updated_data['trading_accounts']:
                    for i, acc in enumerate(updated_data['trading_accounts'], 1):
                        if lang == "ar":
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
                            # إضافة الحقول الجديدة إذا كانت موجودة
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
                        else:
                            account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
                            # إضافة الحقول الجديدة إذا كانت موجودة
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
                    params = {
                        "lang": lang,
                        "edit": "1",
                        "name": updated_data['name'],
                        "email": updated_data['email'],
                        "phone": updated_data['phone']
                    }
                    edit_url = f"{WEBAPP_URL}?{urlencode(params, quote_via=quote_plus)}"
                    keyboard.append([InlineKeyboardButton(edit_data_label, web_app=WebAppInfo(url=edit_url))])
                
                keyboard.append([InlineKeyboardButton(back_label, callback_data="forex_main")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    await application.bot.edit_message_text(
                        chat_id=ref["chat_id"],
                        message_id=ref["message_id"],
                        text=updated_message,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    
                    save_form_ref(telegram_id, ref["chat_id"], ref["message_id"], origin="my_accounts", lang=lang)
                except Exception:
                    logger.exception("Failed to update user accounts message")
                    try:
                        sent = await application.bot.send_message(
                            chat_id=telegram_id, 
                            text=updated_message, 
                            reply_markup=reply_markup, 
                            parse_mode="HTML", 
                            disable_web_page_preview=True
                        )
                        save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="my_accounts", lang=lang)
                    except Exception:
                        logger.exception("Failed to send fallback message")
            else:
                logger.error("Failed to get updated user data")
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

async def show_user_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, lang: str):
    """عرض بيانات المستخدم مع جميع حسابات التداول - بنفس تنسيق صفحة 'تداول الفوركس'"""
    user_data = get_subscriber_with_accounts(telegram_id)
    
    if not user_data:
        if lang == "ar":
            text = "⚠️ لم تقم بالتسجيل بعد. يرجى التسجيل أولاً."
        else:
            text = "⚠️ You haven't registered yet. Please register first."
        
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(text)
        else:
            await context.bot.send_message(chat_id=telegram_id, text=text)
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
            underline_min=FIXED_UNDERLINE_LENGTH,
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
            underline_min=FIXED_UNDERLINE_LENGTH,
            arabic_indent=0
        )
     
        user_info = f"👤 <b>Name:</b> {user_data['name']}\n📧 <b>Email:</b> {user_data['email']}\n📞 <b>Phone:</b> {user_data['phone']}"
        accounts_header = "\n\n🏦 <b>Trading Accounts:</b>"
        no_accounts = "\nNo trading accounts registered yet."

    message = f"{header}\n\n{user_info}{accounts_header}\n"
    
    if user_data['trading_accounts']:
        for i, acc in enumerate(user_data['trading_accounts'], 1):
            if lang == "ar":
                account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
                # إضافة الحقول الجديدة إذا كانت موجودة
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
            else:
                account_text = f"\n{i}. <b>{acc['broker_name']}</b> - {acc['account_number']}\n   🖥️ {acc['server']}\n"
                # إضافة الحقول الجديدة إذا كانت موجودة
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
        
        sent = await context.bot.send_message(
            chat_id=telegram_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
       
        save_form_ref(telegram_id, sent.chat_id, sent.message_id, origin="my_accounts", lang=lang)
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
