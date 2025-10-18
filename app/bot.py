import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from .db import SessionLocal
from .models import User, UserInput
from .i18n import t

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Create the application once and reuse
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    tg_id = tg_user.id
    db = SessionLocal()
    user = db.query(User).filter_by(telegram_id=tg_id).first()
    if not user:
        lang = "ar" if (tg_user.language_code and tg_user.language_code.startswith("ar")) else "en"
        user = User(telegram_id=tg_id, lang=lang)
        db.add(user); db.commit(); db.refresh(user)
    await update.message.reply_text(t("start", user.lang))
    db.close()

async def setlang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    tg_id = update.effective_user.id
    db = SessionLocal()
    user = db.query(User).filter_by(telegram_id=tg_id).first()
    if user and arg in ("en", "ar"):
        user.lang = arg; db.add(user); db.commit()
        await update.message.reply_text("Language set.")
    else:
        await update.message.reply_text("Usage: /setlang en or /setlang ar")
    db.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    tg_id = update.effective_user.id
    db = SessionLocal()
    user = db.query(User).filter_by(telegram_id=tg_id).first()
    if not user:
        user = User(telegram_id=tg_id, lang="en")
        db.add(user); db.commit(); db.refresh(user)
    ui = UserInput(user_id=user.id, text=text)
    db.add(ui); db.commit()
    await update.message.reply_text(t("saved", user.lang))
    db.close()

# register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("setlang", setlang))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
