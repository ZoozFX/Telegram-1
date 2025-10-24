import os
import re
import json
import logging
import unicodedata
from typing import List, Optional, Dict, Any
from urllib.parse import urlencode, quote_plus

from fastapi import FastAPI, Request, Body
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
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship
from app.db import Base, engine

# -------------------------------
# logging
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------
# DB models
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

    # علاقة المستخدم بحساباته
    trading_accounts = relationship(
        "ExistingAccount",
        back_populates="owner",
        cascade="all, delete-orphan"
    )

class ExistingAccount(Base):
    __tablename__ = "existing_accounts"
    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id", ondelete="CASCADE"), nullable=False)
    broker_name = Column(String(100), nullable=False)
    account_number = Column(String(100), nullable=False)
    password = Column(String(100), nullable=False)
    server = Column(String(100), nullable=False)

    owner = relationship("Subscriber", back_populates="trading_accounts")

Base.metadata.create_all(bind=engine)

# -------------------------------
# App settings
# -------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL") or (f"{WEBHOOK_URL}/webapp" if WEBHOOK_URL else None)

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

HEADER_EMOJI = "✨"
NBSP = "\u00A0"
FIXED_UNDERLINE_LENGTH = 25

FORM_MESSAGES: Dict[int, Dict[str, Any]] = {}

# -------------------------------
# Helpers
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
        width += 2 if ea in ("F", "W") else 1
    return width

def build_header_html(
    title: str,
    keyboard_labels: List[str],
    header_emoji: str = HEADER_EMOJI,
    underline_min: int = 20,
    underline_enabled: bool = True,
    underline_char: str = "━",
    arabic_indent: int = 0,
) -> str:
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
    underline_line = "\n" + (underline_char * target_width) if underline_enabled else ""
    return centered_line + underline_line

# -------------------------------
# DB Helpers
# -------------------------------
def save_or_update_subscriber(
    name: str,
    email: str,
    phone: str,
    lang: str = "ar",
    telegram_id: int = None,
    telegram_username: str = None
) -> str:
    try:
        db = SessionLocal()
        if telegram_id:
            existing = db.query(Subscriber).filter(Subscriber.telegram_id == telegram_id).first()
            if existing:
                existing.name = name
                existing.email = email
                existing.phone = phone
                existing.telegram_username = telegram_username
                if lang:
                    existing.lang = lang
                db.commit()
                db.close()
                return "updated"
        sub = Subscriber(
            name=name,
            email=email,
            phone=phone,
            telegram_username=telegram_username,
            telegram_id=telegram_id,
            lang=lang or "ar"
        )
        db.add(sub)
        db.commit()
        db.close()
        return "created"
    except Exception as e:
        logger.exception("Failed to save_or_update subscriber: %s", e)
        return "error"

def get_subscriber_by_telegram_id(tg_id: int) -> Optional[Subscriber]:
    try:
        db = SessionLocal()
        s = db.query(Subscriber).filter(Subscriber.telegram_id == tg_id).first()
        db.close()
        return s
    except Exception as e:
        logger.exception("DB lookup failed")
        return None

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
# Validation
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# -------------------------------
# WebApp endpoint for existing account
# -------------------------------
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

        db = SessionLocal()
        subscriber = db.query(Subscriber).filter(Subscriber.telegram_id == telegram_id).first()
        if not subscriber:
            db.close()
            return JSONResponse(status_code=404, content={"error": "User not found. Please register first."})

        rec = ExistingAccount(
            subscriber_id=subscriber.id,
            broker_name=broker,
            account_number=account,
            password=password,
            server=server
        )
        db.add(rec)
        db.commit()
        db.close()

        ref = get_form_ref(telegram_id)
        msg_text = "✅ تم تسجيل الحساب. سنتواصل معك قريباً!" if lang == "ar" else "✅ Account registered. We will contact you soon."
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
                await application.bot.send_message(chat_id=telegram_id, text=msg_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await application.bot.send_message(chat_id=telegram_id, text=msg_text, reply_markup=reply_markup, parse_mode="HTML")

        return JSONResponse(content={"message": "Saved successfully."})
    except Exception as e:
        logger.exception("Error saving existing account: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

# -------------------------------
# Webhook setup
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
