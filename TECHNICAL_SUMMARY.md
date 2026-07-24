# reCAPTCHA v2 音频绕过技术复盘

## 项目背景

ApplyKitty 面试题要求通过自动化方式绕过 Atos 招聘页面（`jobs.atos.net`）上的 reCAPTCHA v2 验证，进入 SuccessFactors 登录系统的下一步。目标页面通过 "Apply now" 按钮跳转到 `career5.successfactors.eu`，登录表单包含 `username`、`password` 和 `recaptcha_response_field` 三个字段，受 reCAPTCHA v2 保护（sitekey: `6LcIRvssAAAAABQFcFPxVEwioK9odHeHybgtzHjz`）。

技术约束：使用 Python，允许任何操作（靶场环境可恢复），不依赖付费 API。

## 技术方案选型

### 求解方式对比

| 方式 | 原理 | 成本 | 精度 | 适用性 |
|------|------|------|------|--------|
| 2captcha | 人工打码 API | ~$3/1000 | 高 | 通用，但需付费 |
| CapSolver | AI 打码 API | ~$1/1000 | 高 | 通用，但需付费 |
| 音频识别 | 下载音频挑战 → 语音识别 → 输入结果 | 免费 | 取决于识别引擎 | reCAPTCHA v2 专用 |

最终选择音频识别方案，原因：免费、无需 API Key、靶场环境允许。

### 识别引擎演进

| 引擎 | 精度 | 延迟 | 依赖 | 结果 |
|------|------|------|------|------|
| Google Speech Recognition | 低 | 在线，~2s | 需 WAV 格式转换 | 返回 "Llamar first"，失败 |
| faster-whisper (base, int8) | 接近 100% | 本地，~2s | 直接读 MP3，无额外依赖 | 三次运行均一次性通过 |

faster-whisper 基于 CTranslate2，不依赖 PyTorch，内置 PyAV 直接读取 MP3 文件，CPU 上 INT8 量化处理 4 秒音频约 2 秒。模型 `base` 约 74MB，首次下载后永久缓存。

### 浏览器自动化选型

从 Selenium 切换到 Playwright，原因：原生 async 支持、更好的 iframe 管理（无需手动 switch_to）、playwright-stealth 反检测插件成熟。

## 完整操作流程

### 流程概览

```
Atos 招聘页 → 点击 Apply now → SuccessFactors 登录页
    → 等待 reCAPTCHA 渲染 → 点击 checkbox
    → 切换音频挑战 → 下载音频 MP3
    → faster-whisper 识别 → 输入文本 → 点击验证
    → 填写账号 → 提交表单 → 验证结果
```

### 导航阶段

页面有 3 个 "Apply now" 链接，只有第 2 个（`/talentcommunity/apply/1246893201/`）会跳转到 SuccessFactors。第 1 个指向 Talent Community 落地页，不触发跳转。脚本逐个尝试每个链接，检测 URL 是否包含 `successfactors`，失败则关闭新标签页并回到源页面尝试下一个。

成功跳转后等待 reCAPTCHA 渲染：每秒检查 anchor iframe 内 `.recaptcha-checkbox-border` 元素是否存在。单纯检查 iframe URL 是否包含 "recaptcha" 不够可靠——iframe 可能已加载但 checkbox 元素尚未渲染。实际测试中，grecaptcha JS 对象在第 1 秒就加载完成，但 checkbox 元素需要 6-10 秒才出现。

### 音频求解阶段

点击 checkbox 后等待 3 秒检查 `aria-checked` 属性。如果未直接通过，切换到音频挑战：在 bframe iframe 中点击 `.rc-button-audio` 按钮。所有 reCAPTCHA 按钮点击均使用 `force=True`，因为 reCAPTCHA 的 overlay 元素会拦截常规点击。

音频 URL 从 `.rc-audiochallenge-tdownload-link` 的 `href` 属性获取，下载时添加 `Referer: https://www.google.com/recaptcha/` 头避免被拦截。

faster-whisper 直接读取下载的 MP3 文件，输出识别文本。reCAPTCHA 音频挑战的内容是英文短语（如 "It's a bit hard to integrate it."、"the activity section."、"You're training number?"），不是数字，因此识别结果直接作为答案输入 `#audio-response` 输入框。

### 表单提交与验证

点击 `#recaptcha-verify-button` 后等待 3 秒检查 checkbox 是否勾选。通过后填写测试账号并提交表单。服务器返回 "Invalid email address or password" 表示 reCAPTCHA 已通过——表单成功到达了服务器端验证流程，只是测试账号无效。

## 踩过的坑与解决方案

### Playwright Chromium 下载失败

**问题**：`playwright install chromium` 下载失败，网络超时。

**解决**：改用 `channel="chrome"` 启动系统已安装的 Chrome 浏览器，跳过 Chromium 下载。系统 Chrome 路径由 Playwright 自动检测。

### playwright-stealth v2 API 不兼容

**问题**：代码使用 `stealth_async(page)`，但 playwright-stealth v2 已移除该函数。

**解决**：改用 `Stealth` 类的 `apply_stealth_async(context)` 方法，在 BrowserContext 级别注入反检测脚本，而非 Page 级别。

### 导航跳转未触发

**问题**：点击 "Apply now" 后 `expect_page` 超时，页面未跳转。

**解决**：使用 `context.expect_page()` 监听新标签页，同时设置 URL 轮询作为兜底。部分链接是同页跳转（不打开新标签页），部分是新标签页，两种情况都要处理。

### Apply now 链接选择错误

**问题**：页面有 3 个 "Apply now" 链接，硬编码 `.nth(1)` 在页面结构变化时失效。第一个链接指向 Talent Community 落地页，不跳转到 SuccessFactors。

**解决**：遍历所有 "Apply now" 链接，逐个点击并检测 URL 是否包含 `successfactors`。失败时关闭新打开的标签页，重新加载源页面，尝试下一个链接。

### reCAPTCHA 按钮点击被遮挡

**问题**：点击 reCAPTCHA checkbox、音频切换按钮、验证按钮时，Playwright 报 "element is intercepted by another element"。

**解决**：所有 reCAPTCHA iframe 内的 `.click()` 调用添加 `force=True` 参数，绕过 Playwright 的可操作性检查。reCAPTCHA 的 overlay 层（透明 div 覆盖在按钮上方）会拦截常规点击事件。

### ffmpeg MP3 解码失败

**问题**：系统安装的 ffmpeg 编译时使用了 `--disable-everything`，无法解码 MP3。pydub 依赖 ffmpeg 进行格式转换，导致 MP3 → WAV 转换失败。

**解决历程**：
1. 先尝试 librosa + soundfile 替代 ffmpeg，但 librosa 首次运行需 numba JIT 编译，耗时 3 分钟
2. 最终引入 faster-whisper，其内置 PyAV 库直接读取 MP3，完全不需要 ffmpeg 或格式转换

### Google Speech Recognition 精度差

**问题**：Google Speech Recognition 对 reCAPTCHA 合成音频识别率极低，返回 "Llamar first" 等无关文本。

**根因**：reCAPTCHA 音频是合成语音，Google Speech Recognition 的模型对这类音频效果不佳。而且 reCAPTCHA 音频内容是英文短语而非数字，早期代码假设输出是数字并做数字提取，导致结果丢失。

**解决**：替换为 faster-whisper `base` 模型。三次实际运行识别结果：
- "It's a bit hard to integrate it." — 验证通过
- "the activity section." — 验证通过
- "You're training number?" — 验证通过

识别结果直接作为答案输入，不做数字提取（除非识别结果中确实包含数字）。

### librosa 首次加载延迟 3 分钟

**问题**：librosa 依赖 numba，首次调用触发 JIT 编译，耗时约 3 分钟，严重影响用户体验。

**解决**：faster-whisper 不依赖 numba，且模型加载在后台线程执行，导航期间并行下载。首次运行总耗时约 4 分钟（含模型下载 ~1 分钟），后续运行模型已缓存，总耗时降至约 1 分 30 秒。

### 验证结果判断逻辑错误

**问题**：`verify_result()` 先检查页面是否包含 "recaptcha" 字符串，如果包含就判定 reCAPTCHA 失败。但登录失败后页面会重新加载 reCAPTCHA，导致误判。

**解决**：调整判断优先级——先检查账号错误信息（如 "invalid email"、"incorrect" 等），如果检测到账号错误，说明表单已成功提交，reCAPTCHA 已通过。只有在没有错误信息且页面仍有 reCAPTCHA 时，才判定 reCAPTCHA 失败。

### Whisper 模型首次下载阻塞

**问题**：Whisper 模型首次加载需从 HuggingFace 下载约 74MB，如果同步加载会阻塞音频识别流程。

**解决**：实现 `WhisperModelManager` 单例类，在 `AudioRecaptchaSolver.__init__` 时启动后台线程预加载模型。导航阶段（约 30-60 秒）与模型下载并行执行，到音频识别时模型已就绪。后续运行从本地缓存加载，仅需 2-3 秒。

## 最终技术架构

### 文件结构

```
successfactor/
├── config.py              # 配置文件（目标 URL、Whisper 参数、重试次数等）
├── audio_solver.py        # 音频求解器（WhisperModelManager + AudioRecaptchaSolver）
├── recaptcha_bypass.py    # 主流程（导航 + 求解 + 表单提交）
├── captcha_solver.py      # API 求解器（2captcha / CapSolver，audio 模式不调用）
├── requirements.txt       # 依赖清单
└── screenshots/           # 运行截图
```

### 核心依赖

```
playwright>=1.40.0          # 浏览器自动化
playwright-stealth>=1.0.6   # 反检测
faster-whisper>=1.0.0       # 语音识别（主引擎）
SpeechRecognition>=3.10.0   # 语音识别（备选引擎）
pydub>=0.25.1               # MP3→WAV 转换（备选引擎用）
requests>=2.31.0            # HTTP 请求
numpy>=1.24.0               # 数值计算
```

### 关键配置项

| 配置 | 值 | 作用 |
|------|-----|------|
| `SOLVER_METHOD` | `"audio"` | 使用音频识别方案 |
| `AUDIO_RECOGNIZER` | `"whisper"` | 主识别引擎 |
| `WHISPER_MODEL_SIZE` | `"base"` | 模型大小（74MB） |
| `WHISPER_COMPUTE_TYPE` | `"int8"` | INT8 量化，CPU 最快 |
| `NAV_MAX_RETRIES` | `6` | 导航重试次数 |
| `RECAPTCHA_MAX_RETRIES` | `6` | 求解重试次数 |
| `RECAPTCHA_RENDER_WAIT` | `30` | 渲染等待秒数 |

## 经验总结

### 反检测是基础

Playwright 默认会在 `navigator.webdriver` 等属性上暴露自动化特征。playwright-stealth 通过注入脚本覆盖这些特征，配合真实 User-Agent 和 Accept-Language 头，基本能通过 reCAPTCHA 的初始检测。三次运行均未触发 "automated traffic" 拦截。

### force=True 是 reCAPTCHA 交互的关键

reCAPTCHA 的 UI 层有透明 overlay 覆盖在所有交互元素上方，Playwright 的可操作性检查（actionability check）会认为元素被遮挡而拒绝点击。`force=True` 跳过可操作性检查直接发送点击事件，是处理 reCAPTCHA iframe 内所有按钮点击的标准做法。

### 渲染检测要看元素而非 iframe

reCAPTCHA 的 iframe URL 在 JS 对象加载后就可能出现，但 checkbox 元素需要额外几秒才渲染。检测 `.recaptcha-checkbox-border` 元素是否存在，比检测 iframe URL 是否包含 "recaptcha" 更可靠。实际测试中两者之间有 5-9 秒的差距。

### 识别引擎选择决定成败

Google Speech Recognition 对合成语音的识别率不稳定，而 faster-whisper 即使是 `base` 级别的小模型也能准确识别 reCAPTCHA 的英文短语。关键差异在于 Whisper 是在多样化语音数据上训练的端到端模型，对合成语音有更好的泛化能力。

### 后台预加载消除等待感知

Whisper 模型首次下载约 1 分钟，放在后台线程与导航并行执行，用户感知不到等待。`WhisperModelManager` 单例模式确保模型只加载一次，所有求解尝试复用同一实例。这是一个小优化但显著提升了首次运行体验。

### 验证逻辑要区分 reCAPTCHA 失败和账号失败

表单提交后仍停留在登录页有两种可能：reCAPTCHA 未通过，或账号验证失败。后者页面会显示 "Invalid email" 等错误信息，且 reCAPTCHA 会重新加载。正确做法是优先检查账号错误关键词，而非先检查 reCAPTCHA 是否存在。
