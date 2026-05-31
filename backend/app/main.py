"""bee-scraper - 耳目
14 站点 + 13 搜索源 + AI 规划员
端口: 8003

H-SEMAS 蜂群微服务架构(七剑客之一)。
对外: REST + Bearer Token + plugin-manifest.json
"""
from __future__ import annotations

import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel


SERVICE_NAME = "bee-scraper"
SERVICE_PORT = 8003
BEARER_TOKEN = os.environ.get("BEE_BEARER_TOKEN", "dev-token-change-me")

app = FastAPI(title=SERVICE_NAME, version="0.1.0")
bearer = HTTPBearer(auto_error=False)

# v3-I OpenTelemetry + /metrics (silent fallback if deps missing)
import sys as _sys  # noqa: E402
_sys.path.insert(0, "D:/AI/observability")
try:
    from bee_otel import init_otel  # type: ignore
    init_otel(SERVICE_NAME, app)
except Exception:
    pass

_log_router_ok = False
try:
    from bee_logs import setup_service_logging, log_router  # type: ignore
    from pathlib import Path as _BeePath
    setup_service_logging(SERVICE_NAME,
                          _BeePath(__file__).parent.parent / "data" / "logs")
    _log_router_ok = True
except Exception:
    pass


# v8 搜索 key 透传修复: 爬虫是独立进程, 读不到蜂群主进程设的 env.
# 启动时从蜂群 hub_settings.json 读 TAVILY/EXA/BRAVE key 注入本进程 env (不覆盖已有 env).
# 这样用户在前端"搜索 Key 配置"填一次, 重启爬虫即可生效.
def _load_search_keys_from_hub() -> None:
    import json
    from pathlib import Path
    candidates = [
        os.environ.get("BEE_HUB_SETTINGS", ""),
        r"D:\AI\AI 蜂群系统\h-semas\backend\data\hub_settings.json",
    ]
    field_to_env = {
        "tavily_api_key": "TAVILY_API_KEY",
        "exa_api_key": "EXA_API_KEY",
        "brave_api_key": "BRAVE_API_KEY",
    }
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for field, env in field_to_env.items():
            v = data.get(field)
            if isinstance(v, str) and v.strip() and not v.startswith("***") and not os.environ.get(env):
                os.environ[env] = v.strip()
        break  # 用第一个找到的配置文件


_load_search_keys_from_hub()


def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None or credentials.credentials != BEARER_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")
    return credentials.credentials


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "port": str(SERVICE_PORT)}


@app.get("/manifest")
def manifest() -> dict:
    """plugin-spec.md compliant manifest (v5-G)."""
    return {
        "name": SERVICE_NAME,
        "version": "0.2.0",
        "purpose": "5 站点真爬 + 11 站点占位 + 3 搜索源真接",
        "endpoints": [
            {"method": "POST", "path": "/scraper/task",
             "in": "{site,keyword,limit}", "out": "items[]"},
            {"method": "POST", "path": "/scraper/ai-plan",
             "in": "{natural_language}", "out": "plan[]"},
            {"method": "POST", "path": "/scraper/search/query",
             "in": "{query,providers[]}", "out": "{results,errors}"},
            {"method": "GET", "path": "/scraper/data?task_id=",
             "out": "cached task json or recent[]"},
            {"method": "GET", "path": "/scraper/sites",
             "out": "{implemented,stub,search_implemented,search_stub}"},
        ],
        "auth": {"type": "bearer"},
    }


# ----- subroutes loaded from app. -----
from .scraper import router as service_router  # noqa: E402
app.include_router(service_router, prefix="/scraper", dependencies=[Depends(auth)])

if _log_router_ok:
    app.include_router(log_router, dependencies=[Depends(auth)])