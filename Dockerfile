FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
EXPOSE 3005
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3005/healthz')" || exit 1
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.server:app --host 0.0.0.0 --port 3005"]
