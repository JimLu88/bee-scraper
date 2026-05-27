# bee-scraper (耳目)

14 站点 + 13 搜索源 + AI 规划员

## Run

\\\ash
cd backend
py -3.11 -m pip install -r requirements.txt
py -3.11 -m uvicorn app.main:app --reload --port 8003
\\\

## Auth

All routes except /healthz and /manifest require `Authorization: Bearer <BEE_BEARER_TOKEN>`.

## Status

Scaffold only. Real implementation tracks: plan v2 阶段 2-5 + v3 增量包.