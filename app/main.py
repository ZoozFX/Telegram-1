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
# إعدادات عامة
# -------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # eg https://your-app.onrender.com
WEBAPP_URL = os.getenv("WEBAPP_URL") or (f"{WEBHOOK_URL}/webapp" if WEBHOOK_URL else None)

if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not set")

if not WEBAPP_URL:
    logger.warning("⚠️ WEBAPP_URL not set — set WEBAPP_URL env var to your public webapp URL (e.g. https://your-app.onrender.com/webapp).")

application = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

SIDE_MARK = "◾"
HEADER_EMOJI = "✨"
NBSP = "\u00A0"

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
        indent_spaces = ""
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
# REST: قائمة المشتركين
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
# حفظ مشترك
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
# Regex للتحقق
# -------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9\-\s]{6,20}$")

# ===============================
# /start + الأقسام (كما في كودك الأصلي)
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
# صفحة WebApp (HTML) — يتم فتحها داخل Telegram
# ===============================
@app.get("/webapp")
def webapp_form():
    """
    صفحة الـ WebApp: ترسل POST إلى /webapp/submit مباشرة لضمان الحفظ في DB.
    تستخدم Telegram.WebApp.initDataUnsafe.user للحصول على telegram user info عند توفرها.
    """
    html = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>Registration Form</title>
      <style>
        body{{font-family: Arial, Helvetica, sans-serif; padding:16px; background:#f7f7f7;}}
        .card{{max-width:600px;margin:24px auto;padding:16px;border-radius:10px;background:white; box-shadow:0 4px 12px rgba(0,0,0,0.08)}}
        label{{display:block;margin-top:12px;font-weight:600}}
        input{{width:100%;padding:10px;margin-top:6px;border:1px solid #ddd;border-radius:6px;font-size:16px}}
        .btn{{display:inline-block;margin-top:16px;padding:10px 14px;border-radius:8px;border:none;font-weight:700;cursor:pointer}}
        .btn-primary{{background:#1E90FF;color:white}}
        .btn-ghost{{background:transparent;border:1px solid #ccc'}}
        .small{{font-size:13px;color:#666;margin-top:6px}}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>🧾 من فضلك أكمل بياناتك</h2>
        <label>الاسم / Full name</label>
        <input id="name" placeholder="e.g. Ahmed Ali / أحمد علي" />
        <label>البريد الإلكتروني / Email</label>
        <input id="email" type="email" placeholder="you@example.com" />
        <label>رقم الهاتف / Phone (with country code)</label>
        <input id="phone" placeholder="+20123 456 7890" />
        <div class="small">البيانات تُرسل مباشرة للبوت بعد الضغط على إرسال. / Data will be sent to the bot.</div>
        <div style="margin-top:12px;">
          <button class="btn btn-primary" id="submit">إرسال / Submit</button>
          <button class="btn btn-ghost" id="close">إغلاق</button>
        </div>
        <div id="status" class="small" style="margin-top:10px;color:#b00"></div>
      </div>

      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script>
        const tg = window.Telegram.WebApp;
        try {{ tg.expand(); }} catch(e){{/* ignore if not available */}}
        const statusEl = document.getElementById('status');

        function validateEmail(email) {{
          const re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
          return re.test(String(email).toLowerCase());
        }}
        function validatePhone(phone) {{
          const re = /^[+0-9\\-\\s]{{6,20}}$/;
          return re.test(String(phone));
        }}

        async function submitForm() {{
          const name = document.getElementById('name').value.trim();
          const email = document.getElementById('email').value.trim();
          const phone = document.getElementById('phone').value.trim();

          if (!name || name.length < 2) {{
            statusEl.textContent = 'الاسم قصير جدًا / Name is too short';
            return;
          }}
          if (!validateEmail(email)) {{
            statusEl.textContent = 'بريد إلكتروني غير صالح / Invalid email';
            return;
          }}
          if (!validatePhone(phone)) {{
            statusEl.textContent = 'رقم هاتف غير صالح / Invalid phone';
            return;
          }}

          const initUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user : null;

          const payload = {{
            name,
            email,
            phone,
            tg_user: initUser
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
              statusEl.textContent = data.message || 'تم الإرسال. سيتم إغلاق النافذة قريبًا / Sent';
              // إغلاق النافذة بعد ثواني قليلة
              try {{ setTimeout(()=>tg.close(), 800); }} catch(e){{ /* ignore */ }}
            }} else {{
              statusEl.textContent = data.error || 'فشل الإرسال';
            }}
          }} catch (e) {{
            statusEl.textContent = 'فشل في الاتصال بالخادم: ' + e.message;
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
# نقطة نهاية لاستقبال البيانات مباشرة من WebApp (POST)
# ===============================
@app.post("/webapp/submit")
async def webapp_submit(payload: dict = Body(...)):
    """
    يستقبل JSON من صفحة webapp ويقوم بالتحقق وحفظ السجل في DB.
    المتوقَّع: { name, email, phone, tg_user? }
    """
    try:
        name = (payload.get("name") or "").strip()
        email = (payload.get("email") or "").strip()
        phone = (payload.get("phone") or "").strip()
        tg_user = payload.get("tg_user") or {}

        # server-side validation
        if not name or len(name) < 2:
            return JSONResponse(status_code=400, content={"error": "Name too short or missing."})
        if not EMAIL_RE.match(email):
            return JSONResponse(status_code=400, content={"error": "Invalid email."})
        if not PHONE_RE.match(phone):
            return JSONResponse(status_code=400, content={"error": "Invalid phone."})

        # determine language
        lang = "ar"
        lang_code = tg_user.get("language_code") if isinstance(tg_user, dict) else None
        if lang_code and str(lang_code).startswith("en"):
            lang = "en"

        telegram_id = tg_user.get("id") if isinstance(tg_user, dict) else None
        telegram_username = tg_user.get("username") if isinstance(tg_user, dict) else None

        # Save to DB
        save_subscriber(name=name, email=email, phone=phone, lang=lang, telegram_id=telegram_id, telegram_username=telegram_username)

        # If we have telegram_id, send a message to the user with updated buttons
        if telegram_id:
            try:
                # prepare header + keyboard:
                if lang == "ar":
                    header_title = "✅ لقد أكملت التسجيل"
                    brokers_title = "اختر الوسيط"
                    back_label = "🔙 الرجوع للقائمة الرئيسية"
                else:
                    header_title = "✅ Registration completed"
                    brokers_title = "Choose your broker"
                    back_label = "🔙 Back to main menu"

                # message 1: replace the previous button idea by informing user
                header = build_header_html(header_title, [back_label], header_emoji=HEADER_EMOJI, underline_length=20, underline_min=12, arabic_indent=1 if lang=="ar" else 0)

                # keyboard: الصف الأول زر "لقد أكملت التسجيل" (callback) — للتحقق عند الضغط (خيارياً)
                # الصف الثاني: أزرار الوسطاء كروابط
                keyboard = [
                    [InlineKeyboardButton("✅ " + ("لقد أكملت التسجيل" if lang=="ar" else "I've completed registration"), callback_data="registration_confirmed")],
                    [
                        InlineKeyboardButton("🏦 Oneroyall", url="https://t.me/ZoozFX"),
                        InlineKeyboardButton("🏦 Tickmill", url="https://t.me/ZoozFX"),
                    ],
                    [InlineKeyboardButton(back_label, callback_data="back_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # send the message to the user (this will appear in chat, replacing original is not always possible)
                await application.bot.send_message(chat_id=telegram_id, text=header + f"\n\n{brokers_title}", reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                logger.exception("Failed to send post-registration message to user")

        return JSONResponse(content={"message": "Saved successfully."})
    except Exception as e:
        logger.exception("Error in webapp_submit: %s", e)
        return JSONResponse(status_code=500, content={"error": "Server error."})

# ===============================
# تعديل menu_handler: زر يفتح WebApp
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
            keyboard.append([InlineKeyboardButton(open_label, web_app=WebAppInfo(url=WEBAPP_URL))])
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

    # باقي المنطق كما كان
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
# Handler احتياطي: استقبال web_app data (fallback) إن وصل عبر message.web_app_data
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
    lang = context.user_data.get("lang", "ar")

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

    # عرض الوسطاء بعد الحفظ (fallback)
    try:
        if lang == "ar":
            title = "اختر الوسيط"
            back_label = "🔙 الرجوع للقائمة الرئيسية"
        else:
            title = "Choose your broker"
            back_label = "🔙 Back to main menu"

        keyboard = [[InlineKeyboardButton("🏦 Oneroyall", url="https://t.me/ZoozFX"),
                     InlineKeyboardButton("🏦 Tickmill", url="https://t.me/ZoozFX")],
                    [InlineKeyboardButton(back_label, callback_data="back_main")]]

        header = build_header_html(title, ["Oneroyall", "Tickmill", back_label], header_emoji=HEADER_EMOJI, underline_length=25, underline_min=20, arabic_indent=1 if lang=="ar" else 0)
        await msg.reply_text(header, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

# ===============================
# Handler للزر "لقد اكملت التسجيل"
# ===============================
async def registration_confirmed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ar")
    # نتحقق سريعًا إن كان المستخدم موجودًا في DB
    try:
        db = SessionLocal()
        user_row = db.query(Subscriber).filter(Subscriber.telegram_id == query.from_user.id).first()
        db.close()
        if user_row:
            msg = "✅ تم التحقق — أنت مسجل بالفعل. اختر وسيطك أدناه." if lang == "ar" else "✅ Verified — you're already registered. Choose your broker below."
        else:
            msg = "⚠️ لم نجد تسجيلًا مرتبطًا بحسابك. إذا أرسلت النموذج من قبل فربما فشل الحفظ. حاول مرة أخرى." if lang == "ar" else "⚠️ No registration found for your account. If you submitted the form earlier it might have failed. Try again."
    except Exception:
        logger.exception("Error verifying registration")
        msg = "✅ تم (عملية تحقق فشل داخليًا لكن البيانات قد تكون محفوظة)." if lang == "ar" else "✅ Checked (internal verification failed but data may be saved)."

    # نرسل رسالة تأكيد (أو نحدث الرسالة الحالية)
    try:
        await query.edit_message_text(msg)
    except Exception:
        await query.message.reply_text(msg)

# ===============================
# تسجيل المعالجات (handlers)
# ===============================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(menu_handler))
# handler لزر التحقق "لقد اكملت التسجيل"
application.add_handler(CallbackQueryHandler(registration_confirmed_handler, pattern="^registration_confirmed$"))
# web_app fallback handler (قبل handlers النصية العامة)
application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.Regex(r'.*'), web_app_message_handler))
# handler للنصوص العامة (احتياطي)
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
