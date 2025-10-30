# telegram-bot-ar-en
بوت تليجرام بلغة بايثون يدعم العربية والإنجليزية، يحفظ مدخلات المستخدمين ويقبل تحميل مدخلات من ملف JSON.

## تشغيل محلي
1. انسخ الريبو
2. أنشئ env محلياً (أو استخدم .env)
3. تثبيت المتطلبات: `pip install -r requirements.txt`
4. شغّل (للاختبار بتشغيل polling):
   ```bash
   python -m app.main --mode polling
   ```

## نشر على Render
1. أضف Managed Postgres ونسخ `DATABASE_URL` إلى متغيرات البيئة في الخدمة.
2. اربط الريبو وقم بتعيين متغيرات البيئة: `TELEGRAM_TOKEN`, `DATABASE_URL`, `WEBHOOK_URL`, `BOT_WEBHOOK_PATH`.
3. استخدم الأمر من Procfile أو Dockerfile.

## تحميل مدخلات جاهزة
ضع JSON في `app/stored_inputs/seed_inputs.json` ثم شغّل `from app.utils import load_external_inputs; print(load_external_inputs())`

تم تطويرة بواسطة Ahmed Abdelaziz
https://t.me/ZoozFX
