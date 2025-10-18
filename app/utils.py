import json
import os
from .db import SessionLocal
from .models import UserInput

def load_external_inputs(json_path=None):
    path = json_path or os.path.join(os.path.dirname(__file__), "stored_inputs", "seed_inputs.json")
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    db = SessionLocal()
    count = 0
    for item in data:
        # If the referenced user doesn't exist, you might want to set user_id to None or 0
        ui = UserInput(user_id=item.get("user_id", 0) or 0, text=item["text"], tag=item.get("tag"))
        db.add(ui)
        count += 1
    db.commit()
    db.close()
    return count

def setup_webhook(bot):
    """Set webhook if WEBHOOK_URL is provided in env vars."""
    webhook_url = os.getenv("WEBHOOK_URL")
    token = os.getenv("TELEGRAM_TOKEN")
    path = os.getenv("BOT_WEBHOOK_PATH", f"/webhook/{token}")
    if webhook_url and token:
        # final url
        final = webhook_url.rstrip("/") + path
        bot.set_webhook(final)
        return final
    return None
