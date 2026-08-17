FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY start_proxy.py .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/certs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"8082\")}/health', timeout=2)" || exit 1

CMD ["python", "start_proxy.py"]
