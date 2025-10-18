import os
import asyncio

async def setup_webhook(bot):
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH")
    if WEBHOOK_URL and WEBHOOK_PATH:
        full_url = WEBHOOK_URL + WEBHOOK_PATH
        await bot.set_webhook(full_url)
        print(f"✅ Webhook set to {full_url}")
        return full_url
    else:
        print("⚠️ Missing WEBHOOK_URL or BOT_WEBHOOK_PATH")
        return None
