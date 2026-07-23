# -*- coding: utf-8 -*-
"""scrape_guard — 反爬熔断器(保命底)。

目的:抓取一旦出现"被风控/被频繁查"的迹象(封号 / 验证码 / 连续被拦 / 连续空结果),
就**自动熔断**:停止继续抓取、持久化熔断状态、企业微信告警。
**熔断后只能人工 reset 才恢复** —— 即"触发底就停,告诉你,由你决定是否继续"。

信号 signal(由抓取层在每次请求后上报):
  ok       正常拿到数据
  empty    返回 0 条(可能被降级/风控,也可能正常无结果 → 看连续次数)
  block    403 / 429 / 风控拦截页
  captcha  命中验证码(浏览器层接入后上报)
  auth_expired 登录态过期，需要扫码续期；不属于封禁，不触发熔断
  ban      账号明确被封(浏览器层上报)→ 立即熔断

阈值(环境变量可调):
  GUARD_DISABLED=1        关闭熔断(不建议)
  GUARD_SCOPE            "platform"(默认: 单平台熔断) | "global"(任一触发→全停)
  GUARD_WINDOW_SEC       滑动窗口秒数(默认 600)
  GUARD_MAX_BLOCK        窗口内 block 次数上限(默认 5)
  GUARD_MAX_CAPTCHA      窗口内 captcha 次数上限(默认 3)
  GUARD_MAX_EMPTY_STREAK 连续 empty 次数上限(默认 12)
  WECOM_WEBHOOK_URL      企业微信机器人 webhook(熔断时告警)

设计:纯标准库,状态落盘 data/guard_state.json,加锁防并发写。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

_STATE_PATH = Path(__file__).parent.parent / "data" / "guard_state.json"
_LOCK = threading.Lock()
_GLOBAL = "__global__"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def _cfg() -> dict[str, Any]:
    return {
        "disabled": os.environ.get("GUARD_DISABLED", "0") == "1",
        "scope": os.environ.get("GUARD_SCOPE", "platform"),
        "window": _int_env("GUARD_WINDOW_SEC", 600),
        "max_block": _int_env("GUARD_MAX_BLOCK", 5),
        "max_captcha": _int_env("GUARD_MAX_CAPTCHA", 3),
        "max_empty_streak": _int_env("GUARD_MAX_EMPTY_STREAK", 12),
    }


def _load() -> dict[str, Any]:
    try:
        d = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("tripped", {})
            d.setdefault("events", {})
            d.setdefault("empty_streak", {})
            return d
    except Exception:
        pass
    return {"tripped": {}, "events": {}, "empty_streak": {}}


def _save(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _notify(reason: str) -> None:
    """企业微信告警(best-effort, 失败不抛)。"""
    url = os.environ.get("WECOM_WEBHOOK_URL", "").strip()
    if not url:
        return
    text = ("🛑 **爬虫熔断触发(保命底)**\n"
            f"原因: {reason}\n"
            "已停止相关抓取。确认安全后,调用 `/guard/reset` 或在系统里点恢复,再继续。")
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": text}}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=6)
    except Exception:
        pass


def _prune(events: list, now: float, window: int) -> list:
    return [e for e in events if isinstance(e, list) and len(e) == 2 and now - e[0] <= window]


def check(platform: str) -> tuple[bool, str]:
    """抓取前调用。返回 (是否允许, 原因)。已熔断 → (False, 原因)。"""
    cfg = _cfg()
    if cfg["disabled"]:
        return True, ""
    with _LOCK:
        state = _load()
        tripped = state.get("tripped", {})
        if _GLOBAL in tripped:
            return False, f"[全局熔断] {tripped[_GLOBAL].get('reason', '')}"
        if platform in tripped:
            return False, f"[{platform} 熔断] {tripped[platform].get('reason', '')}"
    return True, ""


def record(platform: str, signal: str) -> dict[str, Any] | None:
    """抓取后上报信号。若因此触发熔断 → 返回熔断信息 dict(并已告警),否则 None。"""
    cfg = _cfg()
    if cfg["disabled"]:
        return None
    now = time.time()
    with _LOCK:
        state = _load()
        events = _prune(state["events"].get(platform, []), now, cfg["window"])
        events.append([now, signal])
        state["events"][platform] = events

        # 连续空结果计数(非 empty 即清零)
        streak = int(state["empty_streak"].get(platform, 0))
        streak = streak + 1 if signal == "empty" else 0
        state["empty_streak"][platform] = streak

        # 判定熔断
        reason = ""
        if signal == "ban":
            reason = "账号明确被封(ban)"
        else:
            n_block = sum(1 for _, s in events if s == "block")
            n_captcha = sum(1 for _, s in events if s == "captcha")
            if n_block >= cfg["max_block"]:
                reason = f"{cfg['window']}s 内被拦截 {n_block} 次(block≥{cfg['max_block']})"
            elif n_captcha >= cfg["max_captcha"]:
                reason = f"{cfg['window']}s 内验证码 {n_captcha} 次(captcha≥{cfg['max_captcha']})"
            elif streak >= cfg["max_empty_streak"]:
                reason = f"连续 {streak} 次空结果(疑似被风控降级,empty_streak≥{cfg['max_empty_streak']})"

        if not reason:
            _save(state)
            return None

        key = _GLOBAL if cfg["scope"] == "global" else platform
        full_reason = f"{platform}: {reason}" if key == _GLOBAL else reason
        info = {"reason": full_reason, "platform": platform, "signal": signal, "ts": int(now)}
        state["tripped"][key] = info
        _save(state)
    _notify(full_reason)  # 锁外告警, 不阻塞
    return info


def status() -> dict[str, Any]:
    with _LOCK:
        state = _load()
    cfg = _cfg()
    return {
        "tripped": state.get("tripped", {}),
        "is_tripped": bool(state.get("tripped")),
        "empty_streak": state.get("empty_streak", {}),
        "config": cfg,
    }


def reset(platform: str | None = None) -> dict[str, Any]:
    """人工恢复。platform=None → 全清(含全局);否则只清该平台 + 全局。"""
    with _LOCK:
        state = _load()
        if platform is None:
            cleared = list(state.get("tripped", {}).keys())
            state["tripped"] = {}
            state["events"] = {}
            state["empty_streak"] = {}
        else:
            cleared = []
            for k in (platform, _GLOBAL):
                if k in state.get("tripped", {}):
                    state["tripped"].pop(k, None)
                    cleared.append(k)
            state["events"].pop(platform, None)
            state["empty_streak"].pop(platform, None)
        _save(state)
    return {"reset": cleared, "remaining_tripped": list(state.get("tripped", {}).keys())}
