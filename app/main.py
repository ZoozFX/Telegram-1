from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
from telegram import Update, Bot
from .bot import application
from .db import engine, Base
from .utils import setup_webhook

Base.metadata.create_all(bind=engine)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{TOKEN}")
bot = Bot(token=TOKEN)

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.update_queue.put(update)
        return JSONResponse({"ok": True})
    except Exception as e:
        print("❌ Webhook error:", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.on_event("startup")
async def on_startup():
    try:
        url = setup_webhook(bot)
        print("✅ Webhook set to:", url)
    except Exception as e:
        print("⚠️ Webhook setup failed:", e)
