FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir fastapi uvicorn gunicorn python-telegram-bot[webhooks] SQLAlchemy psycopg2-binary python-dotenv alembic

CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "--bind", "0.0.0.0:5000"]
