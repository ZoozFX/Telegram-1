FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir fastapi uvicorn python-telegram-bot[webhooks] SQLAlchemy psycopg2-binary

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]
