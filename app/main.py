import os
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from .bot import application
from .db import Base, engine
from .utils import setup_webhook

# تهيئة قاعدة البيانات
Base.metadata.create_all(bind=engine)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
bot = Bot(token=TOKEN)

app = FastAPI(title="Telegram Bot")

@app.get("/")
def index():
    return {"status": "ok"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.update_queue.put(update)
        return {"ok": True}
    except Exception as e:
        print("Error handling update:", e)
        return {"ok": False}

# عند تشغيل السيرفر مباشرة (محليًا)
if __name__ == "__main__":
    import uvicorn
    setup_webhook(bot)
    uvicorn.run(app, host="0.0.0.0", port=5000)
