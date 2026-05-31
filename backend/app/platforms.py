"""platforms — 多平台爬取 (原生 API + 搜索引擎 site: 限定 + og:image 取图).

两类抓取:
1. native: 平台有公开 API/JSON, 直接拿结构化数据 (含图/视频).
   reddit / wikipedia / bilibili / fourchan / stackexchange / youtube
2. site: 强登录墙/反爬平台 (小红书/抖音/ins/tiktok/discord/淘宝/京东/拼多多...),
   走搜索引擎 site:domain 检索拿到 url+标题, 再抓页面 og:image 取封面.
   稳定、不需要养号、不会天天失效.

所有 fetcher 统一返回 list[dict], 字段:
  title, url, snippet, image_url, video_url, source, kind ("native"|"site")
缺失字段留空字符串. 任何失败 → 返回已拿到的部分 (绝不抛到主链路).
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

import httpx
from bs4 import BeautifulSoup

HTTP_TIMEOUT = float(os.environ.get("BEE_SCRAPER_TIMEOUT", "20"))
UA = "Mozilla/5.0 (compatible; bee-scraper/0.3; +https://github.com/h-semas)"
_HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


# ============================================================ og:image 抓取
def fetch_og_image(url: str, timeout: float = 8.0) -> str:
    """抓页面 <meta og:image> / twitter:image / 首张大图. 失败返回 ''。"""
    if not url or not url.startswith("http"):
        return ""
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as c:
            r = c.get(url)
            if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                return ""
            soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        return ""
    for sel, attr in [
        ('meta[property="og:image"]', "content"),
        ('meta[name="og:image"]', "content"),
        ('meta[property="og:image:url"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[name="twitter:image:src"]', "content"),
        ('link[rel="image_src"]', "href"),
    ]:
        el = soup.select_one(sel)
        if el and el.get(attr):
            img = str(el.get(attr)).strip()
            if img.startswith("//"):
                img = "https:" + img
            if img.startswith("http"):
                return img
    return ""


def enrich_images(items: list[dict[str, Any]], max_fetch: int = 6) -> list[dict[str, Any]]:
    """对前 max_fetch 条没有 image_url 的项, 抓 og:image 补图 (限量防慢)."""
    fetched = 0
    for it in items:
        if fetched >= max_fetch:
            break
        if not it.get("image_url") and it.get("url"):
            img = fetch_og_image(str(it["url"]))
            if img:
                it["image_url"] = img
            fetched += 1
    return items


# ============================================================ 搜索引擎 (site: 限定)
def _search_via_tavily(query: str, n: int = 10) -> list[dict[str, Any]]:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return []
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.post("https://api.tavily.com/search", json={
            "api_key": key, "query": query, "max_results": n,
            "search_depth": "basic", "include_images": True, "include_answer": False,
        })
        if r.status_code >= 400:
            return []
        data = r.json()
    imgs = data.get("images") or []
    out: list[dict[str, Any]] = []
    for i, it in enumerate(data.get("results", [])):
        out.append({
            "title": it.get("title", ""), "url": it.get("url", ""),
            "snippet": (it.get("content") or "")[:300],
            "image_url": (imgs[i] if i < len(imgs) and isinstance(imgs[i], str) else ""),
        })
    return out


def _search_via_brave(query: str, n: int = 10) -> list[dict[str, Any]]:
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return []
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as c:
        r = c.get("https://api.search.brave.com/res/v1/web/search",
                  params={"q": query, "count": n})
        if r.status_code >= 400:
            return []
        data = r.json()
    out: list[dict[str, Any]] = []
    for it in (data.get("web") or {}).get("results", []):
        thumb = ""
        th = it.get("thumbnail") or {}
        if isinstance(th, dict):
            thumb = th.get("src") or th.get("original") or ""
        out.append({
            "title": it.get("title", ""), "url": it.get("url", ""),
            "snippet": (it.get("description") or "")[:300], "image_url": thumb,
        })
    return out


def search_engine(query: str, n: int = 10) -> list[dict[str, Any]]:
    """通用搜索: 先 tavily (带图) 再 brave 兜底."""
    out = _search_via_tavily(query, n)
    if not out:
        out = _search_via_brave(query, n)
    return out


def site_search(domains: list[str], query: str, *, n: int = 8, enrich: bool = True) -> list[dict[str, Any]]:
    """site:domain 限定搜索 + (可选) og:image 补图. 返回归一 list."""
    site_q = " OR ".join(f"site:{d}" for d in domains)
    full_q = f"({site_q}) {query}".strip()
    results = search_engine(full_q, n)
    # 只保留命中目标域名的
    kept: list[dict[str, Any]] = []
    for it in results:
        url = str(it.get("url") or "")
        if any(d in url for d in domains):
            it["kind"] = "site"
            kept.append(it)
    if not kept:  # 域名过滤后空 → 放宽 (搜索引擎已加 site: 提示, 直接用)
        for it in results:
            it["kind"] = "site"
        kept = results
    if enrich:
        kept = enrich_images(kept, max_fetch=5)
    return kept[:n]


# ============================================================ 原生 API fetchers
_REDDIT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_reddit(query: str, n: int = 10) -> list[dict[str, Any]]:
    """Reddit 官方 search .json (无需登录). 带缩略图/视频.

    Reddit 会封通用 UA (403); 用真实浏览器 UA, 失败则降级到 site:reddit.com 搜索.
    """
    url = "https://www.reddit.com/search.json"
    params = {"q": query or "popular", "limit": max(1, min(n, 25)), "sort": "relevance"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT,
                          headers={"User-Agent": _REDDIT_UA, "Accept": "application/json"}) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        # 403 / 网络问题 → 退化成搜索引擎 site: 限定 (一定有结果)
        res = site_search(["reddit.com"], query or "", n=n)
        for it in res:
            it["source"] = "reddit"
        return res
    out: list[dict[str, Any]] = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        img = ""
        prev = d.get("preview", {})
        try:
            img = prev["images"][0]["source"]["url"].replace("&amp;", "&")
        except Exception:
            th = d.get("thumbnail", "")
            img = th if isinstance(th, str) and th.startswith("http") else ""
        video = ""
        try:
            video = d.get("media", {}).get("reddit_video", {}).get("fallback_url", "") or ""
        except Exception:
            video = ""
        out.append({
            "title": d.get("title", ""),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "snippet": (d.get("selftext") or "")[:300],
            "image_url": img, "video_url": video,
            "source": f"reddit/{d.get('subreddit', '')}", "kind": "native",
            "score": d.get("score", 0),
        })
    return out


def fetch_wikipedia(query: str, n: int = 8, lang: str = "zh") -> list[dict[str, Any]]:
    """Wikipedia REST: 搜索 + 每条取缩略图摘要."""
    base = f"https://{lang}.wikipedia.org/w/api.php"
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=_HEADERS) as c:
        r = c.get(base, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": query, "gsrlimit": max(1, min(n, 20)),
            "prop": "pageimages|extracts", "exintro": 1, "explaintext": 1,
            "piprop": "thumbnail", "pithumbsize": 400, "exsentences": 2,
        })
        r.raise_for_status()
        data = r.json()
    pages = (data.get("query") or {}).get("pages") or {}
    out: list[dict[str, Any]] = []
    for p in pages.values():
        title = p.get("title", "")
        out.append({
            "title": title,
            "url": f"https://{lang}.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "snippet": (p.get("extract") or "")[:300],
            "image_url": (p.get("thumbnail") or {}).get("source", ""),
            "source": "wikipedia", "kind": "native",
        })
    return out


def fetch_bilibili(query: str, n: int = 10) -> list[dict[str, Any]]:
    """B 站搜索 API (web). 带封面 pic + 时长."""
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={**_HEADERS, "Referer": "https://www.bilibili.com"}) as c:
        # 先访问首页拿 cookie (b 站搜索需要 buvid)
        try:
            c.get("https://www.bilibili.com")
        except Exception:
            pass
        r = c.get("https://api.bilibili.com/x/web-interface/search/type", params={
            "search_type": "video", "keyword": query or "热门", "page": 1,
        })
        r.raise_for_status()
        data = r.json()
    items = ((data.get("data") or {}).get("result")) or []
    out: list[dict[str, Any]] = []
    for it in items[: max(1, min(n, 20))]:
        pic = it.get("pic", "")
        if isinstance(pic, str) and pic.startswith("//"):
            pic = "https:" + pic
        title = re.sub(r"<[^>]+>", "", it.get("title", ""))
        out.append({
            "title": title,
            "url": it.get("arcurl") or f"https://www.bilibili.com/video/{it.get('bvid', '')}",
            "snippet": (it.get("description") or "")[:300],
            "image_url": pic, "video_url": it.get("arcurl", ""),
            "source": f"bilibili/{it.get('author', '')}", "kind": "native",
        })
    return out


def fetch_fourchan(query: str, n: int = 10, board: str = "g") -> list[dict[str, Any]]:
    """4chan (2chan 同类) 官方 JSON catalog. 默认 /g/ 科技板; 关键词过滤."""
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=_HEADERS) as c:
        r = c.get(f"https://a.4cdn.org/{board}/catalog.json")
        r.raise_for_status()
        pages = r.json()
    kw = (query or "").lower()
    out: list[dict[str, Any]] = []
    for page in pages:
        for th in page.get("threads", []):
            text = (th.get("sub", "") + " " + th.get("com", "")).lower()
            if kw and kw not in text:
                continue
            sub = re.sub(r"<[^>]+>", "", th.get("sub", "") or th.get("com", "")[:80])
            img = ""
            if th.get("tim") and th.get("ext"):
                img = f"https://i.4cdn.org/{board}/{th['tim']}{th['ext']}"
            out.append({
                "title": sub or "(no subject)",
                "url": f"https://boards.4channel.org/{board}/thread/{th.get('no')}",
                "snippet": re.sub(r"<[^>]+>", "", th.get("com", ""))[:300],
                "image_url": img, "source": f"4chan/{board}", "kind": "native",
            })
            if len(out) >= n:
                return out
    return out


def fetch_stackexchange(query: str, n: int = 10, site: str = "stackoverflow") -> list[dict[str, Any]]:
    """StackExchange API (Quora 同类技术问答). site 可换 superuser/serverfault/..."""
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=_HEADERS) as c:
        r = c.get("https://api.stackexchange.com/2.3/search/advanced", params={
            "order": "desc", "sort": "relevance", "q": query, "site": site,
            "pagesize": max(1, min(n, 20)), "filter": "withbody",
        })
        r.raise_for_status()
        data = r.json()
    out: list[dict[str, Any]] = []
    for it in data.get("items", []):
        body = re.sub(r"<[^>]+>", "", it.get("body", ""))[:300]
        out.append({
            "title": it.get("title", ""), "url": it.get("link", ""),
            "snippet": body, "source": f"stackexchange/{site}", "kind": "native",
            "score": it.get("score", 0),
        })
    return out


def fetch_youtube(query: str, n: int = 10) -> list[dict[str, Any]]:
    """YouTube: 优先官方 Data API (YOUTUBE_API_KEY), 否则 site:youtube.com 搜索兜底."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if key:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=_HEADERS) as c:
            r = c.get("https://www.googleapis.com/youtube/v3/search", params={
                "key": key, "q": query, "part": "snippet", "type": "video",
                "maxResults": max(1, min(n, 20)),
            })
            if r.status_code < 400:
                data = r.json()
                out: list[dict[str, Any]] = []
                for it in data.get("items", []):
                    vid = (it.get("id") or {}).get("videoId", "")
                    sn = it.get("snippet") or {}
                    out.append({
                        "title": sn.get("title", ""),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "snippet": (sn.get("description") or "")[:300],
                        "image_url": ((sn.get("thumbnails") or {}).get("high") or {}).get("url", ""),
                        "video_url": f"https://www.youtube.com/watch?v={vid}",
                        "source": "youtube", "kind": "native",
                    })
                return out
    # 兜底: site 搜索
    res = site_search(["youtube.com"], query, n=n)
    for it in res:
        it["source"] = "youtube"
        it["video_url"] = it.get("url", "")
    return res


# ============================================================ site: 平台域名注册表
# 强反爬/登录墙平台 → 走搜索引擎 site: 限定. 多域名时任一命中即可.
SITE_PLATFORMS: dict[str, list[str]] = {
    "zhihu":        ["zhihu.com"],
    "xiaohongshu":  ["xiaohongshu.com", "xhslink.com"],
    "douyin":       ["douyin.com"],
    "instagram":    ["instagram.com"],
    "tiktok":       ["tiktok.com"],
    "discord":      ["discord.com", "discord.gg"],
    "quora":        ["quora.com"],
    "xiaoheihe":    ["xiaoheihe.cn", "heybox.cn"],   # 小黑盒 (游戏)
    "taobao":       ["taobao.com", "tmall.com"],
    "jd":           ["jd.com"],
    "pinduoduo":    ["pinduoduo.com", "yangkeduo.com"],
    "douban":       ["douban.com"],
    "pinterest":    ["pinterest.com"],
    "smzdm":        ["smzdm.com"],                    # 什么值得买
    "dianping":     ["dianping.com"],                 # 大众点评
    "mafengwo":     ["mafengwo.cn"],                  # 马蜂窝
    "xiachufang":   ["xiachufang.com"],               # 下厨房
    "weixin":       ["mp.weixin.qq.com"],             # 微信公众号
    "medium":       ["medium.com"],
    "weibo":        ["weibo.com", "weibo.cn"],
    "xueqiu":       ["xueqiu.com"],                   # 雪球 (股票/投资)
    "dribbble":     ["dribbble.com"],                 # 设计作品
    "xianyu":       ["goofish.com", "2.taobao.com"],  # 闲鱼 (二手/收藏)
}


def make_site_fetcher(platform: str) -> Callable[[str, int], list[dict[str, Any]]]:
    domains = SITE_PLATFORMS[platform]

    def _fetch(keyword: str, n: int = 8) -> list[dict[str, Any]]:
        items = site_search(domains, keyword or "", n=n)
        for it in items:
            it.setdefault("source", platform)
        return items

    return _fetch


# 原生 fetcher 注册 (signature: (keyword, n) -> list)
NATIVE_FETCHERS: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {
    "reddit": fetch_reddit,
    "wikipedia": fetch_wikipedia,
    "bilibili": fetch_bilibili,
    "fourchan": fetch_fourchan,
    "stackexchange": fetch_stackexchange,
    "youtube": fetch_youtube,
}


def all_platform_fetchers() -> dict[str, Callable[[str, int], list[dict[str, Any]]]]:
    """全平台 fetcher = 原生 + site (供 scraper.py 注册)."""
    out: dict[str, Callable[[str, int], list[dict[str, Any]]]] = dict(NATIVE_FETCHERS)
    for plat in SITE_PLATFORMS:
        out[plat] = make_site_fetcher(plat)
    return out
