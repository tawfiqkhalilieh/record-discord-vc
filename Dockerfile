FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY shared ./shared
COPY dashboard ./dashboard
CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "3000", "--no-proxy-headers"]
