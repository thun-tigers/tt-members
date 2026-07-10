FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*
ARG TT_COMMON_REF=v0.1.17
RUN sed -i "s#@v[0-9][0-9.]*#@${TT_COMMON_REF}#" requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system appgroup && adduser --system --ingroup appgroup --no-create-home appuser \
    && mkdir -p /app/instance && chown -R appuser:appgroup /app
USER appuser

ENV FLASK_APP=run.py
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Zurich

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
