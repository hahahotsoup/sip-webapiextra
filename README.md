# 🍲 sip-web — sip 的本地 Web 界面

> 品，你细品。

> ## ⚠️ 测试软件声明
>
> **这是一个测试 / 实验性质的软件，没有安全功能。**
>
> - 没有身份认证、没有访问控制、没有加密
> - 任何能访问到服务端口的人，都可以读写你的 sip 数据（添加/删除订阅源、删除文章、抓取等）
> - 请只在本地开发环境中使用，**不要部署到公网**，不要监听非回环地址，不要在共享/不受信环境中运行
> - 使用风险自负，作者不对数据丢失或泄露负责

给 **sip**（本地优先的 RSS 阅读器）开发的一个 Web 界面。它把浏览器的 **Web 请求翻译成 HTTP 调用**——具体说，是一个轻量 HTTP 服务，把每个请求翻译成 `sip <命令> --json` 的 CLI 调用，再把 sip 的结构化输出原样返回给前端渲染。

**零第三方依赖**：后端只用 Python 标准库，前端是单页 HTML。Windows / macOS / Linux 通用。

## 用法：把程序放到 sip 文件夹下

`sip-web` 需要找到 `sip` 可执行文件才能工作，部署方式就是——**把整个文件夹放到 `sip`（sip.exe / sip）所在的文件夹里**：

```
sip.exe          ← 你的 sip 程序
readwithhotsoup/ ← 你的数据目录（sip 自动创建）
sip-web.py       ← 本程序（Web 服务器 + 翻译层）
index.html       ← Web 界面
start-sip-web.bat / start-sip-web.sh
```

然后启动：

```bash
# Windows：双击 start-sip-web.bat，或在命令行
python sip-web.py

# macOS / Linux
./start-sip-web.sh
```

浏览器打开 **http://127.0.0.1:8777** 即可使用。

> 为什么要放一起？sip 的数据目录在 `readwithhotsoup/`（exe 同级），翻译层会以 sip 所在目录为工作目录调用它，保证读写的是同一份数据。

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--port 9000` | 监听端口（默认 8777） |
| `--host 0.0.0.0` | 监听地址（默认 127.0.0.1，本地优先） |
| `--sip /path/to/sip` | 指定 sip 可执行文件路径（默认找脚本同目录） |
| `--timeout 300` | 单次 CLI 调用超时秒数 |

## Web 界面功能

| 界面 | 后端翻译成的 sip 命令 |
|------|----------------------|
| 🏠 概览（订阅统计 + 今日哈汤） | `sip -l` / `sip --today` |
| 📡 订阅源列表 / 文章列表 | `sip -l` / `sip -l <编号>` |
| 📖 文章阅读（HTML 正文 / 全文） | `sip --show <id> --json` |
| 📄 全文搜索 | `sip --grep <词> --json` |
| 🧠 语义搜索 | `sip --search <词> --json` |
| 🍵 今日哈汤（含今日变化摘要） | `sip --today [--refresh] --json` |
| ➕ 添加订阅源 | `sip -d <url>` |
| 🔄 同步 / 全更 | `sip --sync` / `sip --update-all` |
| 🗄 归档 / 去归档 / 删除 | `sip -a` / `sip -una` / `sip -r --yes` |
| ♥ 收藏 / 收藏列表 | `sip --like <id>` / `sip --likes` |
| 📥 抓全文 | `sip --fulltext <id> --yes --json` |
| 📜 版本历史 / ⇄ 改动对比 | `sip --versions <id>` / `sip --diff <id> --json` |
| ✨ 生成摘要（需 AI 配置） | `sip --summary <id> --json` |

## HTTP API（翻译层）

所有端点返回 sip 的原始 JSON 结构（`{"success":true,"data":{...}}` 或 `{"success":false,"error":{...}}`），方便直接对接其他工具。

```
GET    /api/status                     sip 版本与连通状态
GET    /api/feeds                      订阅源列表
POST   /api/feeds            {url}     添加订阅源
GET    /api/feeds/{id}                 某源文章列表（?limit=N）
GET    /api/feeds/{id}/info            来源健康信息
POST   /api/feeds/{id}/update          更新某源
POST   /api/feeds/{id}/archive         归档
POST   /api/feeds/{id}/unarchive       去归档
DELETE /api/feeds/{id}                 删除源（--yes）
POST   /api/feeds/sync                 只更新到期的源
POST   /api/feeds/update-all           强制更新全部
GET    /api/articles/{id}              文章详情（含正文）
GET    /api/articles/{id}/versions     版本历史
GET    /api/articles/{id}/diff         改动对比（?from=v&to=v）
POST   /api/articles/{id}/fulltext     抓全文
DELETE /api/articles/{id}/fulltext     清除全文缓存
POST   /api/articles/{id}/like         收藏/取消
POST   /api/articles/{id}/summary      生成摘要
GET    /api/likes                      收藏列表
GET    /api/search/grep?q=…            全文搜索（?feed=N&limit=N）
GET    /api/search/semantic?q=…        语义搜索（?feed=N&threshold=0.7）
GET    /api/today?refresh=1            今日哈汤
GET    /api/config                     AI 配置状态
```

## ⚠️ 已知限制（无安全功能）

- **无认证 / 无授权**：默认监听 `127.0.0.1` 只是让它只有本机能访问，但本机任何进程/用户访问该端口即可操作数据
- **无加密**：HTTP 明文传输；数据本身仍受 sip 自己的保护（如孟思琳加密挡位），但 Web 层不提供任何防护
- **无沙箱**：翻译层把请求直接翻译成 sip CLI 调用，命令参数未经语义校验（虽然不经过 shell）
- **测试用途**：未经安全审计、无测试覆盖承诺，请勿在生产环境或公网使用

## 开发

```
sip-web.py      后端：HTTP 服务器 + sip CLI 翻译层（Python 标准库）
index.html      前端：单页应用（原生 JS，无构建）
```

后端 `SipTranslator` 的 `run()` 就是翻译核心：`[sip 路径] + 参数 + --json --ignoresafeannouncement` → 子进程执行 → 解析 JSON → 返回。

GPL-3.0
