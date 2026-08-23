# Playwright's own image ships the browsers and their system deps.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./crawler/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Persistent browser profile + any downloaded assets live on a volume.
RUN mkdir -p /data/browser-profile && chown -R pwuser /data /app
USER pwuser

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "crawler.run"]
