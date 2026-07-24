# reCAPTCHA v2 自动化绕过工具

基于 Python、Playwright 和 AI 图像识别的多策略 reCAPTCHA v2 绕过工具。仅供教育目的和授权测试环境使用。

> **实测状态说明**: 目前仅**音频识别方案**能够稳定通过验证。AI 图像识别方案偶发通过，成功率较低。其他方案为探索性实现，尚未达到稳定可用状态。详见下方各方案实测状态。

## 功能特性

**7 种求解策略** — 从免费音频识别到零痕迹 OS 级自动化：

| # | 策略 | 费用 | 原理 | 核心技术 | 实测状态 |
|---|------|------|------|----------|----------|
| 1 | 音频识别 | 免费 | 下载音频挑战 → 语音转文字 | faster-whisper (INT8 量化) | **稳定通过** |
| 2 | 2captcha API | ~$3/1000次 | 提交 sitekey 到人工打码服务 | REST API | 需付费, 未实测 |
| 3 | AI 图像识别 | 免费 | YOLOv8 分类 + 分割 + CLIP | 三引擎架构 | 偶发通过, 成功率低 |
| 4 | 无障碍 Cookie | 免费 | 使用 accessibility cookie 跳过挑战 | Cookie 注入 | 依赖有效 cookie |
| 5 | 浏览器扩展 | 免费 | NopeCHA 扩展自动求解 | Chrome 扩展 | 需付费扩展 |
| 6 | Stealth + 真人行为 | 免费 | 反检测指纹 + 模拟人类行为 | patchright + 贝塞尔曲线 | 探索性, 不稳定 |
| 7 | 原生零痕迹 | 免费 | OS 级点击 + Win32 坐标校准 + YOLO 回退 | patchright + PyAutoGUI | 探索性, 不稳定 |

> **推荐方案**: 如需稳定绕过, 请使用音频识别方案 (`python main.py -m audio`)。图像识别方案中 YOLOv8-cls 对 13 类标准目标 (如 Bus/Hydrant) 置信度达 99%+, 但 CLIP 回退对非标准类别精度不足, 多轮挑战成功率受限。原生零痕迹方案在 checkbox 点击和指纹清除方面验证有效, 但触发图像挑战后求解仍依赖图像识别引擎。

### 三引擎图像识别架构

```
挑战网格 → 引擎选择 → Tile 匹配 → 点击 + 验证

┌──────────────────────────────────────────────────────┐
│  3x3 网格 (9 个 tile)                                │
│  ├── YOLOv8-cls (13类微调模型, 99.88% 准确率)       │
│  └── CLIP 回退 (非标准类别)                          │
│                                                      │
│  4x4 网格 (16 个 tile)                               │
│  ├── YOLOv8-seg (COCO 分割 + 重叠比例检测)          │
│  └── YOLOv8-cls 排序模式 (回退)                      │
│                                                      │
│  任意网格 → CLIP (零样本, 排序选择)                   │
└──────────────────────────────────────────────────────┘
```

### 原生零痕迹策略 (最先进)

在 OS 层面绕过 reCAPTCHA 检测 — 无 CDP 协议、无 `Runtime.enable` 泄露：

- **patchright** `launch_persistent_context` — 消除 `webdriver` 标记和 `cdc_` 痕迹
- **Win32 坐标校准** — `GetClientRect` + `ClientToScreen` 实现 DPI 自适应 checkbox 定位
- **PyAutoGUI OS 级点击** — 生成 `isTrusted=true` 鼠标事件
- **螺旋搜索** — 坐标偏移时自动以扩展搜索模式修正
- **YOLO 三引擎回退** — 触发图像挑战时用 AI 求解

## 项目结构

```
.
├── main.py                    # 统一入口 (GUI / CLI / 直接模式)
├── gui.py                     # PyQt6 GUI (模型预加载 + 优先级队列)
├── config.py                  # 配置文件 (从环境变量读取凭据)
├── solutions.py               # 方案注册和依赖检查
├── requirements.txt           # Python 依赖
│
├── core/                      # 共享基础设施
│   ├── base_runtime.py        # 基础运行时: 浏览器初始化、导航、表单提交
│   ├── model_loader.py        # 后台模型预加载 (QThread)
│   ├── task_queue.py          # 四级优先级队列 + 背压控制
│   ├── persistence.py         # QSettings + SQLite (WAL 模式)
│   └── window_chrome.py       # Win32 Chrome 窗口管理
│
├── runtimes/                  # 求解策略 (7 种)
│   ├── runtime_audio.py       # 音频识别 (faster-whisper)
│   ├── runtime_api.py         # 2captcha / CapSolver API
│   ├── runtime_image.py       # AI 图像识别 (YOLO + CLIP)
│   ├── runtime_cookie.py      # 无障碍 Cookie
│   ├── runtime_extension.py   # NopeCHA 浏览器扩展
│   ├── runtime_stealth.py     # Stealth + 真人行为模拟
│   └── runtime_native.py      # 零痕迹 OS 级 (patchright + PyAutoGUI)
│
├── audio_solver.py            # 音频挑战求解器 (Whisper)
├── captcha_solver.py          # API 求解器 (2captcha / CapSolver)
├── recaptcha_bypass.py        # 旧版入口
│
├── models/                    # 预训练模型
│   └── recaptcha_cls_best.pt  # YOLOv8-cls 微调 (13 个 reCAPTCHA 类别)
│
├── extensions/nopecha/        # NopeCHA 扩展占位
└── run_e2e_test.py            # 端到端测试
```

## 环境要求

- **Python 3.10+**
- **Windows 10/11** (原生策略需要 Win32 API + PyAutoGUI)
- **Chrome 浏览器** (Playwright 通过 `channel="chrome"` 使用系统 Chrome)

### Python 依赖

```bash
pip install -r requirements.txt
```

核心依赖：
- `playwright` + `playwright-stealth` — 浏览器自动化 + 反检测
- `patchright` — 无 CDP 泄露的 Playwright 分支 (原生策略)
- `faster-whisper` — 语音识别 (音频策略, INT8 量化)
- `ultralytics` — YOLOv8 推理 (图像策略)
- `transformers` + `torch` — CLIP 模型 (图像回退)
- `PyQt6` — GUI 框架
- `pyautogui` + `pywin32` — OS 级鼠标控制和 Win32 API

安装后执行：
```bash
playwright install chromium
```

## 配置

所有敏感配置从环境变量读取：

```bash
# Windows
set ACCOUNT_EMAIL=your_email@gmail.com
set ACCOUNT_PASSWORD=your_password

# 可选: 付费策略的 API 密钥
set TWOCAPTCHA_API_KEY=your_key
set CAPSOLVER_API_KEY=your_key
```

也可以直接编辑 `config.py` 填写你的值。

### 关键配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SOLVER_METHOD` | `"audio"` | 默认求解策略 |
| `BROWSER_HEADLESS` | `False` | 无头模式 (可能触发更多挑战) |
| `NAV_MAX_RETRIES` | `6` | 导航重试次数 |
| `RECAPTCHA_MAX_RETRIES` | `6` | 求解重试次数 |
| `IMAGE_RANK_SCORE_GAP` | `0.45` | CLIP 自适应间隔阈值 |
| `NATIVE_CLICK_RESULT_WAIT` | `30` | OS 点击后等待响应时间 (秒) |

## 使用方法

### GUI 模式 (默认)

```bash
python main.py
```

启动 PyQt6 图形界面，包含：
- 方案选择器 (带依赖状态检测)
- 实时日志查看器 (智能滚动)
- 模型预加载进度
- 运行历史和成功率统计

### CLI 模式

```bash
# 列出可用策略
python main.py --list

# 运行指定策略
python main.py -m native
python main.py -m audio
python main.py -m image

# 检查依赖
python main.py --check
```

### 直接脚本

```bash
python recaptcha_bypass.py
```

## 技术亮点

### 反检测 (最小化干预原则)

只修补真实 Chromium 缺失的部分 — 绝不覆盖真实指纹值：

- `navigator.webdriver`: `false` → `undefined` (patchright 处理)
- `window.chrome.runtime`: 仅在缺失时补充
- `cdc_` 痕迹: 从 document 中清除
- 真实 WebGL 渲染器、插件、硬件并发数: **保持不动** (避免一致性矛盾)

### Win32 坐标校准

无需截图即可解决 DPI 自适应的 checkbox 定位问题：

```
GetClientRect (排除窗口边框)
  → ClientToScreen (物理屏幕原点)
  → + Chrome UI 高度 (client_h - innerH × DPI)
  → + checkbox CSS 坐标 × 真实 DPI
  → PyAutoGUI 可用的物理像素坐标
```

### 页面状态管理

全流程 `page.is_closed()` 检查，防止 `TargetClosedError` 级联异常：
- 每次截图前检查
- 表单提交前检查
- 结果验证前检查
- 自定义 asyncio 异常处理器将 `TargetClosedError` 降级为 debug 日志

### 四级优先级队列

```
CRITICAL (100) → 用户交互、实时 UI 更新
HIGH (50)      → 实时日志更新
NORMAL (0)     → 后台任务
LOW (-50)      → 维护、日志采样
```

带背压控制: 待处理任务超过 200 时拒绝 LOW/NORMAL 任务，采样丢弃 90% 的 INFO 日志。

## 测试

```bash
# 端到端测试
python run_e2e_test.py

# 路由测试
python run_routes_test.py

# 原生策略测试
python run_native_test.py
```

## 免责声明

本工具仅供**教育目的**和**授权测试环境**使用。使用者需遵守适用法律和服务条款。作者不鼓励任何未经授权或恶意的使用。

## 开源协议

MIT License — 详见 [LICENSE](LICENSE)。

## 致谢

- [ETH Zurich "Breaking reCAPTCHAv2"](https://github.com/aplesner/Breaking-reCAPTCHAv2) — YOLOv8-cls 微调模型来源
- [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) — 无 CDP 泄露的 Playwright 分支
- [playwright-stealth](https://github.com/Mattwmaster58/playwright_stealth) — 反检测脚本
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 快速语音识别
- [ultralytics](https://github.com/ultralytics/ultralytics) — YOLOv8 框架
