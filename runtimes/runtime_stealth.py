"""
方案 6: Stealth + 真人行为模拟 (不触发 reCAPTCHA 图像挑战)
=====================================================
核心思路:
  通过增强反检测指纹 + 模拟真实人类行为, 让 Google 判定为低风险用户,
  从而点击 reCAPTCHA checkbox 时直接通过, 不弹出图像挑战。

三层防御:
  1. 指纹层: 增强 stealth JS 注入 (WebGL/Canvas/chrome/plugins/permissions)
  2. 行为层: 贝塞尔曲线鼠标轨迹 + 真人打字/滚动/停顿
  3. 会话层: Google cookie 预热 + 充分页面停留

参考:
  - puppeteer-extra-plugin-stealth 检测向量
  - reCAPTCHA v2 风险评分机制 (行为分析 + IP + Cookie)
  - ghost-cursor 贝塞尔曲线 + Fitts 定律
"""

import asyncio
import logging
import math
import os
import random
import time

# 使用 patchright 替代 playwright — 移除 Runtime.enable/Console.enable CDP 泄露
# 这是 reCAPTCHA 检测自动化的核心向量, stealth.js 无法修补
try:
    from patchright.async_api import Page, async_playwright
    _USE_PATCHRIGHT = True
    logger_patchright = logging.getLogger(__name__)
    logger_patchright.info("[Stealth] 使用 patchright (CDP 痕迹已消除)")
except ImportError:
    from playwright.async_api import Page, async_playwright
    _USE_PATCHRIGHT = False

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


# ============================================================
# 增强 Stealth JS 注入脚本
# 在 playwright-stealth 基础上补充更深层检测向量
# ============================================================
# ============================================================
# 最小化 Stealth JS 注入脚本
# 原则: 只修补 Playwright Chromium 真正缺失/异常的项
#       绝不覆盖已有的真实值 (避免一致性矛盾)
#
# 诊断发现 headed Chromium 的真实指纹:
#   - WebGL: ANGLE (Intel, Intel(R) Iris(R) Xe Graphics... D3D11)  [真实GPU, 不碰]
#   - plugins: 5个真实 PDF viewer  [真实, 不碰]
#   - hardwareConcurrency: 16  [真实, 不碰]
#   - languages: ["zh-CN"]  [真实, 不碰]
#   - timezone: Asia/Shanghai  [真实, 不碰]
#   - window.chrome: 有 loadTimes/csi/app, 但缺 runtime  [仅补 runtime]
#   - navigator.webdriver: false (应为 undefined)  [需修补]
# ============================================================
_STEALTH_INIT_SCRIPT = r"""
(() => {
    // === 1. navigator.webdriver: false → undefined ===
    // --disable-blink-features=AutomationControlled 已将其设为 false,
    // 但检测器知道 false 是补丁值, 真实浏览器应为 undefined
    try {
        const proto = Object.getPrototypeOf(navigator);
        const desc = Object.getOwnPropertyDescriptor(proto, 'webdriver');
        if (desc) {
            Object.defineProperty(proto, 'webdriver', {
                get: () => undefined,
                set: undefined,
                configurable: true,
                enumerable: true,
            });
        }
    } catch (e) {}

    // === 2. window.chrome.runtime: 仅在缺失时补充 ===
    // 诊断发现真实 Chromium 有 loadTimes/csi/app, 但缺 runtime
    // 只添加 runtime, 绝不替换整个 chrome 对象
    try {
        if (window.chrome && !window.chrome.runtime) {
            window.chrome.runtime = {
                // 真实 Chrome 的 runtime 包含这些枚举
                PlatformOs: { MAC: 'mac', WIN: 'win', LINUX: 'linux', CROS: 'cros', ANDROID: 'android', OPENBSD: 'openbsd' },
                PlatformArch: { X86_32: 'x86-32', X86_64: 'x86-64', ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64' },
                OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                id: undefined,
                connect: function() { return { onMessage: { addListener: function(){} }, postMessage: function(){} }; },
                sendMessage: function() {},
            };
        }
    } catch (e) {}

    // === 3. ChromeDriver 痕迹清除 (仅清理, 不伪造) ===
    try {
        for (const key of Object.keys(document)) {
            if (key.match(/^cdc_/) || key.match(/^[$]cdc_/)) {
                delete document[key];
            }
        }
    } catch (e) {}

    // 不再修补以下项 (保留真实值, 避免一致性矛盾):
    // - WebGL vendor/renderer (真实 ANGLE/Direct3D11 字符串)
    // - navigator.plugins (真实 5 个 PDF viewer)
    // - navigator.hardwareConcurrency (真实 16)
    // - navigator.deviceMemory (真实 null)
    // - navigator.languages (真实 zh-CN)
    // - navigator.maxTouchPoints (真实 10)
    // - navigator.platform (真实 Win32)
    // - Canvas 指纹 (真实渲染)
    // - Permissions API (真实行为)
    // - outerWidth/outerHeight (真实窗口尺寸)
})();
"""


# ============================================================
# 真人行为模拟工具
# ============================================================
class HumanBehavior:
    """模拟真实人类鼠标、键盘、滚动行为"""

    @staticmethod
    def _cubic_bezier(p0, p1, p2, p3, t):
        """三次贝塞尔曲线插值"""
        u = 1 - t
        return (u**3 * p0 + 3 * u**2 * t * p1 +
                3 * u * t**2 * p2 + t**3 * p3)

    @staticmethod
    def _generate_bezier_path(start_x, start_y, end_x, end_y, steps=None):
        """
        生成贝塞尔曲线路径 (含控制点随机偏移 + 微小抖动)
        模拟人类鼠标: 开始慢→中间快→结束慢
        """
        dist = math.hypot(end_x - start_x, end_y - start_y)
        if steps is None:
            # 距离越远步数越多 (Fitts 定律)
            steps = max(15, min(50, int(dist / 15)))

        # 控制点偏移 (越大越弯曲)
        offset = min(dist * 0.3, 100)
        ctrl1_x = start_x + (end_x - start_x) * 0.3 + random.uniform(-offset, offset)
        ctrl1_y = start_y + (end_y - start_y) * 0.3 + random.uniform(-offset, offset)
        ctrl2_x = start_x + (end_x - start_x) * 0.7 + random.uniform(-offset, offset)
        ctrl2_y = start_y + (end_y - start_y) * 0.7 + random.uniform(-offset, offset)

        path = []
        for i in range(steps + 1):
            t = i / steps
            # ease-in-out 缓动 (开始和结束慢)
            t_eased = t * t * (3 - 2 * t)
            x = HumanBehavior._cubic_bezier(start_x, ctrl1_x, ctrl2_x, end_x, t_eased)
            y = HumanBehavior._cubic_bezier(start_y, ctrl1_y, ctrl2_y, end_y, t_eased)
            # 微小抖动 (人手不可能完全稳定)
            x += random.uniform(-1.5, 1.5)
            y += random.uniform(-1.5, 1.5)
            path.append((x, y))
        return path

    @staticmethod
    async def human_move(page: Page, end_x, end_y, start_x=None, start_y=None):
        """
        模拟人类移动鼠标到目标位置
        含贝塞尔曲线轨迹 + 加减速 + 微小抖动
        """
        if start_x is None:
            start_x = random.uniform(100, 800)
        if start_y is None:
            start_y = random.uniform(100, 500)

        path = HumanBehavior._generate_bezier_path(start_x, start_y, end_x, end_y)
        total = len(path)

        for i, (x, y) in enumerate(path):
            if i == 0:
                continue
            prev_x, prev_y = path[i - 1]
            dx, dy = x - prev_x, y - prev_y

            # 加速度模拟: 前 30% 慢(加速), 中 40% 快(匀速), 后 30% 慢(减速)
            progress = i / total
            if progress < 0.3:
                delay = random.uniform(0.015, 0.035)
            elif progress < 0.7:
                delay = random.uniform(0.008, 0.018)
            else:
                delay = random.uniform(0.02, 0.045)

            await page.mouse.move(dx, dy)
            await asyncio.sleep(delay)

    @staticmethod
    async def human_click(page: Page, x, y):
        """
        模拟人类点击: 先移动到目标附近 → 微调 → 短暂停顿 → 点击
        """
        # 1. 移动到目标附近 (有偏差)
        near_x = x + random.uniform(-20, 20)
        near_y = y + random.uniform(-20, 20)
        await HumanBehavior.human_move(page, near_x, near_y)

        # 2. 微调到目标 (小范围修正)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.move(x - near_x + random.uniform(-2, 2),
                              y - near_y + random.uniform(-2, 2))
        await asyncio.sleep(random.uniform(0.05, 0.15))

        # 3. 点击前犹豫 (人类反应时间)
        await asyncio.sleep(random.uniform(0.15, 0.5))

        # 4. 点击 (含微小偏移)
        click_x = x + random.uniform(-3, 3)
        click_y = y + random.uniform(-3, 3)
        await page.mouse.click(click_x, click_y)

    @staticmethod
    async def human_type(page: Page, selector: str, text: str):
        """
        模拟人类打字: 随机间隔 + 偶尔停顿 + 极低概率打错纠错
        """
        await page.focus(selector)
        await asyncio.sleep(random.uniform(0.2, 0.6))

        for i, char in enumerate(text):
            await page.keyboard.type(char)

            # 基础间隔 50-200ms
            delay = random.uniform(0.05, 0.2)

            # 偶尔长停顿 (思考)
            if random.random() < 0.08:
                delay += random.uniform(0.3, 0.9)

            await asyncio.sleep(delay)

            # 极低概率打错并纠正
            if random.random() < 0.02 and i < len(text) - 1:
                wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
                await page.keyboard.type(wrong_char)
                await asyncio.sleep(random.uniform(0.1, 0.3))
                await page.keyboard.press('Backspace')
                await asyncio.sleep(random.uniform(0.1, 0.25))

    @staticmethod
    async def human_scroll(page: Page, scrolls=None):
        """
        模拟人类滚动: 非匀速 + 有停顿 + 偶尔回滚
        """
        if scrolls is None:
            scrolls = random.randint(2, 5)

        for _ in range(scrolls):
            delta = random.randint(150, 500)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.4, 1.8))

            # 偶尔小幅回滚 (人类阅读习惯)
            if random.random() < 0.25:
                await page.mouse.wheel(0, -random.randint(30, 120))
                await asyncio.sleep(random.uniform(0.2, 0.6))

    @staticmethod
    async def random_mouse_wander(page: Page, duration_s=2.0):
        """
        随机鼠标漫游 (模拟人类在页面上无目的移动)
        用于建立鼠标轨迹历史
        """
        end_time = time.time() + duration_s
        while time.time() < end_time:
            x = random.uniform(50, 1850)
            y = random.uniform(50, 950)
            await HumanBehavior.human_move(page, x, y)
            await asyncio.sleep(random.uniform(0.2, 0.6))

    @staticmethod
    async def random_delay(min_s=0.5, max_s=2.0):
        """随机延迟"""
        await asyncio.sleep(random.uniform(min_s, max_s))


# ============================================================
# StealthRuntime
# ============================================================
class StealthRuntime(BaseBypassRuntime):
    """
    Stealth + 真人行为模拟方案
    目标: 不触发 reCAPTCHA 图像挑战, 直接通过 checkbox 验证
    """

    method_name = "stealth"
    method_desc = "Stealth + 真人行为模拟 (不触发图像挑战)"

    def __init__(self):
        super().__init__()
        self._challenge_detected = False
        self._use_cdp = getattr(config, "STEALTH_USE_CDP", False)
        self._cdp_endpoint = getattr(config, "STEALTH_CDP_ENDPOINT", "http://localhost:9222")
        self._use_persistent = getattr(config, "STEALTH_PERSISTENT_SESSION", False)
        self._use_real_profile = getattr(config, "STEALTH_USE_REAL_PROFILE", True)
        self._auto_kill_chrome = getattr(config, "STEALTH_AUTO_KILL_CHROME", True)
        self._state_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".stealth_state")
        self._chrome_process = None  # 手动启动的 Chrome 进程
        self._user_data_dir = None   # Chrome 用户数据目录
        self._using_real_profile = False  # 是否使用真实 profile (影响清理逻辑)

    # ========================================================
    # 手动启动真实 Chrome (零自动化参数)
    # ========================================================
    def _find_chrome_path(self) -> str | None:
        """查找系统安装的真实 Chrome"""
        import platform
        if platform.system() == "Windows":
            candidates = [
                os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LocalAppData", ""), "Google", "Chrome", "Application", "chrome.exe"),
            ]
        else:
            candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser"]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def _get_real_chrome_user_data_dir(self) -> str | None:
        """
        获取用户真实 Chrome 的 User Data 目录
        Windows: %LOCALAPPDATA%\\Google\\Chrome\\User Data
        macOS:   ~/Library/Application Support/Google/Chrome
        Linux:   ~/.config/google-chrome
        """
        import platform
        system = platform.system()
        if system == "Windows":
            path = os.path.join(os.environ.get("LocalAppData", ""), "Google", "Chrome", "User Data")
        elif system == "Darwin":
            path = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        else:
            path = os.path.expanduser("~/.config/google-chrome")
        if os.path.exists(path):
            return path
        return None

    def _is_chrome_running(self) -> bool:
        """检查 Chrome 是否正在运行"""
        import subprocess
        import platform
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                    capture_output=True, text=True, timeout=5,
                )
                return "chrome.exe" in result.stdout
            else:
                result = subprocess.run(
                    ["pgrep", "-f", "chrome"],
                    capture_output=True, text=True, timeout=5,
                )
                return len(result.stdout.strip()) > 0
        except Exception:
            return False

    def _kill_chrome(self):
        """终止所有 Chrome 进程 (释放 profile 锁)"""
        import subprocess
        import platform
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chrome.exe"],
                    capture_output=True, timeout=10,
                )
            else:
                subprocess.run(["pkill", "-f", "chrome"], capture_output=True, timeout=10)
            logger.info("[Stealth] 已终止所有 Chrome 进程")
        except Exception as e:
            logger.warning(f"[Stealth] 终止 Chrome 失败: {e}")

    def _cleanup_profile_locks(self, user_data_dir: str):
        """
        清理 Chrome profile 残留锁文件.
        taskkill /F 强制终止 Chrome 后, 锁文件可能未被释放, 导致下次启动卡住.
        """
        import os
        # Chrome 锁文件位置 (Windows):
        # - User Data/Default/LOCK (主锁)
        # - User Data/Default/LOCK~*.TMP (临时锁)
        # - User Data/Default/BrowserMetrics-spare.pma (可能锁定的指标文件)
        lock_patterns = ["LOCK", "LOCK~"]
        default_dir = os.path.join(user_data_dir, "Default")
        cleaned = 0
        for dirpath in [user_data_dir, default_dir]:
            if not os.path.exists(dirpath):
                continue
            for fname in os.listdir(dirpath):
                for pattern in lock_patterns:
                    if fname.startswith(pattern):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            os.remove(fpath)
                            cleaned += 1
                            logger.info(f"[Stealth] 已清理锁文件: {fpath}")
                        except Exception:
                            pass
        if cleaned == 0:
            logger.info("[Stealth] 无残留锁文件需要清理")

    def _launch_real_chrome(self, port=9222) -> bool:
        """
        手动启动真实 Chrome (带调试端口), 零 Playwright 自动化参数
        关键: 优先使用用户真实 profile (含 cookies/历史/扩展)
              空白 profile 是 reCAPTCHA 触发图像挑战的头号原因
        """
        chrome_path = self._find_chrome_path()
        if not chrome_path:
            logger.error("[Stealth] 未找到系统 Chrome")
            return False

        # ============================================================
        # 决定使用哪个 user-data-dir
        # 直接使用真实 profile (不复制), 这是用户说的"直接用我真实浏览器"
        # ============================================================
        if self._use_real_profile:
            real_dir = self._get_real_chrome_user_data_dir()
            if real_dir:
                # 真实 profile 被占用时需要先关闭 Chrome
                if self._is_chrome_running():
                    if self._auto_kill_chrome:
                        logger.warning("[Stealth] Chrome 正在运行, 正在关闭以释放真实 profile...")
                        self._kill_chrome()
                        import time
                        time.sleep(5)  # 等待进程完全退出 + 文件句柄释放 (3s→5s)
                        # 清理残留的 profile 锁文件 (taskkill /F 可能导致锁未释放)
                        self._cleanup_profile_locks(real_dir)
                    else:
                        logger.error("[Stealth] Chrome 正在运行且未启用自动关闭, 无法使用真实 profile")
                        logger.error("[Stealth] 请手动关闭 Chrome 后重试, 或设置 STEALTH_AUTO_KILL_CHROME=True")
                        return False

                # ============================================================
                # 直接使用真实 profile 目录 (不复制)
                # 这是完全真实的浏览器环境: cookies/历史/扩展/偏好全保留
                # ============================================================
                self._user_data_dir = real_dir
                self._using_real_profile = True  # 真实 profile, close() 时不删除!
                logger.info(f"[Stealth] 直接使用真实 Chrome profile: {real_dir}")
                logger.info("[Stealth] 含真实 Google cookies/浏览历史/扩展 — reCAPTCHA 风险评分最低")
            else:
                logger.warning("[Stealth] 未找到真实 Chrome profile, 回退到临时 profile")
                import tempfile
                self._user_data_dir = tempfile.mkdtemp(prefix="stealth_chrome_")
                self._using_real_profile = False
        else:
            import tempfile
            self._user_data_dir = tempfile.mkdtemp(prefix="stealth_chrome_")
            self._using_real_profile = False
            logger.info("[Stealth] 使用临时 profile (STEALTH_USE_REAL_PROFILE=False)")

        # ============================================================
        # 启动 Chrome (零自动化参数)
        # ============================================================
        args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self._user_data_dir}",
            "--profile-directory=Default",       # 明确使用 Default profile
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",  # 防止 "Chrome 未正常关闭" 恢复提示
            "--restore-last-session",            # 自动恢复会话, 跳过恢复对话框 (关键修复)
            "--disable-features=InfiniteSessionRestore",  # 跳过会话恢复
            "--window-size=1920,1080",
            # 注意: 不加 --enable-automation
            # 注意: 不加 --disable-blink-features (让 Chrome 以完全正常方式启动)
            # 注意: 不加 --disable-extensions (保留真实扩展, 增强真实性)
        ]

        import subprocess
        self._chrome_process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info(f"[Stealth] 真实 Chrome 已启动 (PID: {self._chrome_process.pid}, 端口: {port})")
        return True

    def _copy_real_profile_data(self, real_dir: str, temp_dir: str):
        """
        从真实 Chrome profile 复制关键文件到临时目录
        只复制 cookies/历史/登录数据等 — 不复制缓存/扩展 (太大)
        这些文件让 reCAPTCHA 看到真实的 Google 会话, 大幅降低风险评分
        """
        import shutil
        import time

        real_default = os.path.join(real_dir, "Default")
        temp_default = os.path.join(temp_dir, "Default")
        os.makedirs(temp_default, exist_ok=True)

        # 1. 复制 Local State (含 cookie 加密密钥, 必须!)
        local_state_src = os.path.join(real_dir, "Local State")
        if os.path.exists(local_state_src):
            self._copy_with_retry(local_state_src, os.path.join(temp_dir, "Local State"))
            logger.info("[Stealth] 已复制 Local State (加密密钥)")

        # 2. 复制 Default profile 的关键文件
        # Cookies 位置: Chrome 96+ 在 Default/Network/Cookies, 旧版在 Default/Cookies
        critical_files = [
            "Cookies",               # 旧版 cookie 路径
            "Login Data",            # 保存的密码
            "Preferences",           # 用户偏好设置
            "History",               # 浏览历史
            "Web Data",              # 表单数据
            "TransportSecurity",     # HSTS 数据
        ]

        copied_count = 0
        for fname in critical_files:
            src = os.path.join(real_default, fname)
            if os.path.exists(src):
                dst = os.path.join(temp_default, fname)
                if self._copy_with_retry(src, dst):
                    copied_count += 1

        # 3. 新版 Chrome cookies 路径 (Default/Network/Cookies)
        real_network = os.path.join(real_default, "Network")
        temp_network = os.path.join(temp_default, "Network")
        if os.path.exists(real_network):
            os.makedirs(temp_network, exist_ok=True)
            cookies_src = os.path.join(real_network, "Cookies")
            if os.path.exists(cookies_src):
                if self._copy_with_retry(cookies_src, os.path.join(temp_network, "Cookies")):
                    copied_count += 1
                    logger.info("[Stealth] 已复制 Network/Cookies (新版 Chrome)")

        # 4. 复制 Default 目录下的 Network 子目录其他文件
        if os.path.exists(real_network):
            for fname in ["Login Data", "Trust Tokens"]:
                src = os.path.join(real_network, fname)
                if os.path.exists(src):
                    self._copy_with_retry(src, os.path.join(temp_network, fname))

        logger.info(f"[Stealth] 共复制 {copied_count} 个关键文件到临时 profile")

    def _copy_with_retry(self, src: str, dst: str, max_retries: int = 3) -> bool:
        """带重试的文件复制 (Chrome 刚关闭时文件可能被短暂锁定)"""
        import shutil
        import time
        for attempt in range(max_retries):
            try:
                shutil.copy2(src, dst)
                return True
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.warning(f"[Stealth] 复制失败 (锁定): {os.path.basename(src)}")
                    return False
            except Exception as e:
                logger.warning(f"[Stealth] 复制失败: {os.path.basename(src)} - {e}")
                return False
        return False

    def _prepare_stealth_user_data_dir(self):
        """
        准备 user-data-dir: 临时 profile + 复制真实 cookies.
        不直接使用真实 profile (避免 profile 锁和恢复对话框问题).
        """
        # 关闭已运行的 Chrome (避免多实例干扰)
        if self._auto_kill_chrome and self._is_chrome_running():
            logger.warning("[Stealth] Chrome 正在运行, 正在关闭以确保单一实例...")
            self._kill_chrome()
            time.sleep(5)
            real_dir = self._get_real_chrome_user_data_dir()
            if real_dir:
                self._cleanup_profile_locks(real_dir)

        # 创建临时 profile
        import tempfile
        self._user_data_dir = tempfile.mkdtemp(prefix="stealth_chrome_")
        self._using_real_profile = False

        # 复制真实 cookies/登录数据 (降低 reCAPTCHA 风险评分)
        real_dir = self._get_real_chrome_user_data_dir()
        if real_dir and os.path.exists(real_dir):
            self._copy_real_profile_data(real_dir, self._user_data_dir)
            logger.info("[Stealth] 使用临时 profile (已复制真实 cookies/登录数据)")
        else:
            logger.warning("[Stealth] 使用空白临时 profile (未找到真实 profile)")

    async def _init_real_chrome_browser(self):
        """
        使用 patchright launch_persistent_context 启动 Chrome.

        关键改进 (v2):
          - 放弃 connect_over_cdp 方案 (Chrome 调试端口启动不稳定 + CDP 连接被 reCAPTCHA 检测)
          - 改用 launch_persistent_context (patchright 反检测补丁完整生效)
          - 临时 profile + 复制真实 cookies (避免 profile 锁和恢复对话框)
          - 注入 stealth JS (在 patchright 基础上补充 navigator.webdriver 等修补)

        项目硬约束: "Use launch_persistent_context instead of connect_over_cdp
                     to ensure patchright anti-detection patches work"
        """
        logger.info("[Stealth] 初始化: patchright launch_persistent_context...")

        # 1. 准备 user-data-dir (临时 profile + 复制真实 cookies)
        self._prepare_stealth_user_data_dir()

        # 2. patchright launch_persistent_context
        self.playwright = await async_playwright().start()

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            channel="chrome",
            headless=False,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--disable-features=InfiniteSessionRestore",
                "--start-maximized",
                # 注意: 不加 --enable-automation (patchright 已处理)
                # 注意: 不加 --disable-blink-features=AutomationControlled (patchright 自己处理)
                # 注意: 不加 --disable-extensions (保留扩展增强真实性)
            ],
            # 不设 viewport/locale/timezone_id/user_agent: 使用系统真实值 (避免指纹矛盾)
        )

        # 3. 注入 stealth JS (在 patchright 基础上补充 navigator.webdriver 等修补)
        # patchright 已移除 Runtime.enable/Console.enable 泄露,
        # 但 navigator.webdriver 仍为 false (应为 undefined), 需额外修补
        await self.context.add_init_script(_STEALTH_INIT_SCRIPT)
        logger.info("[Stealth] stealth JS 已注入 (补充 navigator.webdriver 等修补)")

        # 4. 获取 page (persistent context 自动创建一个空白页)
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)

        logger.info("[Stealth] 浏览器初始化完成 (patchright launch_persistent_context)")

        # 5. 指纹诊断 (仅记录, 不修改)
        await self._diagnose_fingerprint()

    # ========================================================
    # CDP 模式: 连接到真实 Chrome (最隐蔽方案)
    # ========================================================
    async def _init_cdp_browser(self):
        """通过 CDP 连接到已运行的 Chrome 浏览器 (最隐蔽方案)"""
        logger.info(f"[Stealth] CDP 模式: 连接到 {self._cdp_endpoint}")
        self.browser = await self.playwright.chromium.connect_over_cdp(self._cdp_endpoint)

        # 获取已有 context (真实浏览器已有 cookie/历史)
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
            logger.info("[Stealth] 使用真实浏览器已有 context (含真实 cookie/历史)")
        else:
            self.context = await self.browser.new_context(**self._get_context_options())

        # 注入增强 stealth 脚本
        await self._post_context_init()

        # 获取已有页面或创建新页面
        if self.context.pages:
            self.page = self.context.pages[0]
            logger.info(f"[Stealth] 使用浏览器已有页面: {self.page.url}")
        else:
            self.page = await self.context.new_page()

    # ========================================================
    # 持久化模式: 保存/恢复浏览器状态
    # ========================================================
    async def _save_browser_state(self):
        """保存浏览器状态 (cookie + localStorage) 供下次使用"""
        if not self._use_persistent or not self.context:
            return
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            state_path = os.path.join(self._state_dir, "browser_state.json")
            state = await self.context.storage_state(path=state_path)
            logger.info(f"[Stealth] 浏览器状态已保存到 {state_path}")
        except Exception as e:
            logger.warning(f"[Stealth] 保存浏览器状态失败: {e}")

    async def _load_browser_state(self) -> dict | None:
        """加载之前保存的浏览器状态"""
        if not self._use_persistent:
            return None
        state_path = os.path.join(self._state_dir, "browser_state.json")
        if os.path.exists(state_path):
            logger.info(f"[Stealth] 加载之前保存的浏览器状态: {state_path}")
            return state_path
        return None

    # ========================================================
    # 覆盖: 浏览器初始化 (支持 CDP / 持久化 / 标准三种模式)
    # ========================================================
    async def init_browser(self):
        # 优先使用 patchright launch_persistent_context (零自动化痕迹)
        # 项目硬约束: launch_persistent_context 替代 connect_over_cdp
        if _USE_PATCHRIGHT:
            logger.info("[Stealth] 使用 patchright launch_persistent_context (零自动化痕迹)")
            await self._init_real_chrome_browser()
        elif self._use_cdp:
            # CDP 模式: 连接已有 Chrome
            self.playwright = await async_playwright().start()
            await self._init_cdp_browser()
        else:
            # 回退: 使用父类 (普通 playwright)
            saved_state = await self._load_browser_state()
            if saved_state:
                original_get_context = self._get_context_options
                def _get_context_with_state():
                    opts = original_get_context()
                    opts["storage_state"] = saved_state
                    return opts
                self._get_context_options = _get_context_with_state
            await super().init_browser()

        logger.info("[Stealth] 浏览器初始化完成")

    # ========================================================
    # 覆盖: 关闭时保存状态
    # ========================================================
    async def close(self):
        await self._save_browser_state()
        # launch_persistent_context 模式: 关闭 context 会自动关闭 Chrome
        if self.context:
            try:
                await self.context.close()
                logger.info("[Stealth] persistent context 已关闭")
            except Exception as e:
                logger.warning(f"[Stealth] context.close() 异常: {e}")
        # 兼容旧 CDP 模式: 如有 browser 连接则断开
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
                logger.info("[Stealth] playwright 已停止")
            except Exception:
                pass
        # 兼容旧 CDP 模式: 如有手动启动的 Chrome 进程则终止
        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
                logger.info("[Stealth] 手动启动的 Chrome 进程已终止")
            except Exception:
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
        # 清理临时 profile — 绝不删除真实 profile!
        if self._user_data_dir and not self._using_real_profile:
            import shutil
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
                logger.info("[Stealth] 临时 profile 已清理")
            except Exception:
                pass
        elif self._using_real_profile:
            logger.info("[Stealth] 使用的是真实 profile, 跳过清理 (保留用户数据)")

    # ========================================================
    # 覆盖: 增强浏览器启动参数
    # ========================================================
    def _get_browser_args(self) -> list:
        # 仅在 fallback (非 patchright) 路径使用
        # 不加 --disable-extensions (保留扩展增强真实性)
        return [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-popup-blocking",
            "--window-size=1920,1080",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--disable-notifications",
            "--disable-background-networking",
            "--disable-default-apps",
            "--no-first-run",
            "--no-default-browser-check",
        ]

    def _get_context_options(self) -> dict:
        # 原则: 不覆盖任何真实机器属性, 让浏览器使用自身真实值
        # 诊断发现真实机器:
        #   - UA: Chrome/149.0.0.0 (不覆盖, 让 Playwright 使用真实 UA)
        #   - locale: zh-CN (不覆盖)
        #   - timezone: Asia/Shanghai (不覆盖)
        #   - languages: ["zh-CN"] (不覆盖)
        # 只设置 viewport (不影响指纹一致性)
        return {
            "viewport": {"width": 1920, "height": 1080},
            # 不设 locale: 使用系统真实值 (zh-CN)
            # 不设 timezone_id: 使用系统真实时区 (Asia/Shanghai)
            # 不设 user_agent: 使用 Chromium 真实 UA (Chrome/149)
            # 不设 extra_http_headers: 避免与真实浏览器不一致
        }

    # ========================================================
    # 覆盖: context 创建后注入增强 stealth 脚本
    # ========================================================
    async def _post_context_init(self):
        """注入增强 stealth JS (在页面脚本执行前生效)"""
        logger.info("[Stealth] 注入增强反检测脚本...")
        await self.context.add_init_script(_STEALTH_INIT_SCRIPT)
        logger.info("[Stealth] 增强 stealth 脚本注入完成")

    # ========================================================
    # 指纹诊断: 验证 stealth 效果
    # ========================================================
    async def _diagnose_fingerprint(self):
        """诊断浏览器指纹, 验证 stealth 脚本是否生效"""
        logger.info("[Stealth] ====== 指纹诊断 ======")
        try:
            # 导航到一个空白页以执行 JS (about:blank 不受同源策略限制)
            await self.page.goto("about:blank", wait_until="domcontentloaded")

            diagnostics = await self.page.evaluate("""() => {
                const result = {};
                // navigator.webdriver: 应为 undefined (不是 false)
                result.webdriver = String(navigator.webdriver);
                result.webdriver_type = typeof navigator.webdriver;
                // window.chrome
                result.has_chrome = typeof window.chrome !== 'undefined';
                result.has_chrome_runtime = typeof window.chrome?.runtime !== 'undefined';
                // 其他关键指纹
                result.userAgent = navigator.userAgent;
                result.platform = navigator.platform;
                result.languages = JSON.stringify(navigator.languages);
                result.hardwareConcurrency = navigator.hardwareConcurrency;
                result.maxTouchPoints = navigator.maxTouchPoints;
                result.plugins_count = navigator.plugins.length;
                // WebGL
                try {
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl');
                    if (gl) {
                        result.webglVendor = gl.getParameter(gl.VENDOR);
                        result.webglRenderer = gl.getParameter(gl.RENDERER);
                    }
                } catch(e) { result.webglError = e.message; }
                // cdc_ 痕迹
                result.cdc_traces = Object.keys(document).filter(k => k.match(/^cdc_|^[$]cdc_/)).length;
                return result;
            }""")

            # 评估指纹 (仅检查真正的异常, 不把正常值标记为问题)
            # 调研结论: webdriver=false 是现代浏览器标准默认值, 不需要 undefined
            #          chrome.runtime 在普通页面上可能不存在, 这是正常的
            issues = []
            if diagnostics.get("webdriver") == "true":
                issues.append(f"navigator.webdriver = true (检测到自动化!)")
            if diagnostics.get("cdc_traces", 0) > 0:
                issues.append(f"检测到 {diagnostics['cdc_traces']} 个 cdc_ 痕迹")

            logger.info(f"[Stealth] webdriver: {diagnostics.get('webdriver')} (type: {diagnostics.get('webdriver_type')})")
            logger.info(f"[Stealth] chrome.runtime: {'存在' if diagnostics.get('has_chrome_runtime') else '缺失 (正常)'}")
            logger.info(f"[Stealth] UA: {diagnostics.get('userAgent', '')[:80]}")
            logger.info(f"[Stealth] platform: {diagnostics.get('platform')}")
            logger.info(f"[Stealth] languages: {diagnostics.get('languages')}")
            logger.info(f"[Stealth] hardwareConcurrency: {diagnostics.get('hardwareConcurrency')}")
            logger.info(f"[Stealth] plugins: {diagnostics.get('plugins_count')} 个")
            logger.info(f"[Stealth] WebGL: {diagnostics.get('webglVendor', '?')} / {diagnostics.get('webglRenderer', '?')[:60]}")
            logger.info(f"[Stealth] cdc_ 痕迹: {diagnostics.get('cdc_traces', 0)} 个")

            if issues:
                logger.warning(f"[Stealth] 指纹诊断发现 {len(issues)} 个问题:")
                for issue in issues:
                    logger.warning(f"  - {issue}")
            else:
                logger.info("[Stealth] 指纹诊断通过: 真实浏览器指纹, 无自动化痕迹")

        except Exception as e:
            logger.warning(f"[Stealth] 指纹诊断失败 (不影响主流程): {e}")
        logger.info("[Stealth] ========================")

    # ========================================================
    # 会话预热: 先访问 Google 建立 cookie
    # ========================================================
    async def _warmup_session(self):
        """会话预热: 访问 Google 建立浏览历史和 cookie"""
        logger.info("[Stealth] 会话预热: 访问 Google 建立浏览历史...")

        try:
            warmup_page = await self.context.new_page()

            # 1. 访问 Google 首页
            # 注意: 中国大陆可能需要代理才能访问 Google
            # 如果无法访问, 改用百度 (保持地域一致性)
            warmup_ok = False
            try:
                await warmup_page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                logger.info("[Stealth] Google 首页已加载")
                warmup_ok = True
            except Exception:
                logger.info("[Stealth] Google 不可达, 改用百度预热 (地域一致性更好)")
                try:
                    await warmup_page.goto("https://www.baidu.com", wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    logger.info("[Stealth] 百度首页已加载")
                    warmup_ok = True
                except Exception as e2:
                    logger.warning(f"[Stealth] 百度也不可达, 跳过预热: {e2}")

            if not warmup_ok:
                await warmup_page.close()
                return

            # 2. 模拟人类行为: 鼠标漫游 + 滚动
            await HumanBehavior.random_mouse_wander(warmup_page, duration_s=random.uniform(1.5, 3.0))
            await HumanBehavior.human_scroll(warmup_page, scrolls=random.randint(1, 2))

            # 3. 随机搜索 (使用中文搜索词, 匹配 zh-CN locale)
            try:
                search_box = warmup_page.locator('input[name="wd"], input[name="q"], textarea[name="q"]')
                if await search_box.count() > 0:
                    # 中文搜索词 (匹配 zh-CN 语言环境)
                    search_terms = [
                        "今天天气", "新闻头条", "python教程",
                        "咖啡店推荐", "电影票房", "科技新闻",
                    ]
                    term = random.choice(search_terms)
                    await HumanBehavior.human_type(warmup_page, 'input[name="wd"], input[name="q"], textarea[name="q"]', term)
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    await warmup_page.keyboard.press("Enter")
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                    logger.info(f"[Stealth] 搜索完成: '{term}'")

                    # 搜索结果页模拟浏览
                    await HumanBehavior.human_scroll(warmup_page, scrolls=random.randint(1, 3))
                    await HumanBehavior.random_mouse_wander(warmup_page, duration_s=random.uniform(1.0, 2.5))
            except Exception as e:
                logger.warning(f"[Stealth] 搜索模拟失败 (不影响主流程): {e}")

            # 4. 关闭预热页面
            await warmup_page.close()
            logger.info("[Stealth] 会话预热完成, Google cookie 已建立")

        except Exception as e:
            logger.warning(f"[Stealth] 会话预热失败 (不影响主流程): {e}")

    # ========================================================
    # 覆盖导航: 加入会话预热 + 人类浏览行为
    # ========================================================
    async def navigate_to_target(self):
        """导航到目标页面 (含会话预热)"""
        # 1. 会话预热
        await self._warmup_session()

        # 2. 导航到目标页面
        logger.info("[Stealth] 开始导航到目标页面...")
        await super().navigate_to_target()

        # 3. 到达登录页后模拟人类浏览行为
        logger.info("[Stealth] 模拟人类浏览登录页...")
        await HumanBehavior.human_scroll(self.page, scrolls=random.randint(1, 3))
        await HumanBehavior.random_mouse_wander(self.page, duration_s=random.uniform(2.0, 4.0))
        await HumanBehavior.random_delay(1.0, 2.5)

    # ========================================================
    # 核心: 求解 reCAPTCHA (真人行为模拟点击 checkbox)
    # ========================================================
    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        通过真人行为模拟点击 reCAPTCHA checkbox
        目标: 不触发图像挑战, 直接通过验证
        """
        logger.info("[Stealth] 开始真人行为模拟求解 reCAPTCHA v2...")

        max_attempts = config.RECAPTCHA_MAX_RETRIES

        for attempt in range(1, max_attempts + 1):
            logger.info(f"[Stealth] 第 {attempt}/{max_attempts} 次尝试...")

            # 1. 等待 reCAPTCHA 渲染完成
            anchor_frame = await self._get_recaptcha_frame("anchor")
            if not anchor_frame:
                logger.warning("[Stealth] 未找到 reCAPTCHA anchor iframe")
                await asyncio.sleep(2)
                continue

            # 2. 检查是否已经通过 (偶尔会自动通过)
            try:
                checked = anchor_frame.locator(".recaptcha-checkbox-checked")
                if await checked.count() > 0:
                    logger.info("[Stealth] reCAPTCHA 已自动通过 (无需点击)!")
                    return None
            except Exception:
                pass

            # 3. 模拟人类行为: 先在页面其他区域移动鼠标 (建立轨迹历史)
            logger.info("[Stealth] 建立鼠标轨迹历史...")
            await HumanBehavior.random_mouse_wander(self.page, duration_s=random.uniform(1.5, 3.0))

            # 4. 模拟人类阅读页面
            await HumanBehavior.human_scroll(self.page, scrolls=random.randint(1, 2))
            await HumanBehavior.random_delay(1.0, 3.0)

            # 5. 获取 checkbox 位置
            try:
                checkbox = anchor_frame.locator(".recaptcha-checkbox-border")
                box = await checkbox.bounding_box()
                if not box:
                    logger.warning("[Stealth] 无法获取 checkbox 位置")
                    continue
            except Exception as e:
                logger.warning(f"[Stealth] 获取 checkbox 位置失败: {e}")
                continue

            target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
            target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)

            # 6. 贝塞尔曲线移动鼠标到 checkbox
            logger.info(f"[Stealth] 移动鼠标到 checkbox ({target_x:.0f}, {target_y:.0f})...")
            # 从随机起始位置移动
            start_x = random.uniform(200, 800)
            start_y = random.uniform(200, 600)
            await HumanBehavior.human_move(self.page, target_x, target_y, start_x, start_y)

            # 7. 点击前停顿 (人类犹豫)
            await HumanBehavior.random_delay(0.3, 1.0)

            # 8. 点击 checkbox (含微小偏移)
            click_x = target_x + random.uniform(-2, 2)
            click_y = target_y + random.uniform(-2, 2)
            await self.page.mouse.click(click_x, click_y)
            logger.info("[Stealth] 已点击 reCAPTCHA checkbox")

            # 9. 等待验证结果
            await HumanBehavior.random_delay(2.0, 4.0)

            # 10. 检查是否触发了图像挑战
            challenge_frame = await self._get_recaptcha_frame("bframe")
            if challenge_frame:
                try:
                    challenge = challenge_frame.locator(".rc-imageselect-payload, .rc-imageselect-target")
                    if await challenge.count() > 0:
                        logger.warning(f"[Stealth] 触发了图像挑战! (第 {attempt} 次)")
                        self._challenge_detected = True
                        self._consecutive_challenges = getattr(self, '_consecutive_challenges', 0) + 1
                        await self._take_screenshot(f"stealth_challenge_triggered_{attempt}")

                        # 立即 Fallback 到 ImageRuntime 求解
                        # (stealth 点击 isTrusted=false, 在数据中心 IP 下几乎必然触发挑战)
                        # 不再重试 3 次: 每次重试都会增加被 reCAPTCHA 标记的风险,
                        # 且关闭挑战后重试几乎必然再次触发, 浪费时间
                        STEALTH_FALLBACK_THRESHOLD = 1
                        if self._consecutive_challenges >= STEALTH_FALLBACK_THRESHOLD:
                            logger.warning(
                                f"[Stealth] 触发图像挑战, 立即 Fallback 到图像识别 "
                                f"(isTrusted=false 点击在数据中心 IP 下必然触发挑战)"
                            )
                            # 等待挑战弹窗完全加载 (bframe 内图片可能需要时间渲染)
                            logger.info("[Stealth] 等待挑战弹窗完全加载 (3s)...")
                            await asyncio.sleep(3)
                            fallback_result = await self._fallback_to_image()
                            return fallback_result

                        # 未达阈值: 点击关闭挑战, 重试
                        try:
                            close_btn = challenge_frame.locator(".rc-button-close, button[title='Close']")
                            if await close_btn.count() > 0:
                                await close_btn.click()
                                await asyncio.sleep(1)
                        except Exception:
                            pass

                        # 重置 checkbox
                        try:
                            reset_btn = anchor_frame.locator(".recaptcha-checkbox-spinner")
                            if await reset_btn.count() > 0:
                                await asyncio.sleep(2)
                        except Exception:
                            pass

                        continue
                except RuntimeError:
                    # _fallback_to_image() 失败时 raise RuntimeError, 必须向上传播
                    raise
                except Exception:
                    pass

            # 11. 检查是否通过验证
            try:
                checked = anchor_frame.locator(".recaptcha-checkbox-checked")
                if await checked.count() > 0:
                    logger.info("[Stealth] reCAPTCHA checkbox 验证通过! (未触发图像挑战)")
                    await self._take_screenshot("stealth_success")
                    return None
            except Exception:
                pass

            # 12. 检查是否仍在加载
            try:
                spinner = anchor_frame.locator(".recaptcha-checkbox-spinner")
                if await spinner.count() > 0:
                    logger.info("[Stealth] reCAPTCHA 正在验证中, 等待...")
                    await asyncio.sleep(5)
                    # 再次检查
                    checked = anchor_frame.locator(".recaptcha-checkbox-checked")
                    if await checked.count() > 0:
                        logger.info("[Stealth] reCAPTCHA checkbox 验证通过! (延迟通过)")
                        return None
            except Exception:
                pass

            logger.warning(f"[Stealth] 第 {attempt} 次尝试未通过, 重试...")
            await HumanBehavior.random_delay(1.5, 3.0)

        logger.error(f"[Stealth] {max_attempts} 次尝试均未通过")
        if self._challenge_detected:
            logger.error("[Stealth] 图像挑战被触发, stealth 方案在此环境下可能不适用")
        # 不返回 None (会被误判为"浏览器内通过"), raise 让调用方正确捕获失败
        raise RuntimeError(
            f"Stealth runtime {max_attempts} 次尝试均未通过 reCAPTCHA"
            + (" (图像挑战被触发)" if self._challenge_detected else " (未触发挑战但未通过)")
        )

    # ========================================================
    # Fallback: 图像识别求解 (与 Native runtime 类似)
    # ========================================================
    async def _fallback_to_image(self) -> str | None:
        """
        Fallback: 使用 ImageRuntime 解决图像挑战 (挑战弹窗已显示)
        ImageRuntime 使用 CLS/SEG/CLIP 三引擎

        Stealth runtime 使用常规 Playwright, 共享 context/page 给 ImageRuntime

        关键: 跳过 ImageRuntime 的 checkbox 点击
        - 挑战弹窗已显示, checkbox 无需再点击
        - 在挑战已显示时再次点击 checkbox 会关闭弹窗, 导致 Fallback 失败

        失败处理: raise RuntimeError, 不返回 None (避免 false positive)
        """
        logger.info("[Stealth] Fallback 到图像识别方案...")

        try:
            from runtimes.runtime_image import ImageRuntime
            image_runtime = ImageRuntime()
            # 共享浏览器会话
            image_runtime.playwright = self.playwright
            image_runtime.browser = self.browser
            image_runtime.context = self.context
            image_runtime.page = self.page

            # 跳过 checkbox 点击 (挑战弹窗已显示)
            async def _skip_checkbox():
                logger.info("[Stealth] Fallback: 跳过 checkbox 点击 (挑战弹窗已显示)")
                return True
            image_runtime._click_checkbox = _skip_checkbox

            sitekey = await self.extract_sitekey()
            result = await image_runtime.solve_recaptcha(sitekey, self.page.url)

            logger.info(f"[Stealth] 图像识别 Fallback 完成, result={'None(浏览器内通过)' if result is None else 'token'}")
            return result
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"[Stealth] 图像识别 Fallback 异常: {e}", exc_info=True)
            raise RuntimeError(f"Stealth 图像识别 Fallback 异常: {e}") from e

    # ========================================================
    # 覆盖: 使用真人打字填写凭据
    # ========================================================
    async def _fill_credentials(self):
        """使用真人打字模拟填写账号信息"""
        logger.info("[Stealth] 模拟真人填写账号信息...")

        try:
            email_field = self.page.locator("#username")
            await email_field.wait_for(state="visible")
            await HumanBehavior.random_delay(0.5, 1.5)
            await HumanBehavior.human_type(self.page, "#username", config.ACCOUNT_EMAIL)
            logger.info(f"[Stealth] 邮箱已填写 (真人打字): {config.ACCOUNT_EMAIL}")

            await HumanBehavior.random_delay(0.3, 1.0)

            # 密码字段
            pwd_field = self.page.locator("#password")
            await HumanBehavior.human_type(self.page, "#password", config.ACCOUNT_PASSWORD)
            logger.info("[Stealth] 密码已填写 (真人打字)")

        except Exception as e:
            logger.error(f"[Stealth] 无法填写账号: {e}")
            raise

    # ========================================================
    # 覆盖: 使用真人行为提交表单
    # ========================================================
    async def _submit_form(self):
        """模拟真人提交表单"""
        logger.info("[Stealth] 模拟真人提交表单...")

        try:
            submit_btn = self.page.locator("input[type='submit']")
            if await submit_btn.count() > 0:
                box = await submit_btn.bounding_box()
                if box:
                    # 用真人行为点击提交按钮
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2
                    await HumanBehavior.human_click(self.page, target_x, target_y)
                    logger.info("[Stealth] 表单已提交 (真人点击)")
                else:
                    await submit_btn.click()
            else:
                logger.info("[Stealth] 未找到提交按钮, 使用 JS 提交")
                await self.page.evaluate("document.getElementById('careerform').submit();")
        except Exception as e:
            logger.warning(f"[Stealth] 提交表单失败, 尝试 JS: {e}")
            await self.page.evaluate("document.getElementById('careerform').submit();")

        await asyncio.sleep(3)
        await self._take_screenshot("03_after_submit")
