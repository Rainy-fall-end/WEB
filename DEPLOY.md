# 公网部署指南

这份文档说明如何把 Search Console 部署到一台 Linux 服务器，并通过公网域名访问。

推荐架构：

```text
公网用户
  -> 域名 / HTTPS
  -> Nginx
  -> Gunicorn
  -> Django
  -> Playwright 搜索目标站
```

## 1. 服务器准备

推荐系统：

```text
Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS
```

需要开放端口：

```text
22   SSH
80   HTTP
443  HTTPS
```

登录服务器：

```bash
ssh root@你的服务器IP
```

安装基础依赖：

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx git
```

## 2. 上传项目

建议项目路径：

```bash
/opt/search-console
```

如果用 Git：

```bash
cd /opt
git clone 你的仓库地址 search-console
cd /opt/search-console
```

如果暂时不用 Git，可以本地打包后上传：

```bash
scp -r ./tmps root@你的服务器IP:/opt/search-console
```

注意不要把公开仓库里放入：

```text
tk55tk_config.json
tk55tk_output/
db.sqlite3
```

## 3. 安装 Python 依赖

```bash
cd /opt/search-console
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

安装 Playwright Chromium：

```bash
python -m playwright install chromium
python -m playwright install-deps chromium
```

脚本默认使用 headless Chromium，并在 Linux 下自动加入这些启动参数：

```text
--no-sandbox
--disable-setuid-sandbox
--disable-dev-shm-usage
--disable-gpu
```

这能减少服务器、Docker、root 用户环境下的浏览器启动问题。不要在无桌面服务器上使用 `--headed`。

## 4. 配置目标站账号

在服务器上创建或修改：

```bash
nano tk55tk_config.json
```

示例：

```json
{
  "url": "https://www.tk55tk.com/",
  "username": "你的账号",
  "password": "你的密码",
  "output_dir": "tk55tk_output",
  "search_keyword": "\u9752\u94dc",
  "search_limit": 3,
  "fetch_prices": true
}
```

生成登录态：

```bash
python login_tk55tk.py --headless
```

成功后应该出现：

```text
tk55tk_output/storage_state.json
```

如果后续目标站 Cookie 过期，重新执行这一条即可。

## 5. 配置 Django 生产参数

编辑：

```bash
nano searchsite/settings.py
```

上线前必须修改：

```python
DEBUG = False
SECRET_KEY = "换成一串很长的随机密钥"
ALLOWED_HOSTS = ["你的域名.com", "你的服务器IP"]
```

建议增加：

```python
STATIC_ROOT = BASE_DIR / "staticfiles"
```

如果后面用了 HTTPS，也建议加：

```python
CSRF_TRUSTED_ORIGINS = ["https://你的域名.com"]
```

## 6. 初始化数据库

```bash
python manage.py migrate
python manage.py createsuperuser
```

收集静态文件：

```bash
python manage.py collectstatic
```

## 7. 手动测试 Gunicorn

先手动启动一次：

```bash
gunicorn searchsite.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
```

另开一个 SSH 窗口测试：

```bash
curl http://127.0.0.1:8000/
```

如果能返回 HTML，说明 Django + Gunicorn 正常。

按 `Ctrl+C` 停掉手动 Gunicorn，下一步交给 systemd 管理。

## 8. 配置 systemd

创建服务文件：

```bash
nano /etc/systemd/system/search-console.service
```

内容：

```ini
[Unit]
Description=Search Console Django App
After=network.target

[Service]
User=root
WorkingDirectory=/opt/search-console
Environment="PATH=/opt/search-console/.venv/bin"
ExecStart=/opt/search-console/.venv/bin/gunicorn searchsite.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
systemctl daemon-reload
systemctl enable --now search-console
systemctl status search-console
```

查看日志：

```bash
journalctl -u search-console -f
```

## 9. 配置 Nginx

创建站点配置：

```bash
nano /etc/nginx/sites-available/search-console
```

内容：

```nginx
server {
    listen 80;
    server_name 你的域名.com;

    location /static/ {
        alias /opt/search-console/staticfiles/;
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

启用配置：

```bash
ln -s /etc/nginx/sites-available/search-console /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

现在访问：

```text
http://你的域名.com/
```

## 10. 配置 HTTPS

安装 Certbot：

```bash
apt install -y certbot python3-certbot-nginx
```

申请证书：

```bash
certbot --nginx -d 你的域名.com
```

完成后访问：

```text
https://你的域名.com/
```

## 11. 验证功能

首页：

```text
https://你的域名.com/
```

API：

```bash
curl "https://你的域名.com/api/search/?q=%E9%9D%92%E9%93%9C"
```

后台：

```text
https://你的域名.com/admin/
```

缓存是否命中：

```bash
curl "https://你的域名.com/api/search/?q=%E9%9D%92%E9%93%9C"
```

看响应里的：

```json
{
  "cache": {
    "hit": true
  }
}
```

第一次通常是 `false`，第二次应该是 `true`。

## 12. 常用运维命令

重启 Django 服务：

```bash
systemctl restart search-console
```

查看 Django 服务状态：

```bash
systemctl status search-console
```

查看 Django 日志：

```bash
journalctl -u search-console -f
```

重载 Nginx：

```bash
systemctl reload nginx
```

测试 Nginx 配置：

```bash
nginx -t
```

重新登录目标站：

```bash
cd /opt/search-console
source .venv/bin/activate
python login_tk55tk.py --headless
systemctl restart search-console
```

查看脚本日志：

```bash
tail -f /opt/search-console/tk55tk_output/login.log
tail -f /opt/search-console/tk55tk_output/search.log
```

如果 Chromium 启动失败，优先重新执行：

```bash
source /opt/search-console/.venv/bin/activate
python -m playwright install chromium
python -m playwright install-deps chromium
```

清空搜索缓存：

```bash
cd /opt/search-console
source .venv/bin/activate
python manage.py shell -c "from portal.models import SearchCache; SearchCache.objects.all().delete()"
```

## 13. 上线前检查清单

- `DEBUG = False`
- `SECRET_KEY` 已替换
- `ALLOWED_HOSTS` 已配置域名
- `CSRF_TRUSTED_ORIGINS` 已配置 HTTPS 域名
- `python manage.py migrate` 已执行
- `python manage.py collectstatic` 已执行
- `python login_tk55tk.py --headless` 已生成登录态
- Nginx 能访问首页
- HTTPS 证书已配置
- `/api/search/?q=青铜` 能返回 JSON
- 第二次搜索能命中缓存
- `tk55tk_config.json` 没有进公开仓库
- `tk55tk_output/storage_state.json` 没有进公开仓库

## 14. 后续生产增强

第一版可以用 SQLite。长期运行建议逐步升级：

- PostgreSQL 替代 SQLite
- Redis 做缓存
- Celery / RQ 做后台搜索任务
- 普通用户限流
- VIP 支付和到期续费
- 搜索结果缓存预热
- 登录态失效告警
- Playwright 超时告警
- Docker 化部署
