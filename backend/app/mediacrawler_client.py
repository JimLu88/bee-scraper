# -*- coding: utf-8 -*-
"""mediacrawler_client — NAS bee-scraper → PC 端 bee-mediacrawler 的瘦客户端。

架构:重爬(浏览器农场)放 PC;NAS 通过局域网调它。
**故障安全**:未配置 / PC 关机 / 连接超时 → 返回 None,调用方自动降级到 NAS 本地 site_search。
连接超时设得很短(默认 2s),保证 PC 离线时"快速失败、立刻降级",不拖累决策。

env:
  BEE_MEDIACRAWLER_URL   PC 端地址, 如 http://192.168.31.100:8009 (空=禁用, 永远走降级)
  MC_CONNECT_TIMEOUT     连接超时秒 (默认 2, PC 离线时快速失败)
  MC_READ_TIMEOUT        读超时秒 (默认 200, MediaCrawler 抓取较慢)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

_URL = os.environ.get("BEE_MEDIACRAWLER_URL", "").strip().rstrip("/")
_CONNECT_TIMEOUT = float(os.environ.get("MC_CONNECT_TIMEOUT", "2"))
_READ_TIMEOUT = float(os.environ.get("MC_READ_TIMEOUT", "200"))


def enabled() -> bool:
    return bool(_URL)


def scrape(platform: str, keyword: str, limit: int = 8) -> dict[str, Any] | None:
    """调 PC 端重爬。返回 {ok, items, signal} 或 None。
    None = PC 不可用(未配置/关机/超时)→ 调用方应降级到 site_search。"""
    if not _URL:
        return None
    try:
        timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{_URL}/mc/scrape",
                       json={"platform": platform, "keyword": keyword, "limit": limit})
            if r.status_code >= 400:
                return None
            data = r.json()
            return data if isinstance(data, dict) else None
    except Exception:
        return None  # PC 关机 / 网络不通 / 超时 → 降级
