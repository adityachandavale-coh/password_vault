FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends rclone && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY homehub ./homehub
COPY src ./src
COPY public ./public
COPY scripts ./scripts
COPY index.html ./index.html
EXPOSE 8443
CMD ["python", "-m", "uvicorn", "homehub.main:app", "--host", "0.0.0.0", "--port", "8443", "--ssl-certfile", "data/certs/server.crt", "--ssl-keyfile", "data/certs/server.key"]
