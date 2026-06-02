# bee-scraper (耳目) · 集成说明

> 数据爬取微服务。本文档供其它程序调用本服务时阅读。
> 服务端口 **8003** · 鉴权 Bearer Token · OpenAPI 文档 `http://127.0.0.1:8003/docs`

---

## 一、启动方式

### 1. 开发模式 (Python)
```powershell
cd "D:\AI\AI 数据爬虫\backend"
py -3.11 -m pip install -r requirements.txt
py -3.11 -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

### 2. 通过托盘看门狗
打开 `D:\AI\AI 蜂群系统\h-semas\tray_watchdog.pyw`，右键托盘 → 数据爬虫 → 启动。

### 3. 作为独立 EXE
见仓库根 `D:/AI/scripts/build_exe.ps1`（生成 `dist/bee-scraper.exe` 可双击运行）。

### 4. 健康检查
```powershell
(Invoke-WebRequest http://127.0.0.1:8003/healthz).Content
# 期望: {"status":"ok","service":"bee-scraper","port":"8003"}
```

---

## 二、鉴权

所有 `/scraper/**` 与 `/logs/**` 端点都需要 Bearer header：

```
Authorization: Bearer <BEE_BEARER_TOKEN>
```

默认值 `dev-token-change-me`（通过环境变量 `BEE_BEARER_TOKEN` 改）。

---

## 三、关键端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/healthz` | GET | 无需鉴权；返回 `{status,service,port}` |
| `/manifest` | GET | 无需鉴权；返回所有 endpoints schema |
| `/scraper/sites` | GET | 已实现 / 占位的站点 + 搜索源清单 |
| `/scraper/task` | POST | `{site,keyword?,limit}` → 真去抓数据 |
| `/scraper/search/query` | POST | `{query,providers?}` → 多 provider 搜索 |
| `/scraper/ai-plan` | POST | `{natural_language}` → 启发式生成爬取计划 |
| `/scraper/data?task_id=` | GET | 按 task_id 取已缓存结果 |
| `/logs/recent?limit=200&level=` | GET | 拉本服务最近日志 |
| `/logs/stats` | GET | 日志级别计数 |

---

## 四、已实现 vs 占位

| 类别 | 已真实现 | 占位 (返 501) |
|---|---|---|
| 站点 | hacker_news, arxiv, github_trending, huggingface, weibo_hot | papers_with_code, product_hunt, github, xueqiu, reddit, bilibili, juejin, sspai, _36kr, huxiu, infoq |
| 搜索 | tavily, brave, exa (需 API Key) | perplexity, zhipu, bing_cn, search360, semantic_scholar, google_scholar, github_code, sourcegraph, npm, pypi |

---

## 五、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BEE_BEARER_TOKEN` | dev-token-change-me | 鉴权 token |
| `BEE_SCRAPER_TIMEOUT` | 20 | HTTP 抓取超时秒 |
| `TAVILY_API_KEY` | (空) | 启用 tavily 搜索 |
| `BRAVE_API_KEY` | (空) | 启用 brave 搜索 |
| `EXA_API_KEY` | (空) | 启用 exa 搜索 |
| `GITHUB_TOKEN` | (空) | 提升 GitHub Search API 配额 |

---

## 六、调用示例

### Python (httpx)
```python
import httpx
BASE = "http://127.0.0.1:8003"
HEADERS = {"Authorization": "Bearer dev-token-change-me"}

r = httpx.post(f"{BASE}/scraper/task",
               json={"site": "hacker_news", "limit": 5},
               headers=HEADERS, timeout=20)
data = r.json()  # {task_id, status:"done", count, items:[...]}
```

### PowerShell
```powershell
$body = @{site="arxiv"; keyword="reasoning"; limit=10} | ConvertTo-Json
$h = @{Authorization="Bearer dev-token-change-me"}
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8003/scraper/task `
                  -ContentType "application/json" -Body $body -Headers $h
```

### TypeScript (前端)
```ts
const res = await fetch("http://127.0.0.1:8003/scraper/task", {
  method: "POST",
  headers: {"Content-Type": "application/json", Authorization: "Bearer dev-token-change-me"},
  body: JSON.stringify({site: "github_trending", keyword: "stars:>1000 language:rust", limit: 10}),
});
const data = await res.json();
```

---

## 七、日志

JSONL 格式滚动日志：
- 路径：`backend/data/logs/bee-scraper.log`
- 轮转：5MB × 3 备份
- 在线查看：`GET /logs/recent?limit=200&level=ERROR`
- 蜂群主程序的 **📋 日志** 按钮可一次性看 7 个服务

---

## 八、故障排查

| 症状 | 原因 | 修法 |
|---|---|---|
| 401 invalid bearer token | header 没带或 token 不对 | 检查 `BEE_BEARER_TOKEN` |
| 502 upstream fetch failed | 网络断 / 目标站点挂 | 重试；翻墙；换站点 |
| 429 github rate limit | 未带 GitHub token | 设 `GITHUB_TOKEN` |
| 501 not implemented | 用了占位站点 | 用 `IMPL_SITES` 中的站点 |
| 启动报 import 失败 | 缺依赖 | `pip install -r requirements.txt` |
