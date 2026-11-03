# V10 免费云端部署指南（Render）

## 你拿到的文件是什么？

这份是 V10 42 只股票完整代码，并添加 Render 免费部署配置。原有 Windows 启动文件保留，云端部署不使用 `.cmd` 或 `.bat`。

## 方法 A：Render 控制台手动部署（建议）

1. 在 https://github.com 登录后新建仓库（建议 **Private**）。
2. **先把 ZIP 解压**，将本目录的全部文件和文件夹上传到 GitHub 仓库根目录；不是上传 ZIP 这一个文件。上传后，仓库根目录应该能看到 `app.py`、`engine.py`、`reports.py`、`deploy_app.py`、`requirements.txt`、`render.yaml`、`static/`。
3. 打开 https://dashboard.render.com ，用 GitHub 账户登录，点击 **New > Web Service**，选择刚刚的仓库。
4. 按下表填写（不要用 Windows 本地的 `run_local.py`）：

| 设置 | 值 |
|---|---|
| Language | Python 3 |
| Branch | main（按你的 GitHub 实际分支） |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn deploy_app:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |
| Health Check Path（若有） | `/health` |

5. 如果只想自己看，建议在 Environment 中添加 `DASHBOARD_PASSWORD`，值为你自己设置的强密码。访问时用户名固定为 `viewer`；如果没设置密码，网址上的数据接口对所有能访问网址的人开放。
6. 点击 **Create Web Service**。看 Events/Logs 的部署结果；成功会获得 `https://你的服务名.onrender.com`。首页展示 42 只股票。
7. 分别试「更新全部42只」「更新 BOLL 与共振」「更新最新财报」。若网页正常但实时数据为空，先检查 Render Logs 是否被第三方财经数据接口拒绝或限流。

## 方法 B：Render Blueprint

`render.yaml` 已配置 Free 计划、构建命令、启动命令以及 `/health`。在 Render 中选择 **New > Blueprint** 并授权 GitHub 仓库后，可以按配置自动创建服务。随后仍建议在 Environment 添加 `DASHBOARD_PASSWORD`。

## 注意事项

- 免费 Render 在连续 15 分钟无请求后休眠，再次访问有冷启动等待。免费实例每个工作区每月合计 750 实例小时，另有带宽/构建分钟限制；达到额度可能暂停服务或产生账单，详情以 Render 当前套餐为准。
- Render 免费 Web 服务的**本地文件不是持久存储**。原项目使用 `.market_cache_v10.json` 和 `.report_index_v10.json`，休眠、重启或重新部署后可能消失，须重新拉取，不建议把它当成长期数据库。
- 中国的腾讯/东方财富/巨潮/新浪等数据接口可能对海外云服务器存在访问限制；网站可启动不等于每个实时数据源都可用。这个问题无法仅靠改启动命令解决。
- 后复权 HFQ 日/周/月 BOLL 及低位共振口径未改变。外部接口的实时数据准确性需要线上逐只核对。
- **不要把运行日志、密码或私密数据提交到公开 GitHub 仓库。** `.gitignore` 已忽略常见临时文件。
- 若 42 只批量刷新速度慢，可减少刷新频率；云端请求并不会让数据变成真正的交易所授权实时行情。

## 部署日志里常见情况

- `ModuleNotFoundError: fastapi`：看 Build Command 和 requirements 是否在仓库根目录。
- `No open ports detected`：确认 Start Command 包含 `--host 0.0.0.0 --port $PORT`。
- `Address already in use`：不要用本地 Windows 的启动脚本。
- 首页可开但「API读取失败」：在 Render Logs 中查具体财经接口网络错误/429/403。
- 页面首次打开慢：可能是免费实例休眠唤醒，属平台限制。

官方文档：https://render.com/docs/deploy-fastapi 、https://render.com/docs/free
