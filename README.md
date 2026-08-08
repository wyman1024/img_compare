# 像素对照台

一个运行在局域网内的静态图片对比工具。它提供两种创建模式：

- **图片对比**：上传 2–8 张图片，可同步并排查看，也可任选两张进行滑杆和全屏对比。
- **批量图集**：整理 1–30 组 A/B 图片，以瀑布流展示；点击卡片后可使用与普通对比一致的滑杆、缩放、平移、标题字号和全屏工具栏。

每次发布都会生成随机唯一地址。图片和项目数据只保存在运行服务的内网机器上，服务重启后链接仍然有效；没有账号、公开项目列表或云端上传。

## 启动

1. 在服务机器上安装 Python 3.10 或更高版本。
2. 双击 `start.bat`。
3. 首次运行会自动创建独立环境并安装依赖，随后浏览器会打开首页。
4. 启动窗口会列出局域网地址，例如 `http://192.168.1.20:8000`。同一局域网内的设备使用这个地址访问。

关闭启动窗口即可停止服务。如果项目被复制到另一台机器，不要复制 `.venv`；如果误复制后无法启动，删除 `.venv` 再运行 `start.bat` 即可重建。

> Windows 首次运行时可能弹出防火墙提示。请允许 Python 在“专用网络”中通信，否则其他局域网设备无法访问。

## 使用

首页提供统一的图片对比入口：

1. **图片对比**：默认提供 A/B 两个槽位，可通过“＋”增加到 A–H 共 8 张图片，并设置顺序和可选图片标题。发布后可共享缩放和平移，也可选择任意两张覆盖滑动；标题会悬浮在画面底部，并可在顶部工具栏调整字号。查看时按 `Shift + F` 可进入或退出全屏。
2. **批量图集**：逐组添加图片，在核对清单中改名、交换、替换、删除或排序，最后统一上传。上传最多同时进行 3 个文件；失败时只需重试失败项。

支持 PNG、JPEG、WebP、GIF 和 BMP 静态图片，默认单张最大 20 MB、单项目最大 512 MB。批量图集最多 30 组（60 张），单个图片对比项目最多 8 张。发布后的页面只读；若要调整内容，请重新创建项目。

不要分享以 `localhost` 或 `127.0.0.1` 开头的地址，其他设备应使用启动窗口显示的局域网 IP。随机链接本身就是访问凭据：同一网络中知道链接的人可以查看图片，也可以打开首页创建项目，因此只应在可信局域网中运行。

## 数据目录

- 已发布项目：`data/projects/<项目 ID>`
- 未完成草稿：`data/drafts/<草稿 ID>`，默认 24 小时后清理
- 旧版目录：`data/comparisons`，仅作为不可访问备份保留，不迁移、不扫描，也不会自动删除

发布通过同一数据盘上的目录原子重命名完成。原图旁会生成最长边 960 px、质量 82 的 WebP 预览。删除项目或释放空间前，请先停止服务并自行备份需要保留的目录。

## 可选设置

在启动 `start.bat` 前设置环境变量即可调整行为：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `PORT` | `8000` | 服务端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `MAX_IMAGE_BYTES` | `20971520` | 单张图片最大字节数 |
| `MAX_PROJECT_BYTES` | `536870912` | 单项目原图总字节数 |
| `MIN_FREE_DISK_BYTES` | `536870912` | 低于该剩余空间时停止接收图片 |
| `DRAFT_TTL_SECONDS` | `86400` | 未发布草稿的有效期 |
| `PREVIEW_MAX_EDGE` | `960` | WebP 预览最长边像素数 |
| `PREVIEW_QUALITY` | `82` | WebP 预览质量（1–100） |
| `DATA_DIR` | 项目内的 `data` | 项目和草稿保存目录 |
| `OPEN_BROWSER` | `1` | 设为 `0` 时启动后不自动打开浏览器 |
| `SHARE_BASE_URL` | 自动识别 | 指定分享地址；飞书预览需使用公司内网 FQDN，如 `http://img-compare.sg.cn:8000` |
| `FEISHU_APP_ID` | 空 | 企业自建应用的 App ID；与 App Secret 同时设置后启用飞书长连接预览 |
| `FEISHU_APP_SECRET` | 空 | 企业自建应用的 App Secret；仅保存在服务机器的启动环境中 |
| `FEISHU_PREVIEW_IMAGE_KEY` | 空 | 可选的飞书图片 key，用作链接标题前的统一小图标；不设置时只显示标题 |

例如要改用 9000 端口，可先在命令提示符执行 `set PORT=9000`，再运行 `start.bat`。

## 飞书链接预览（内网长连接）

服务通过飞书官方 SDK 主动建立 WebSocket 长连接。只要服务机器能够访问公网，就不需要公网 IP、Webhook 请求地址或内网穿透。当前支持两类分享页面：

- 图片对比：`/analyze/<项目 ID>`
- 批量图集：`/gallery/<项目 ID>`

已有的 `/compare/<项目 ID>` 双图对比链接也可以生成预览。发布项目时，服务会选取前几张图片合成为一张 1200×630 的静态拼图并上传到飞书，缓存返回的 `image_key`；回调只读缓存并返回标题与图片卡片，因此不会在 3 秒回调时限内临时上传。项目原图不会上传到飞书。`FEISHU_PREVIEW_IMAGE_KEY` 仍只用于标题前的统一小图标。

纯标题预览不依赖机器人；图片卡片需要使用飞书“上传图片”接口，因此同一个自建应用必须开启 **机器人能力** 并申请 **获取与上传图片或文件资源（`im:resource`）** 权限。机器人只用于获得 `image_key`，不需要加入联系人会话或群聊。

配置步骤：

1. 为服务准备公司内网可解析的完整域名，例如 `img-compare.sg.cn`，并设置 `SHARE_BASE_URL=http://img-compare.sg.cn:8000`。飞书 URL 规则不接受 IP 或 `jz-tools-server` 这种单段主机名；端口 `8000` 可以保留。
2. 创建 **企业自建应用**，在“应用能力”中添加“链接预览”。长连接暂不支持商店应用。
3. 在链接预览能力中注册以下 URL 规则。配置时不要写 `http://` 或 `https://`；无通配符规则会自动匹配所有子路径。

   ```text
   img-compare.sg.cn:8000/analyze
   img-compare.sg.cn:8000/gallery
   ```

   如仍在分享 `/compare/` 链接，再添加 `img-compare.sg.cn:8000/compare`。
4. 在“应用能力”中添加 **机器人**，并在“权限管理”中开通 **获取与上传图片或文件资源（`im:resource`）**。无需把机器人添加到任何会话。
5. 从应用“凭证与基础信息”获取 App ID 和 App Secret。复制 `config.local.bat.example` 为 `config.local.bat`，填写 `SHARE_BASE_URL`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`。该本机配置文件已被 Git 忽略；两项凭证必须同时设置。
6. 启动服务。窗口会显示“飞书预览：长连接正在建立”。SDK 日志默认不会打印包含临时连接凭据的完整 WebSocket URL。
7. 在飞书开发者后台进入“事件与回调 → 回调配置”，将订阅方式改为 **使用长连接接收回调**。保存时本服务必须正在运行并已连接。
8. 添加“链接预览 → 拉取链接预览数据（`url.preview.get`）”回调，然后创建并发布正式应用版本。机器人能力、权限和链接预览配置都必须包含在这个已发布版本中；测试版本不支持链接预览，且预览只对应用可用范围内的用户生效。
9. 重启图片工具，在飞书中重新发送以 `http://` 或 `https://` 开头、使用上述完整内网域名的项目 URL。

启动后可访问 `http://img-compare.sg.cn:8000/api/feishu/status` 查看诊断状态。`configured`、`worker_running`、`client_initialized` 应为 `true`；发送测试链接后，`last_callback_at` 应出现时间，`last_callback_result` 应为 `matched`，`last_callback_card_ready` 应为 `true`。`card_preview.last_error_code` 和 `card_preview.last_error_message` 可用于判断机器人能力或图片权限是否缺失。该接口不会返回 App ID 或 App Secret。

新发布项目会在发布响应前做一次有界预热。为了避免把从未分享过的历史图片上传到飞书，旧项目只在链接实际触发预览时按需生成并上传；旧项目第一次可能仍只有标题，请等待 `card_preview.queued` 变为 `0` 后重新发送。飞书已经生成的旧消息不会自动改成新卡片；如果客户端仍复用旧预览，可在链接末尾临时加 `?preview=1` 触发一次新预览。

也可以不创建配置文件，改为在命令提示符中临时配置并启动：

```bat
set SHARE_BASE_URL=http://img-compare.sg.cn:8000
set FEISHU_APP_ID=cli_xxx
set FEISHU_APP_SECRET=你的_App_Secret
start.bat
```

如需前缀小图标，可另行设置 `FEISHU_PREVIEW_IMAGE_KEY`。项目内容图片由服务自动合成和上传，该配置不控制卡片封面。

官方参考：[使用长连接接收回调](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/step-1-choose-a-subscription-mode/configure-callback-request-address?lang=zh-CN)、[链接预览开发指南](https://open.feishu.cn/document/development-link-preview/link-preview-development-guide?lang=zh-CN)、[上传图片 API](https://open.feishu.cn/document/server-docs/im-v1/image/create?lang=zh-CN)。

## 开发与测试

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests -q
```

测试只使用临时数据目录，不会修改 `data` 中的真实项目。
