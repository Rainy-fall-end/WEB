# Search Console

这是一个基于 Django 的聚合搜索网站 MVP。当前搜索来源只有 `TK55TK`，后端会复用已有的 Playwright 登录态，到目标站执行搜索，进入前三条结果详情页读取售价，并按 `1 人民币 = 10 通宝` 换算价格。

后续可以继续接入多个网站，把它做成一个统一搜索入口。Django 负责网站、账号、后台、VIP 权限和搜索日志；每个外部网站只需要新增一个 adapter。

## 当前能力

- 搜索首页：输入关键词，展示结果列表和价格。
- JSON API：适合后续接前端、App 或其他服务。
- 详情页价格抓取：返回通宝价格和人民币换算。
- 搜索结果缓存：同一关键词、来源、条数和价格权限命中缓存时直接返回。
- 后台管理：管理搜索来源、VIP 用户和搜索日志。
- VIP 雏形：可控制用户单次搜索条数、是否查看价格。
- 多来源扩展结构：当前只有一个来源，但代码已经按聚合搜索组织。

## 项目结构

```text
.
├── manage.py
├── requirements.txt
├── login_tk55tk.py              # 登录目标站，生成 storage_state.json
├── search_tk55tk.py             # TK55TK 搜索 adapter，也可命令行单独运行
├── tk55tk_config.json           # 本地配置，包含账号密码，不要提交公开仓库
├── searchsite/                  # Django 项目配置
├── portal/                      # 搜索页面、API、VIP/日志/来源模型
├── templates/                   # 页面模板
├── static/                      # CSS 静态文件
└── tk55tk_output/
    ├── storage_state.json       # Playwright 登录态
    ├── latest.html/png          # 登录调试文件
    ├── search_latest.html/png   # 搜索调试文件
    └── details/                 # 搜索结果详情页快照
```

## 本地运行

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python .\login_tk55tk.py --headless
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python login_tk55tk.py --headless
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

如果 Linux 服务器缺少 Chromium 系统依赖，执行：

```bash
python -m playwright install-deps chromium
```

访问地址：

- 搜索首页：http://127.0.0.1:8000/
- JSON API：http://127.0.0.1:8000/api/search/?q=青铜
- 管理后台：http://127.0.0.1:8000/admin/

公网部署流程见 [DEPLOY.md](DEPLOY.md)。

## 配置文件

`tk55tk_config.json` 示例：

```json
{
  "url": "https://www.tk55tk.com/",
  "username": "Rainy_fall",
  "password": "your-password",
  "output_dir": "tk55tk_output",
  "search_keyword": "\u9752\u94dc",
  "search_limit": 3,
  "fetch_prices": true
}
```

说明：

- `username` / `password`：目标站登录账号。
- `output_dir`：保存登录态、截图、HTML 快照的位置。
- `search_keyword`：命令行调试时的默认关键词。
- `search_limit`：默认返回条数。
- `fetch_prices`：是否进入详情页读取售价。

这个文件包含敏感信息，已经被 `.gitignore` 忽略。上线时建议改成环境变量或服务器私有配置文件。

## 登录态

第一次部署或登录态失效时，先运行：

```bash
python login_tk55tk.py --headless
```

成功后会生成：

```text
tk55tk_output/storage_state.json
```

搜索脚本和网站 API 都依赖这个登录态。目标站账号失效、Cookie 过期或服务器 IP 被限制时，需要重新登录。

## 搜索命令行调试

直接搜索默认关键词：

```bash
python search_tk55tk.py --headless
```

指定关键词：

```bash
python search_tk55tk.py 青铜 --headless
```

不进入详情页抓价格：

```bash
python search_tk55tk.py 青铜 --headless --no-fetch-prices
```

返回 JSON 示例：

```json
{
  "keyword": "青铜",
  "count": 3,
  "exchange_rate": {
    "rmb": 1,
    "tongbao": 10
  },
  "results": [
    {
      "title": "标题",
      "url": "https://www.tk55tk.com/forum.php?mod=viewthread&tid=123",
      "snippet": "摘要",
      "meta": "发布时间或其他信息",
      "price": {
        "tongbao": 400,
        "rmb": 40,
        "source_text": "售价: 400 东周列国通宝"
      }
    }
  ]
}
```

## 网站 API

GET:

```text
/api/search/?q=关键词
```

示例：

```bash
curl "http://127.0.0.1:8000/api/search/?q=%E9%9D%92%E9%93%9C"
```

API 会返回聚合后的结果。现在只有一个来源，后面多个来源接入后，结果里的 `source` 字段会区分来自哪个网站。

## 缓存机制

搜索结果会写入数据库缓存表 `SearchCache`，默认缓存一周：

```text
7 天 = 604800 秒
```

缓存 key 由这些字段生成：

- 关键词
- 启用的搜索来源
- 每次返回条数 `limit`
- 是否抓取价格 `fetch_prices`

命中缓存时，系统不会再打开 Playwright 浏览器，也不会访问目标站，会直接返回数据库里保存的 JSON。响应里会带：

```json
{
  "cache": {
    "hit": true,
    "ttl_seconds": 604800,
    "expires_at": "2026-06-17T16:42:05.124766+00:00"
  }
}
```

未命中缓存时：

```json
{
  "cache": {
    "hit": false,
    "ttl_seconds": 604800,
    "expires_at": "2026-06-17T16:42:05.124766+00:00"
  }
}
```

清理全部缓存：

```bash
python manage.py shell -c "from portal.models import SearchCache; SearchCache.objects.all().delete()"
```

只清理过期缓存：

```bash
python manage.py shell -c "from django.utils import timezone; from portal.models import SearchCache; SearchCache.objects.filter(expires_at__lte=timezone.now()).delete()"
```

## 后台和 VIP

创建后台管理员：

```bash
python manage.py createsuperuser
```

后台可管理：

- `SearchSource`：搜索来源，例如 TK55TK、未来新增的网站。
- `VipSubscription`：用户 VIP 信息，包括单次搜索条数、是否可看价格。
- `SearchLog`：搜索记录、来源、结果数量和错误信息。
- `SearchCache`：搜索结果缓存，默认一周过期。

当前权限逻辑：

- 未登录用户：默认最多返回 `SEARCH_DEFAULT_LIMIT` 条。
- 普通登录用户：默认最多返回 `SEARCH_DEFAULT_LIMIT` 条。
- VIP 用户：按 `VipSubscription.search_limit_per_query` 限制。
- `VipSubscription.can_view_prices = false` 时，不抓详情页价格。

默认值在 `searchsite/settings.py`：

```python
SEARCH_DEFAULT_LIMIT = 3
SEARCH_VIP_LIMIT = 10
```

## 多网站扩展方式

每个新网站写一个 adapter，函数签名建议和 `search_tk55tk.search_tk55tk` 保持一致：

```python
async def search_xxx(
    keyword=None,
    config_path=None,
    url=None,
    output_dir=None,
    limit=3,
    timeout_ms=15000,
    slow_mo=100,
    headless=True,
    fetch_prices=True,
    allow_config_price_override=True,
):
    return {
        "keyword": keyword,
        "count": 0,
        "exchange_rate": {"rmb": 1, "tongbao": 10},
        "results": []
    }
```

然后在后台新增 `SearchSource`：

```text
name: New Site
slug: new-site
base_url: https://example.com/
adapter_path: adapters.search_new_site.search_xxx
enabled: true
```

`portal/services.py` 会自动遍历启用的来源，把结果合并返回。

## Linux 上线建议

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

初始化：

```bash
python login_tk55tk.py --headless
python manage.py migrate
python manage.py createsuperuser
```

生产运行建议使用 Gunicorn：

```bash
gunicorn searchsite.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
```

前面用 Nginx 做反向代理，域名转发到 `127.0.0.1:8000`。

上线前必须处理：

- 把 `DEBUG` 改成 `False`。
- 把 `SECRET_KEY` 换成安全随机值。
- 把 `ALLOWED_HOSTS` 改成你的域名和服务器 IP。
- 不要把 `tk55tk_config.json`、`storage_state.json`、`db.sqlite3` 放进公开仓库。
- 给 `tk55tk_output/` 设置合适权限，确保运行用户可写。
- 给 Playwright 搜索设置超时和错误告警。

## Nginx 示例

```nginx
server {
    listen 80;
    server_name example.com;

    location /static/ {
        alias /path/to/project/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 后续路线

短期：

- 增加注册页面。
- 增加用户搜索额度。
- 增加搜索结果缓存，避免重复触发目标站搜索。
- 把目标站账号密码改成环境变量。

中期：

- 接入多个搜索来源。
- 增加任务队列，比如 Celery/RQ，避免请求卡住网页。
- 做 VIP 套餐、支付回调、到期续费。
- 增加搜索结果去重、排序和价格过滤。

上线前：

- 使用 PostgreSQL 替代 SQLite。
- 使用 Redis 缓存搜索结果和限流。
- 使用 HTTPS。
- 增加日志、监控和异常告警。
- 给外部站点访问失败、验证码、登录失效做明确提示。
