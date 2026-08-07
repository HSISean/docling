FROM python:3.11-slim

WORKDIR /app

# libgl1 / libglib are needed by opencv, which docling's layout models depend on
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD gunicorn app:app --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:$PORT
