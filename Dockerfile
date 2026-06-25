FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Amazon DocumentDB / RDS CA bundle
RUN curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /usr/local/share/ca-certificates/rds-global-bundle.crt \
    && update-ca-certificates

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY app/ ./app/
COPY public/ ./public/
COPY prompts/ ./prompts/

# Data directory (CSV files mounted at runtime)
RUN mkdir -p data && chmod -R 777 data

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
