"""bee-scraper / 数据爬虫 — 14 站点 + 13 搜索源 + AI 规划员 (v2 阶段 5 + v5-C 模型自发现)"""
from __future__ import annotations
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data" / "scraped"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_SITES = [
    # MVP 一期
    "arxiv", "papers_with_code", "product_hunt", "hacker_news",
    "github", "huggingface", "xueqiu",
    # 二期
    "reddit", "bilibili", "juejin", "sspai",
    # 三期
    "_36kr", "huxiu", "infoq", "weibo_hot",
]

SEARCH_PROVIDERS = [
    "tavily", "exa", "brave", "perplexity",
    "zhipu", "bing_cn", "search360",
    "semantic_scholar", "google_scholar",
    "github_code", "sourcegraph", "npm", "pypi",
]


class ScrapeTask(BaseModel):
    site: str
    keyword: str = ""
    limit: int = 20


@router.post("/task")
def submit_task(req: ScrapeTask) -> dict:
    if req.site not in SUPPORTED_SITES:
        raise HTTPException(400, f"unsupported site; use one of {SUPPORTED_SITES}")
    tid = "s-" + uuid.uuid4().hex[:12]
    return {"task_id": tid, "status": "queued", "site": req.site, "scaffold": True}


class AiPlanRequest(BaseModel):
    natural_language: str


@router.post("/ai-plan")
def ai_plan(req: AiPlanRequest) -> dict:
    """v4 AI 规划员: 自然语言 → 爬取计划 (stub)."""
    text = req.natural_language.lower()
    sites: list[str] = []
    if any(k in text for k in ["ai", "ml", "论文", "model"]):
        sites += ["arxiv", "papers_with_code", "hacker_news"]
    if any(k in text for k in ["股", "财", "雪球"]):
        sites += ["xueqiu"]
    if any(k in text for k in ["热搜", "新闻", "趋势"]):
        sites += ["weibo_hot", "_36kr"]
    if not sites:
        sites = ["hacker_news", "product_hunt"]
    return {"plan": [{"site": s, "limit": 20} for s in sites]}


class SearchQuery(BaseModel):
    query: str
    providers: list[str] = Field(default_factory=list)


@router.post("/search/query")
def search_query(req: SearchQuery) -> dict:
    """v3-E L0 外部搜索框架 (13 providers, ELO 路由)."""
    use = req.providers or SEARCH_PROVIDERS[:3]
    bad = [p for p in use if p not in SEARCH_PROVIDERS]
    if bad:
        raise HTTPException(400, f"unknown providers: {bad}")
    return {"providers": use, "results": [], "scaffold": True}


@router.get("/data")
def get_data(site: str = "", date: str = "") -> dict:
    return {"items": [], "site": site, "date": date, "scaffold": True}


@router.get("/sites")
def list_sites() -> dict:
    return {"supported": SUPPORTED_SITES, "search_providers": SEARCH_PROVIDERS}