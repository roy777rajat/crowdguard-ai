# Backend image. UNTESTED: Docker was not run during development. Demo auth and simulated data only.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
ENV CROWDGUARD_DB=/data/crowdguard.db
EXPOSE 8000
CMD ["uvicorn", "backend.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
