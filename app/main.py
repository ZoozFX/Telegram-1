import os
import argparse
from flask import Flask, request, abort
from telegram import Update, Bot
from .bot import application
from .db import engine, Base
from .utils import setup_webhook

app = Flask(__name__)

# create tables if not exist
Base.metadata.create_all(bind=engine)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
bot = Bot(token=TOKEN)

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    update = Update.de_json(request.get_json(force=True), bot)
    # Put update into PTB application update queue
    await application.update_queue.put(update)
    return "OK", 200

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['polling', 'webhook'], default='webhook')
    args = parser.parse_args()

    if args.mode == 'polling':
        # Run locally using polling for development
        application.run_polling()
    else:
        # When running under gunicorn/Render, the Flask app will be used and
        # we should ensure webhook is set
        url = setup_webhook(bot)
        print("Webhook set to:", url)
        # Flask app will be served by gunicorn (app variable)
