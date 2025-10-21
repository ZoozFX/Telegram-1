import os
import re
import json
import logging
import unicodedata
from typing import List
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
from app.db import Base, engine
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import sessionmaker

# -------------------------------
# Logging
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
    telegram_id = Column(Integer, nullable=True)
    lang = Column(String(8), default="ar")

Base.metadata.create_all(bind=engine)

# -------------------------------
# Settings / env
# -------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.onrender.com
# WEBAPP_URL can be set directly, otherwise it will be derived from WEBHOOK_URL + /webapp
WEBAPP_URL = os.getenv("WEBAPP_URL") or (f"{WEBHOOK_URL}/webapp" if WEBHOOK_URL else None)

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")
if not WEBAPP_URL:
    logger.warning("⚠️ WEBAPP_URL not set — WebApp button may not work without a public URL.")

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

SIDE_MARK = "◾"
HEADER_EMOJI = "✨"
NBSP = "\u00A0"

# -------------------------------
# Helpers: emoji removal / display width
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
# build_header_html
# -------------------------------
def build_header_html(
    title: str,
    keyboard_labels: List[str],
    header_emoji: str = "💥💥",
    underline_min: int = 25,
    underline_enabled: bool = True,
    underline_length: int = 25,
    underline_char: str = "━",
    arabic_indent: int = 0,
) -> str:
    NBSP = "\u00A0"
    RLE = "\u202B"
    PDF = "\u202C"
    LRM = "\u200E"

    is_arabic = bool(re.search(r'[\u0600-\u06FF]', title))

    if is_arabic:
        indent_spaces = NBSP * arabic_indent
        full_title = f"{indent_spaces}{RLE}{header_emoji} {title} {header_emoji}{PDF}"
    else:
        full_title = f"{LRM}{header_emoji} {title} {header_emoji}{LRM}"

    title_width = display_width(remove_emoji(full_title))
    target_width = max(max_button_width(keyboard_labels), underline_min)
    space_needed = max(0, target_width - title_width)
    pad_left = space_needed // 2
    pad_right = space_needed - pad_left

    centered_line = f"{NBSP * pad_left}<b>{full_title}</b>{NBSP * pad_right}"

    underline_line = ""
    if underline_enabled:
        line = underline_char * underline_length
        diff = max(0, target_width - underline_length)
        pad_left_line = diff // 2
        pad_right_line = diff - pad_left_line
        underline_line = f"\n{NBSP * pad_left_line}{line}{NBSP * pad_right_line}"

    return centered_line + underline_line

# -------------------------------
# REST: list subscribers
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
# Save subscriber helper
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
# Validation regex (server-side)
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# ===============================
# Start & main sections
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
        await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = "ar" if query.data == "lang_ar" else "en"
    context.user_data["lang"] = lang
    await show_main_sections(update, context, lang)

# ===============================
# WebApp page (renders Arabic or English based on query param ?lang=ar|en)
# ===============================
@app.get("/webapp")
def webapp_form(request: Request):
    # determine page language from query param (fallback to 'ar')
    lang = (request.query_params.get("lang") or "ar").lower()
    is_ar = lang == "ar"

    # Texts depending on language
    page_title = "🧾 من فضلك أكمل بياناتك" if is_ar else "🧾 Please complete your data"
    name_label = "الاسم" if is_ar else "Full name"
    email_label = "البريد الإلكتروني" if is_ar else "Email"
    phone_label = "رقم الهاتف (مع رمز الدولة)" if is_ar else "Phone (with country code)"
    submit_label = "إرسال" if is_ar else "Submit"
    close_label = "إغلاق" if is_ar else "Close"
    sending_msg = "تم الإرسال. سيتم إغلاق النافذة..." if is_ar else "Sent — window will close..."
    invalid_conn = "فشل في الاتصال بالخادم" if is_ar else "Failed to connect to server"

    # direction and rtl style for Arabic
    dir_attr = "rtl" if is_ar else "ltr"
    text_align = "right" if is_ar else "left"
    input_dir = "rtl" if is_ar else "ltr"

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
        <label style="text-align:{text_align}">{name_label} / { 'Name' if is_ar else '' }</label>
        <input id="name" placeholder="{ 'مثال: أحمد علي' if is_ar else 'e.g. Ahmed Ali' }" />
        <label style="text-align:{text_align}">{email_label}</label>
        <input id="email" type="email" placeholder="you@example.com" />
        <label style="text-align:{text_align}">{phone_label}</label>
        <input id="phone" placeholder="+20123 456 7890" />
        <div class="small">{ 'البيانات تُرسل مباشرة للبوت بعد الضغط على إرسال.' if is_ar else 'Data will be sent to the bot.' }</div>
        <div style="margin-top:12px;text-align:{text_align};">
          <button class="btn btn-primary" id="submit">{submit_label}</button>
          <button class="btn btn-ghost" id="close">{close_label}</button>
        </div>
        <div id="status" class="small" style="margin-top:10px;color:#b00;text-align:{text_align}"></div>
      </div>

      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script>
        const tg = window.Telegram.WebApp || {{}};
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

        // determine lang from query param if present
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
              statusEl.textContent = data.message || '{sending_msg}';
              // close the webapp after short delay
              try {{ setTimeout(()=>tg.close(), 700); }} catch(e){{ /* ignore */ }}
              // also send small notification via sendData (optional)
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
# POST endpoint: receive form submission from WebApp
# ===============================
@app.post("/webapp/submit")
async def webapp_submit(payload: dict = Body(...)):
    try:
        name = (payload.get("name") or "").strip()
        email = (payload.get("email") or "").strip()
        phone = (payload.get("phone") or "").strip()
        tg_user = payload.get("tg_user") or {}
        page_lang = (payload.get("lang") or "").lower() or None

        # server-side validation
        if not name or len(name) < 2:
            return JSONResponse(status_code=400, content={"error": "Name too short or missing."})
        if not EMAIL_RE.match(email):
            return JSONResponse(status_code=400, content={"error": "Invalid email."})
        if not PHONE_RE.match(phone):
            return JSONResponse(status_code=400, content={"error": "Invalid phone."})

        # determine language: prefer page_lang, else infer from tg_user, else default 'ar'
        lang = "ar"
        if page_lang in ("ar", "en"):
            lang = page_lang
        else:
            lang_code = tg_user.get("language_code") if isinstance(tg_user, dict) else None
            if lang_code and str(lang_code).startswith("en"):
                lang = "en"

        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        telegram_username = tg_user.get("username") if isinstance(tg_user, dict) else None

        # Save to DB
        save_subscriber(name=name, email=email, phone=phone, lang=lang, telegram_id=telegram_id, telegram_username=telegram_username)

        # Send message to user (if we have telegram_id)
        if telegram_id:
            try:
                if lang == "ar":
                    header_title = "✅ تم حفظ بياناتك"
                    brokers_title = "اختر وسيطك الآن"
                    back_label = "🔙 الرجوع للقائمة الرئيسية"
                else:
                    header_title = "✅ Your data has been saved"
                    brokers_title = "Choose your broker now"
                    back_label = "🔙 Back to main menu"

                header = build_header_html(header_title, [back_label], header_emoji=HEADER_EMOJI, underline_length=20, underline_min=12, arabic_indent=1 if lang=="ar" else 0)

                keyboard = [
                    [
                        InlineKeyboardButton("🏦 Oneroyall", url="https://t.me/ZoozFX"),
                        InlineKeyboardButton("🏦 Tickmill", url="https://t.me/ZoozFX")
                    ],
                    [InlineKeyboardButton(back_label, callback_data="back_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # send message to user
                await application.bot.send_message(chat_id=telegram_id, text=header + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                logger.exception("Failed to send post-registration message to user")

        return JSONResponse(content={"message": "Saved successfully."})
    except Exception as e:
        logger.exception("Error in webapp_submit: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

# ===============================
# menu_handler: build WebApp URL with lang param
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

    if query.data in ("📊 نسخ الصفقات", "📊 Copy Trading"):
        context.user_data["registration"] = {"lang": lang}
        if lang == "ar":
            title = "من فضلك ادخل البيانات"
            back_label = "🔙 الرجوع للقائمة السابقة"
            open_label = "📝 افتح نموذج التسجيل"
            header_emoji_for_lang = HEADER_EMOJI
        else:
            title = "Please enter your data"
            back_label = "🔙 Back to previous menu"
            open_label = "📝 Open registration form"
            header_emoji_for_lang = "✨"

        labels = [open_label, back_label]
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

        keyboard = []
        if WEBAPP_URL:
            # attach lang param so webapp renders in correct language
            url_with_lang = f"{WEBAPP_URL}?lang={lang}"
            keyboard.append([InlineKeyboardButton(open_label, web_app=WebAppInfo(url=url_with_lang))])
        else:
            fallback_text = "فتح النموذج" if lang == "ar" else "Open form"
            keyboard.append([InlineKeyboardButton(fallback_text, callback_data="fallback_open_form")])

        keyboard.append([InlineKeyboardButton(back_label, callback_data="back_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=header, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return

    # fallback for other sections
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

# ===============================
# Fallback: handle message.web_app_data if Telegram provides it
# ===============================
async def web_app_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    web_app_data = getattr(msg, "web_app_data", None)
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
    # prefer payload lang if present
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
        save_subscriber(
            name=name,
            email=email,
            phone=phone,
            lang=lang,
            telegram_id=getattr(msg.from_user, "id", None),
            telegram_username=getattr(msg.from_user, "username", None)
        )
    except Exception:
        logger.exception("Error saving subscriber from web_app message fallback")

    success_msg = "✅ تم حفظ بياناتك بنجاح! شكراً." if lang == "ar" else "✅ Your data has been saved successfully! Thank you."
    try:
        await msg.reply_text(success_msg)
    except Exception:
        pass

    # show brokers
    try:
        if lang == "ar":
            title = "اختر الوسيط"
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            title = "Choose your broker"
            back_label = "🔙 Back to main menu"

        keyboard = [
            [InlineKeyboardButton("🏦 Oneroyall", url="https://t.me/ZoozFX"),
             InlineKeyboardButton("🏦 Tickmill", url="https://t.me/ZoozFX")],
            [InlineKeyboardButton(back_label, callback_data="back_main")]
        ]
        header = build_header_html(title, ["Oneroyall", "Tickmill", back_label], header_emoji=HEADER_EMOJI, underline_length=25, underline_min=20, arabic_indent=1 if lang=="ar" else 0)
        await msg.reply_text(header, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

# ===============================
# Handlers registration
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))
# web_app fallback handler (should be before the general text handlers)
application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.Regex(r'.*'), web_app_message_handler))
# keep a placeholder for general text handlers if you need them
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: None))

# ===============================
# Webhook
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
