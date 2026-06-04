"""bee-scraper / 数据爬虫 — 真实现版.

v0.2 — 真爬:
- hacker_news: 官方 firebase API
- arxiv: 官方 Atom API
- github_trending: GitHub Search API (无需 token; 有 token 走 GITHUB_TOKEN)
- huggingface: 官方 /api/models?sort=likes
- weibo_hot: s.weibo.com/top/summary 解析 HTML

搜索:
- tavily / brave / exa: 真调 API; 缺 key 时 501
- 其它 provider: 401/501 直说没接
"""
from __future__ import annotations
import os
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from . import platforms as _plat
from . import scrape_guard as _guard

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data" / "scraped"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = float(os.environ.get("BEE_SCRAPER_TIMEOUT", "20"))
UA = "bee-scraper/0.2 (+https://github.com/h-semas)"

_BASE_IMPL_SITES = {"hacker_news", "arxiv", "github_trending", "huggingface", "weibo_hot"}
# v0.3: 多平台 fetcher 由 platforms 模块提供 (原生 + site:); 这些现在都是「真实现」.
_PLATFORM_SITES = set(_plat.all_platform_fetchers().keys())
IMPL_SITES = _BASE_IMPL_SITES | _PLATFORM_SITES
STUB_SITES = {
    "papers_with_code", "product_hunt",
    "xueqiu", "juejin", "sspai", "_36kr", "huxiu", "infoq",
} - IMPL_SITES
SUPPORTED_SITES = sorted(IMPL_SITES | STUB_SITES)

IMPL_SEARCH = {"tavily", "brave", "exa"}
STUB_SEARCH = {
    "perplexity", "zhipu", "bing_cn", "search360",
    "semantic_scholar", "google_scholar",
    "github_code", "sourcegraph", "npm", "pypi",
}
SEARCH_PROVIDERS = sorted(IMPL_SEARCH | STUB_SEARCH)


def _fetch_hacker_news(limit: int) -> list[dict[str, Any]]:
    base = "https://hacker-news.firebaseio.com/v0"
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as c:
        ids = c.get(f"{base}/topstories.json").json()[: max(1, min(limit, 50))]
        out: list[dict[str, Any]] = []
        for sid in ids:
            it = c.get(f"{base}/item/{sid}.json").json() or {}
            out.append({
                "id": str(sid),
                "title": it.get("title", ""),
                "url": it.get("url") or f"https://news.ycombinator.com/item?id={sid}",
                "score": it.get("score", 0),
                "by": it.get("by", ""),
                "ts": it.get("time", 0),
                "comments": it.get("descendants", 0),
            })
    return out


def _fetch_arxiv(keyword: str, limit: int) -> list[dict[str, Any]]:
    q = (keyword or "").strip() or "machine learning"
    params = {
        "search_query": f"all:{q}", "start": 0,
        "max_results": max(1, min(limit, 50)),
        "sortBy": "submittedDate", "sortOrder": "descending",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as c:
        r = c.get("http://export.arxiv.org/api/query", params=params)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "xml")
    out: list[dict[str, Any]] = []
    for entry in soup.find_all("entry"):
        link_pdf = ""
        for ln in entry.find_all("link"):
            if ln.get("title") == "pdf":
                link_pdf = ln.get("href", "")
                break
        out.append({
            "id": (entry.id.text if entry.id else "").strip(),
            "title": (entry.title.text if entry.title else "").strip(),
            "summary": (entry.summary.text if entry.summary else "").strip()[:800],
            "authors": [a.find("name").text for a in entry.find_all("author") if a.find("name")],
            "pdf": link_pdf,
            "published": (entry.published.text if entry.published else "").strip(),
        })
    return out


def _fetch_github_trending(keyword: str, limit: int) -> list[dict[str, Any]]:
    q = (keyword or "").strip() or "stars:>1000"
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    params = {"q": q, "sort": "stars", "order": "desc",
              "per_page": max(1, min(limit, 50))}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as c:
        r = c.get("https://api.github.com/search/repositories", params=params)
        if r.status_code == 403:
            raise HTTPException(429, "github rate limit; set GITHUB_TOKEN to raise quota")
        r.raise_for_status()
        items = r.json().get("items", [])
    return [{
        "id": str(it.get("id", "")),
        "full_name": it.get("full_name", ""),
        "url": it.get("html_url", ""),
        "stars": it.get("stargazers_count", 0),
        "language": it.get("language") or "",
        "description": it.get("description") or "",
        "pushed_at": it.get("pushed_at", ""),
    } for it in items]


def _fetch_huggingface(keyword: str, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"sort": "likes7d", "direction": -1,
                              "limit": max(1, min(limit, 50))}
    if (keyword or "").strip():
        params["search"] = keyword.strip()
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as c:
        r = c.get("https://huggingface.co/api/models", params=params)
        r.raise_for_status()
        items = r.json()
    return [{
        "id": it.get("modelId") or it.get("id", ""),
        "url": f"https://huggingface.co/{it.get('modelId') or it.get('id', '')}",
        "likes": it.get("likes", 0),
        "downloads": it.get("downloads", 0),
        "tags": (it.get("tags") or [])[:8],
        "pipeline_tag": it.get("pipeline_tag", ""),
    } for it in items]


def _fetch_weibo_hot(limit: int) -> list[dict[str, Any]]:
    url = "https://s.weibo.com/top/summary"
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA},
                      follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
    rows = soup.select("table tbody tr")
    out: list[dict[str, Any]] = []
    for row in rows[: max(1, min(limit, 50))]:
        a = row.select_one("td.td-02 a")
        hot_td = row.select_one("td.td-02 span")
        if not a:
            continue
        out.append({
            "rank": len(out) + 1,
            "title": a.get_text(strip=True),
            "url": "https://s.weibo.com" + (a.get("href") or ""),
            "hot": hot_td.get_text(strip=True) if hot_td else "",
        })
    return out


SITE_FETCHERS = {
    "hacker_news": lambda kw, n: _fetch_hacker_news(n),
    "arxiv": _fetch_arxiv,
    "github_trending": _fetch_github_trending,
    "huggingface": _fetch_huggingface,
    "weibo_hot": lambda kw, n: _fetch_weibo_hot(n),
}

# v0.3: 合并多平台 fetcher (原生 API: reddit/wikipedia/bilibili/4chan/stackexchange/youtube
# + site: 限定: 小红书/抖音/ins/tiktok/discord/知乎/quora/小黑盒/淘宝/京东/拼多多/... 共 20+).
# 这些覆盖原 STUB_SITES 里的同名占位 (如 reddit/bilibili), 让它们真能用.
SITE_FETCHERS.update(_plat.all_platform_fetchers())


def _search_tavily(query: str) -> list[dict[str, Any]]:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise HTTPException(501, "TAVILY_API_KEY not set")
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.post("https://api.tavily.com/search", json={
            "api_key": key, "query": query, "max_results": 10,
            "search_depth": "basic", "include_answer": False, "include_images": True,
        })
        r.raise_for_status()
        data = r.json()
    imgs = data.get("images") or []
    return [{
        "title": it.get("title", ""),
        "url": it.get("url", ""),
        "snippet": (it.get("content") or "")[:400],
        "score": it.get("score", 0.0),
        "image_url": (imgs[i] if i < len(imgs) and isinstance(imgs[i], str) else ""),
    } for i, it in enumerate(data.get("results", []))]


def _search_brave(query: str) -> list[dict[str, Any]]:
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        raise HTTPException(501, "BRAVE_API_KEY not set")
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as c:
        r = c.get("https://api.search.brave.com/res/v1/web/search",
                  params={"q": query, "count": 10})
        r.raise_for_status()
        data = r.json()
    return [{
        "title": it.get("title", ""),
        "url": it.get("url", ""),
        "snippet": (it.get("description") or "")[:400],
        "age": it.get("age", ""),
    } for it in (data.get("web") or {}).get("results", [])]


def _search_exa(query: str) -> list[dict[str, Any]]:
    key = os.environ.get("EXA_API_KEY", "")
    if not key:
        raise HTTPException(501, "EXA_API_KEY not set")
    headers = {"x-api-key": key, "Content-Type": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as c:
        r = c.post("https://api.exa.ai/search",
                   json={"query": query, "num_results": 10, "type": "auto"})
        r.raise_for_status()
        data = r.json()
    return [{
        "title": it.get("title", ""),
        "url": it.get("url", ""),
        "snippet": (it.get("text") or "")[:400],
        "score": it.get("score", 0.0),
    } for it in data.get("results", [])]


SEARCH_FETCHERS = {"tavily": _search_tavily, "brave": _search_brave, "exa": _search_exa}


def _apply_request_keys(req: Any) -> None:
    """v9: 允许调用方(蜂群后端)随请求带搜索 key, 写入本进程 env, 让用户在前端填一次即可,
    无需单独配爬虫容器的 env/.env。只在请求带了非空 key 时覆盖。"""
    for field, env in (("tavily_api_key", "TAVILY_API_KEY"),
                       ("exa_api_key", "EXA_API_KEY"),
                       ("brave_api_key", "BRAVE_API_KEY")):
        v = getattr(req, field, None)
        if isinstance(v, str) and v.strip():
            os.environ[env] = v.strip()


class ScrapeTask(BaseModel):
    site: str
    keyword: str = ""
    limit: int = 20
    tavily_api_key: str | None = None
    exa_api_key: str | None = None
    brave_api_key: str | None = None


class AiPlanRequest(BaseModel):
    natural_language: str


class SearchQuery(BaseModel):
    query: str
    providers: list[str] = Field(default_factory=list)
    tavily_api_key: str | None = None
    exa_api_key: str | None = None
    brave_api_key: str | None = None


@router.post("/task")
def submit_task(req: ScrapeTask) -> dict:
    _apply_request_keys(req)
    if req.site not in SUPPORTED_SITES:
        raise HTTPException(400, f"unsupported site; use one of {SUPPORTED_SITES}")
    if req.site not in SITE_FETCHERS:
        raise HTTPException(501, f"site '{req.site}' not yet implemented")
    # 反爬熔断(保命底): 已熔断则直接停, 根本不发请求, 直到人工 reset
    allowed, why = _guard.check(req.site)
    if not allowed:
        return {"task_id": "", "status": "paused", "site": req.site,
                "count": 0, "items": [], "guard": why}
    try:
        items = SITE_FETCHERS[req.site](req.keyword, req.limit)
    except HTTPException as he:
        if he.status_code in (403, 429):
            _guard.record(req.site, "block")  # 被拦截信号 → 计入熔断
        raise
    except httpx.HTTPError as e:
        _guard.record(req.site, "block")
        raise HTTPException(502, f"upstream fetch failed: {e!r}") from e
    _guard.record(req.site, "empty" if not items else "ok")  # 上报本次信号
    tid = "s-" + uuid.uuid4().hex[:12]
    payload = {
        "task_id": tid, "site": req.site, "keyword": req.keyword,
        "ts": int(time.time()), "count": len(items), "items": items,
    }
    (DATA_DIR / f"{tid}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {"task_id": tid, "status": "done", "site": req.site,
            "count": len(items), "items": items}


@router.get("/guard/status")
def guard_status() -> dict:
    """查看熔断状态(是否触发保命底、各平台连续空计数、阈值配置)。"""
    return _guard.status()


@router.post("/guard/reset")
def guard_reset(body: dict = Body(default={})) -> dict:
    """人工恢复抓取。body 可传 {"platform": "xiaohongshu"} 只恢复单平台;不传=全清。"""
    return _guard.reset(body.get("platform"))


@router.post("/ai-plan")
def ai_plan(req: AiPlanRequest) -> dict:
    text = (req.natural_language or "").lower()
    sites: list[str] = []
    if any(k in text for k in ["ai", "ml", "论文", "model", "paper"]):
        sites += ["arxiv", "hacker_news"]
    if any(k in text for k in ["repo", "项目", "github", "代码", "star"]):
        sites += ["github_trending"]
    if any(k in text for k in ["model", "权重", "transformer", "llama"]):
        sites += ["huggingface"]
    if any(k in text for k in ["热搜", "新闻", "trending", "热点"]):
        sites += ["weibo_hot", "hacker_news"]
    if not sites:
        sites = ["hacker_news", "github_trending"]
    seen, dedup = set(), []
    for s in sites:
        if s not in seen:
            seen.add(s); dedup.append(s)
    return {"plan": [{"site": s, "limit": 20} for s in dedup]}


@router.post("/search/query")
def search_query(req: SearchQuery) -> dict:
    _apply_request_keys(req)
    use = req.providers or ["tavily", "brave", "exa"]
    bad = [p for p in use if p not in SEARCH_PROVIDERS]
    if bad:
        raise HTTPException(400, f"unknown providers: {bad}")
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for p in use:
        if p not in SEARCH_FETCHERS:
            errors[p] = "not implemented"
            continue
        try:
            results[p] = SEARCH_FETCHERS[p](req.query)
        except HTTPException as e:
            errors[p] = f"{e.status_code}: {e.detail}"
        except httpx.HTTPError as e:
            errors[p] = f"http error: {e!r}"
    return {"query": req.query, "providers": use,
            "results": results, "errors": errors}


@router.get("/data")
def get_data(task_id: str = "") -> dict:
    if not task_id:
        files = sorted(DATA_DIR.glob("s-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return {"recent": [f.stem for f in files[:20]]}
    f = DATA_DIR / f"{task_id}.json"
    if not f.exists():
        raise HTTPException(404, f"task {task_id} not found")
    return json.loads(f.read_text(encoding="utf-8"))


@router.get("/sites")
def list_sites() -> dict:
    return {
        "supported": SUPPORTED_SITES,
        "implemented": sorted(IMPL_SITES),
        "stub": sorted(STUB_SITES),
        "search_providers": SEARCH_PROVIDERS,
        "search_implemented": sorted(IMPL_SEARCH),
        "search_stub": sorted(STUB_SEARCH),
    }
