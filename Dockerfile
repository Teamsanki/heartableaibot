FROM python:3.9-slim
RUN apt-get update && apt-get install -y \
    chromium-driver \
    chromium \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]