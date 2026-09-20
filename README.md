# ShareLink · 文件共享（分享码取件）

上传文件 → 设置有效期限 → 后端返回 **分享码** → 别人凭分享码下载 → 到期后文件自动删除。

## 特性

- 上传任意文件，自选有效期（10 分钟 / 1 小时 / 1 天 / 7 天 / 30 天，或自定义秒数）
- 后端返回 8 位分享码（去掉 `I/O/0/1` 等易混字符），大小写与空格容错
- 凭分享码下载，文件名、大小、剩余有效时间对取件人可见
- **到期自动删除**：后台定时任务扫描清理 + 取件时惰性校验双重保障
- 纯 HTML/CSS/JS 前端，无框架、无外网依赖
- 元数据落 SQLite，文件落磁盘，单机即可跑

## 快速开始

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 <http://127.0.0.1:8000/> 即可使用。

## 命令行用法

```bash
# 上传（返回分享码）
curl -F "file=@report.pdf" -F "ttl=1h" http://127.0.0.1:8000/api/upload

# 下载
curl -OJ http://127.0.0.1:8000/api/download/AB3D7K9M
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SHARELINK_DATA_DIR` | `./storage` | 上传文件存放目录 |
| `SHARELINK_DB_PATH` | `./data/sharelink.db` | SQLite 元数据库路径 |
| `SHARELINK_MAX_UPLOAD_MB` | `200` | 单文件大小上限 |
| `SHARELINK_DEFAULT_TTL` | `1h` | 未指定有效期时的默认值 |
| `SHARELINK_MIN_TTL_SECONDS` | `60` | 允许的最短有效期 |
| `SHARELINK_MAX_TTL_SECONDS` | `2592000` | 允许的最长有效期（30 天） |
| `SHARELINK_CLEANUP_INTERVAL` | `60` | 后台清理间隔（秒） |

## 测试

```bash
.venv/bin/python -m pytest -q
```

## 目录结构

```
app/        后端（FastAPI）
  config.py   环境变量与常量
  codes.py    分享码生成与规范化
  db.py       SQLite 元数据层
  storage.py  文件落盘 / 删除 / 校验
  cleanup.py  过期清理（惰性 + 后台定时）
  main.py     HTTP 接口与静态页面
static/     前端页面
tests/      pytest 用例
```
