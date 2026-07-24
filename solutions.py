"""
统一方案定义 — CLI 与 GUI 共享
================================
所有方案的元数据 (名称/描述/图标/状态) 集中在此,
避免 main.py 和 gui.py 之间重复定义和不一致。

方案 key 是唯一标识, 用于:
  - CLI 方案选择 (main.py LAUNCHERS 字典)
  - GUI 卡片渲染 (gui.py MethodCard)
  - 持久化存储 (PersistenceManager.KEY_SELECTED_METHOD)
"""

from __future__ import annotations

# ============================================================
# 方案定义
# ============================================================
SOLUTIONS: list[dict] = [
    {
        "key": "audio",
        "name": "音频识别",
        "short_desc": "faster-whisper 本地模型识别音频挑战",
        "detail": (
            "点击 checkbox → 切换音频 → 下载识别 → 提交验证\n"
            "本地运行, 无需联网调用第三方"
        ),
        "cost": "免费",
        "status": "已验证",
        "status_color": "#10b981",
        "icon": "music",
        "cli_icon": "[A]",
        "deps": ["faster_whisper", "playwright"],
    },
    {
        "key": "api",
        "name": "API 代解",
        "short_desc": "2captcha / CapSolver 第三方服务",
        "detail": (
            "提交 sitekey 给 API → 轮询获取 token → 注入页面\n"
            "成功率最高, 速度最快, 需要付费"
        ),
        "cost": "付费",
        "status": "代码就绪",
        "status_color": "#3b82f6",
        "icon": "key",
        "cli_icon": "[B]",
        "deps": ["playwright"],
    },
    {
        "key": "image",
        "name": "AI 图像识别",
        "short_desc": "YOLO 三引擎 + CLIP 零样本分类",
        "detail": (
            "截取 3x3/4x4 网格 → YOLO CLS/SEG/CLIP 三引擎识别\n"
            "→ 点击匹配 tile → 提交验证\n"
            "免费, 需安装 torch + ultralytics (~2GB)"
        ),
        "cost": "免费",
        "status": "已验证",
        "status_color": "#10b981",
        "icon": "image",
        "cli_icon": "[C]",
        "deps": ["torch", "ultralytics", "transformers", "playwright"],
    },
    {
        "key": "cookie",
        "name": "无障碍 Cookie",
        "short_desc": "Accessibility Cookie 自动通过验证",
        "detail": (
            "设置 Google 无障碍 cookie → reCAPTCHA 自动通过\n"
            "速度极快, 需注册 Google 无障碍功能获取 cookie"
        ),
        "cost": "免费",
        "status": "需配置",
        "status_color": "#f59e0b",
        "icon": "cookie",
        "cli_icon": "[D]",
        "deps": ["playwright"],
    },
    {
        "key": "extension",
        "name": "浏览器扩展",
        "short_desc": "NopeCHA 扩展自动求解",
        "detail": (
            "加载 NopeCHA 扩展 → 点击 checkbox → 扩展自动求解\n"
            "免费, 需下载扩展, 仅支持有头模式"
        ),
        "cost": "免费",
        "status": "需配置",
        "status_color": "#f59e0b",
        "icon": "puzzle",
        "cli_icon": "[E]",
        "deps": ["playwright"],
    },
    {
        "key": "native",
        "name": "原生零痕迹",
        "short_desc": "patchright + PyAutoGUI OS级控制",
        "detail": (
            "patchright launch_persistent_context 零CDP痕迹\n"
            "→ 截图差异校准坐标 → OS级点击 (isTrusted=true)\n"
            "→ 图像挑战 YOLO 三引擎 Fallback"
        ),
        "cost": "免费",
        "status": "已验证",
        "status_color": "#10b981",
        "icon": "shield",
        "cli_icon": "[F]",
        "deps": ["patchright", "pyautogui", "ultralytics"],
    },
]

# 按 key 索引的字典 (O(1) 查找)
SOLUTION_MAP: dict[str, dict] = {s["key"]: s for s in SOLUTIONS}

# 方案总数
SOLUTION_COUNT = len(SOLUTIONS)


def get_solution(key: str) -> dict | None:
    """按 key 获取方案定义"""
    return SOLUTION_MAP.get(key)


def check_dependency(package_name: str) -> bool:
    """检查 Python 包是否可导入"""
    try:
        __import__(package_name)
        return True
    except ImportError:
        return False


def check_solution_deps(key: str) -> tuple[bool, list[str]]:
    """
    检查指定方案的依赖是否满足。

    Returns:
        (all_ok, missing_packages)
    """
    sol = get_solution(key)
    if sol is None:
        return False, []
    missing = [dep for dep in sol.get("deps", []) if not check_dependency(dep)]
    return len(missing) == 0, missing
