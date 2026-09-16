FROM python:3.12-alpine
WORKDIR /app
COPY app.py ./
COPY static ./static
RUN mkdir -p /app/data && adduser -D -u 10001 ledger && chown -R ledger:ledger /app
USER ledger
ENV PORT=8080 ROADTRIP_DB=/app/data/roadtrip.db PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD wget -q -O - http://127.0.0.1:8080/api/health || exit 1
CMD ["python", "app.py"]
