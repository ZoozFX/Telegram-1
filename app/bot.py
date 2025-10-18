from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = user.language_code or "en"
    if lang.startswith("ar"):
        text = f"👋 أهلاً {user.first_name}!\nمرحباً بك في البوت!"
    else:
        text = f"👋 Hello {user.first_name}!\nWelcome to the bot!"
    await update.message.reply_text(text)

application = ApplicationBuilder().token("dummy").build()
application.add_handler(CommandHandler("start", start))
