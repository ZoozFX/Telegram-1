FROM python:3.11-slim
WORKDIR /app

# system deps
RUN apt-get update && apt-get install -y gcc make git libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORT=5000

CMD ["gunicorn", "app.main:app", "--bind", "0.0.0.0:5000", "--workers", "1"]
