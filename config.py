"""
配置文件 - reCAPTCHA 自动化绕过
支持多种验证码求解方案
"""

import os

# ============================================================
# 目标网站配置
# ============================================================
# 源网站 (Atos 招聘页面, 点击 Apply now 跳转到登录页)
SOURCE_URL = "https://jobs.atos.net/job/Senior-PLM-Consultant-%28mwd%29/1246893201/?feedId=365901&utm_source=CareerSite&utm_campaign=Atos_CorpSite"
# 登录页 (跳转后的目标)
TARGET_URL = "https://career5.successfactors.eu/careers?company=Atos"
PAGE_URL = "https://career5.successfactors.eu/careers?company=Atos"

# reCAPTCHA v2 sitekey (从页面提取)
RECAPTCHA_SITEKEY = "6LcIRvssAAAAABQFcFPxVEwioK9odHeHybgtzHjz"

# ============================================================
# 验证码求解服务配置
# 选择求解方式: "2captcha" | "capsolver" | "audio"
# ============================================================
SOLVER_METHOD = "audio"

# 2captcha 配置 (https://2captcha.com)
TWOCAPTCHA_API_KEY = "YOUR_2CAPTCHA_API_KEY"

# CapSolver 配置 (https://capsolver.com)
CAPSOLVER_API_KEY = "YOUR_CAPSOLVER_API_KEY"

# ============================================================
# 浏览器配置
# ============================================================
BROWSER_HEADLESS = False  # 设为 True 则无头模式运行
BROWSER_TIMEOUT = 30      # 页面加载超时 (秒)
IMPLICIT_WAIT = 10        # 隐式等待 (秒)

# ============================================================
# 账号配置 (如需登录)
# 从环境变量读取, 避免硬编码敏感信息
# 使用方法: set ACCOUNT_EMAIL=your_email@gmail.com
#           set ACCOUNT_PASSWORD=your_password
# 或创建 .env 文件 (需 python-dotenv)
# ============================================================
ACCOUNT_EMAIL = os.environ.get("ACCOUNT_EMAIL", "your_email@example.com")
ACCOUNT_PASSWORD = os.environ.get("ACCOUNT_PASSWORD", "your_password")

# ============================================================
# 音频识别引擎配置
# ============================================================
AUDIO_RECOGNIZER = "whisper"       # "whisper" | "google"

# faster-whisper 配置
WHISPER_MODEL_SIZE = "base"        # tiny|base|small|medium|large-v3
WHISPER_DEVICE = "cpu"             # cpu|cuda
WHISPER_COMPUTE_TYPE = "int8"      # int8|int8_float16|float16|float32
WHISPER_BEAM_SIZE = 1              # beam search 宽度 (1=greedy, 最快)
WHISPER_LANGUAGE = "en"            # 语言代码

# ============================================================
# 导航配置
# ============================================================
NAV_MAX_RETRIES = 6                # 导航重试次数 (原 3 → 6)
NAV_DIRECT_URL_FALLBACK = True     # 多次失败后直接访问 SuccessFactors URL
NAV_PAGE_LOAD_TIMEOUT = 60000      # 页面加载超时 (毫秒)
NAV_FORM_WAIT_TIMEOUT = 30000      # 登录表单等待超时 (毫秒)
NAV_LINK_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".nav_link_cache.json")  # Apply 链接成功索引缓存
NAV_PREFERRED_HREF_PATTERN = "/talentcommunity/apply/"  # 优先尝试的 href 模式 (首次运行无缓存时生效)

# ============================================================
# reCAPTCHA 配置
# ============================================================
RECAPTCHA_MAX_RETRIES = 6          # 求解重试次数 (原 3 → 6)
RECAPTCHA_RENDER_WAIT = 60         # reCAPTCHA 渲染等待 (秒, 原 30 → 60)
RECAPTCHA_RETRY_DELAY = 3          # 重试间隔 (秒)

# ============================================================
# Stealth 方案配置
# ============================================================
STEALTH_USE_CDP = False            # CDP 模式: 连接真实 Chrome (需先启动 chrome --remote-debugging-port=9222)
STEALTH_CDP_ENDPOINT = "http://localhost:9222"  # CDP 连接地址
STEALTH_PERSISTENT_SESSION = True  # 持久化会话: 保存 cookie/localStorage
STEALTH_USE_REAL_PROFILE = True    # 使用真实 Chrome profile (含 cookies/历史/扩展), 消除空白 profile 触发 reCAPTCHA 的风险
STEALTH_AUTO_KILL_CHROME = True    # 自动关闭已运行的 Chrome 以释放 profile 锁

# 音频下载请求头 (模拟浏览器, 避免 Google 拦截)
AUDIO_DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.google.com/recaptcha/",
}

# ============================================================
# AI 图像识别配置 (方案 3: YOLOv8 微调分类 + 分割 + CLIP 三引擎)
# ============================================================
# CLIP 模型 (非 COCO 类别回退引擎)
IMAGE_CLASSIFIER_MODEL = "openai/clip-vit-base-patch32"  # HuggingFace CLIP 模型
IMAGE_MATCH_THRESHOLD = 0.5  # CLIP 匹配概率阈值 (0-1, 越高越严格)
IMAGE_MIN_CONFIDENCE = 0.45  # 最低置信度: 排序模式下低于此值的 tile 不选中
IMAGE_RANK_SCORE_GAP = 0.45  # CLIP top-k 自适应间隔: 只选距离最高分不超过该值的 tile (0.18→0.45: CLIP 对正确匹配分数跨度大, 过紧导致漏选)
IMAGE_TOP_K_3X3 = 3  # 3x3 CLIP 回退时最多点击的 tile 数
IMAGE_TOP_K_4X4 = 4  # 4x4/更多格子 CLIP 回退时最多点击的 tile 数

# YOLOv8 分类模型 (3x3 挑战: 微调模型, 12 个 reCAPTCHA 专用类别)
# 来源: ETH Zurich "Breaking reCAPTCHAv2" (https://github.com/aplesner/Breaking-reCAPTCHAv2)
YOLO_CLS_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "recaptcha_cls_best.pt")
YOLO_CLS_THRESHOLD = 0.15  # 分类概率阈值 (降低以减少漏选, ETH 论文使用 0.2)
YOLO_CLS_IMGSZ = 224  # 分类输入尺寸 (增大以提高小目标识别, ETH 训练用 120)
YOLO_CLS_TOP1_MARGIN = 0.08  # 目标类必须接近 top1 概率, 降低误点
# CLS ranking 模式参数 (4x4 网格回退时使用, 因为 CLS 模型未在 4x4 尺寸训练)
YOLO_CLS_RANK_GAP = 0.20  # 自适应间隔: 与最高分差距不超过此值的 tile 才选中 (0.20 兼顾精度和召回)
YOLO_CLS_RANK_MIN = 0.03  # 最低概率: 低于此值的 tile 不选中 (避免选完全不相关的)

# YOLOv8-seg 分割模型 (4x4 挑战: 基础 COCO 模型 + 多尺度检测)
YOLO_SEG_MODEL_NAME = "yolov8n-seg.pt"  # ultralytics 预训练分割模型 (自动下载)
YOLO_SEG_CONFIDENCE = 0.20  # 分割置信度阈值 (降低以提高小目标检出率)
YOLO_SEG_IMGSZ = 320  # 分割主尺度输入尺寸 (ETH 使用 320)
YOLO_SEG_IMGSZ_HIGH = 640  # 分割高分辨率尺度 (检测 4x4 网格中的小目标)
YOLO_SEG_CONFIDENCE_FALLBACK = 0.08  # 低置信度回退阈值 (主尺度 0 匹配时使用)
YOLO_SEG_OVERLAP_MODE = "ratio"  # "ratio" = 按比例阈值 (更精确), "any" = 任意像素重叠即选中
YOLO_SEG_OVERLAP_THRESHOLD = 0.05  # ratio 模式: 掩码覆盖网格 5% 以上才选中
YOLO_SEG_EDGE_MARGIN_PX = 2  # any 模式忽略 cell 边缘像素, 减少边界轻微触碰造成的误选

# ============================================================
# 无障碍 Cookie 配置 (方案 4: Accessibility Cookie)
# ============================================================
# 获取方式: 访问 https://www.google.com/recaptcha/admin/accessibility 注册
# 注册后在浏览器 Cookie 中找到 recaptcha-accessibility-cookie 的值
RECAPTCHA_ACCESSIBILITY_COOKIE = "YOUR_ACCESSIBILITY_COOKIE"

# ============================================================
# NopeCHA 扩展配置 (方案 5: 浏览器扩展)
# ============================================================
# NopeCHA 扩展目录路径 (解压后的扩展文件夹)
# Chrome Web Store ID: dknliebolcfipdbfhohdchdbmldibjco
NOPECHA_EXTENSION_PATH = os.path.join(os.path.dirname(__file__), "extensions", "nopecha")
NOPECHA_EXTENSION_ID = "dknliebolcfipdbfhohdchdbmldibjco"
EXTENSION_SOLVE_TIMEOUT = 120  # 扩展求解超时 (秒)

# ============================================================
# 日志配置
# ============================================================
LOG_LEVEL = "INFO"
SAVE_SCREENSHOTS = True
SCREENSHOT_DIR = "screenshots"

# ============================================================
# Native 方案配置 (零 CDP 痕迹: patchright launch_persistent_context + PyAutoGUI)
# ============================================================
NATIVE_BROWSER_CHANNEL = "chrome"        # 使用系统 Chrome (patchright channel 参数)
NATIVE_CLICK_RESULT_WAIT = 30            # OS 点击后等待 reCAPTCHA 响应时间 (秒, reCAPTCHA 处理可能需要 20-30s)
NATIVE_USE_REAL_PROFILE = False           # False=临时profile+复制关键cookies (推荐, 快速), True=直接用真实profile (慢, 可能卡住)
NATIVE_AUTO_KILL_CHROME = True           # 自动关闭已运行的 Chrome 以释放 profile 锁
NATIVE_FALLBACK_TO_IMAGE = True          # 触发图像挑战时是否 Fallback 到 ImageRuntime
