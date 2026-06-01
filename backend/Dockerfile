# bee-scraper (数据爬虫) — 群晖/Docker 部署用. 端口 8003.
# 依赖轻量 (httpx/beautifulsoup4/lxml, 无 playwright), python:3.11-slim 即可.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8003

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
