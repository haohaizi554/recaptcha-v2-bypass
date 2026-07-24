"""
方案 7: 零 CDP 痕迹绕过 (patchright launch_persistent_context + PyAutoGUI OS 级点击)
=============================================================
核心思路:
  1. patchright launch_persistent_context 从源头消除 CDP 痕迹
     - 不发送 Runtime.enable/Console.enable (patchright AST 补丁)
     - navigator.webdriver = undefined (patchright 补丁)
     - 不添加 --enable-automation
  2. PyAutoGUI OS 级点击产生 isTrusted=true 事件 (Win32 SendInput)
  3. Playwright DOM 检测结果 (无需 OpenCV)
  4. 触发挑战时 Fallback 到 ImageRuntime 三引擎

与旧方案 (connect_over_cdp + CDP 断连) 的区别:
  - 旧方案: CDP 连接导航 → 断开 CDP → 点击 → 重连 CDP
    问题: reCAPTCHA 在页面加载第一秒就采集 CDP 痕迹, 断开为时已晚
  - 新方案: patchright launch_persistent_context (从源头不发 Runtime.enable)
    + PyAutoGUI OS 级点击 (isTrusted=true)
    无需断连: CDP 连接存在但不泄漏检测向量

参考:
  - patchright: AST 分析移除 CDP 检测向量
  - SeleniumBase UC 模式: OS 级点击绕过检测
  - reCAPTCHA 四层检测架构: L1-L4 持续打分, 90% 判定在页面加载第一秒
"""

import asyncio
import logging
import math
import os
import platform
import random
import shutil
import subprocess
import tempfile
import time

# DPI 感知: 统一坐标系 (必须在导入 pyautogui 前调用)
# 当前系统 DPI=96 (100% 缩放), CSS 像素 = 物理像素 = PyAutoGUI 坐标
# 调用 SetProcessDPIAware 保证在其他 DPI 环境下坐标一致
if platform.system() == "Windows":
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 使用 patchright 替代 playwright — 从源头移除 CDP 痕迹
# patchright 的反检测补丁仅在 launch_persistent_context / launch 路径下生效
# connect_over_cdp 走附加路径, 补丁不生效 (项目记忆已固化此约束)
try:
    from patchright.async_api import async_playwright

    _USE_PATCHRIGHT = True
except ImportError:
    from playwright.async_api import async_playwright

    _USE_PATCHRIGHT = False

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


class NativeRuntime(BaseBypassRuntime):
    """
    零 CDP 痕迹绕过方案
    核心: patchright launch_persistent_context + PyAutoGUI OS 级点击
    """

    method_name = "native"
    method_desc = "零 CDP 痕迹 (patchright + PyAutoGUI OS 级点击)"

    def __init__(self):
        super().__init__()
        self._user_data_dir = None
        self._using_real_profile = False
        self._checkbox_screen_pos = None  # (screen_x, screen_y)
        self._challenge_detected = False
        self._spiral_hit_offset = None  # 螺旋搜索命中的偏移 (dx, dy), 供后续尝试优化

    # ========================================================
    # Chrome 路径与 Profile 管理
    # ========================================================
    def _find_chrome_path(self) -> str | None:
        """查找系统安装的 Chrome 可执行文件"""
        if platform.system() == "Windows":
            candidates = [
                os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            ]
        else:
            candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser"]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def _get_real_chrome_user_data_dir(self) -> str | None:
        """获取用户真实 Chrome 的 User Data 目录"""
        system = platform.system()
        if system == "Windows":
            path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
        elif system == "Darwin":
            path = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        else:
            path = os.path.expanduser("~/.config/google-chrome")
        if os.path.exists(path):
            return path
        return None

    def _is_chrome_running(self) -> bool:
        """检查 Chrome 是否正在运行"""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return "chrome.exe" in result.stdout
            else:
                result = subprocess.run(
                    ["pgrep", "-f", "chrome"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return len(result.stdout.strip()) > 0
        except Exception:
            return False

    def _kill_chrome(self):
        """终止所有 Chrome 进程 (释放 profile 锁)"""
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chrome.exe"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                subprocess.run(["pkill", "-f", "chrome"], capture_output=True, timeout=10)
            logger.info("[Native] 已终止所有 Chrome 进程")
        except Exception as e:
            logger.warning(f"[Native] 终止 Chrome 失败: {e}")

    def _copy_with_retry(self, src: str, dst: str, max_retries: int = 3) -> bool:
        """带重试的文件复制 (Chrome 刚关闭时文件可能被锁定)"""
        for attempt in range(max_retries):
            try:
                shutil.copy2(src, dst)
                return True
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.warning(f"[Native] 复制失败 (锁定): {os.path.basename(src)}")
                    return False
            except Exception as e:
                logger.warning(f"[Native] 复制失败: {os.path.basename(src)} - {e}")
                return False
        return False

    def _copy_real_profile_data(self, real_dir: str, temp_dir: str):
        """
        从真实 Chrome profile 复制关键文件到临时目录
        只复制 cookies/历史/登录数据等 — 不复制缓存/扩展 (太大)
        这些文件让 reCAPTCHA 看到真实的 Google 会话, 降低风险评分
        """
        real_default = os.path.join(real_dir, "Default")
        temp_default = os.path.join(temp_dir, "Default")
        os.makedirs(temp_default, exist_ok=True)

        # 1. 复制 Local State (含 cookie 加密密钥, 必须!)
        local_state_src = os.path.join(real_dir, "Local State")
        if os.path.exists(local_state_src):
            self._copy_with_retry(local_state_src, os.path.join(temp_dir, "Local State"))
            logger.info("[Native] 已复制 Local State (加密密钥)")

        # 2. 复制 Default profile 的关键文件
        critical_files = [
            "Cookies",  # 旧版 cookie 路径
            "Login Data",  # 保存的密码
            "Preferences",  # 用户偏好设置
            "History",  # 浏览历史
            "Web Data",  # 表单数据
            "TransportSecurity",  # HSTS 数据
        ]

        copied_count = 0
        for fname in critical_files:
            src = os.path.join(real_default, fname)
            if os.path.exists(src):
                if self._copy_with_retry(src, os.path.join(temp_default, fname)):
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
                    logger.info("[Native] 已复制 Network/Cookies (新版 Chrome)")

            for fname in ["Login Data", "Trust Tokens"]:
                src = os.path.join(real_network, fname)
                if os.path.exists(src):
                    self._copy_with_retry(src, os.path.join(temp_network, fname))

        logger.info(f"[Native] 共复制 {copied_count} 个关键文件到临时 profile")

    def _prepare_user_data_dir(self):
        """
        准备 user-data-dir:
          - NATIVE_USE_REAL_PROFILE=True: 直接使用真实 profile (需先关闭 Chrome)
          - NATIVE_USE_REAL_PROFILE=False: 创建临时 profile 并复制关键文件

        关键: 无论使用哪种 profile, 都需要先关闭已运行的 Chrome
        原因: 多个 Chrome 实例会导致 EnumWindows 找到错误的窗口,
              且窗口可能被遮挡或最小化, 导致坐标计算失败
        """
        use_real_profile = getattr(config, "NATIVE_USE_REAL_PROFILE", True)
        auto_kill = getattr(config, "NATIVE_AUTO_KILL_CHROME", True)

        # 统一处理: 无论真实/临时 profile, 都先关闭已运行的 Chrome
        if auto_kill and self._is_chrome_running():
            logger.warning("[Native] Chrome 正在运行, 正在关闭以确保单一实例...")
            self._kill_chrome()
            time.sleep(3)

        if use_real_profile:
            real_dir = self._get_real_chrome_user_data_dir()
            if real_dir:
                self._user_data_dir = real_dir
                self._using_real_profile = True
                logger.info(f"[Native] 使用真实 Chrome profile: {real_dir}")
                logger.info("[Native] 含真实 Google cookies/浏览历史 — reCAPTCHA 风险评分最低")
            else:
                logger.warning("[Native] 未找到真实 Chrome profile, 回退到临时 profile (含关键数据复制)")
                real_dir_fallback = self._get_real_chrome_user_data_dir()
                self._user_data_dir = tempfile.mkdtemp(prefix="native_chrome_")
                self._using_real_profile = False
                if real_dir_fallback:
                    self._copy_real_profile_data(real_dir_fallback, self._user_data_dir)
        else:
            self._user_data_dir = tempfile.mkdtemp(prefix="native_chrome_")
            self._using_real_profile = False
            # 即使使用临时 profile, 也复制真实 cookies 降低 reCAPTCHA 风险
            real_dir = self._get_real_chrome_user_data_dir()
            if real_dir and os.path.exists(real_dir):
                self._copy_real_profile_data(real_dir, self._user_data_dir)
            logger.info("[Native] 使用临时 profile (已复制真实 cookies/登录数据)")

    # ========================================================
    # 浏览器初始化 (覆盖)
    # ========================================================
    async def init_browser(self):
        """
        使用 patchright launch_persistent_context 启动 Chrome

        关键: patchright 的反检测补丁仅在 launch_persistent_context 路径下生效
              - 不发送 Runtime.enable/Console.enable
              - navigator.webdriver = undefined
              - 不添加 --enable-automation

        与 connect_over_cdp 的区别:
          connect_over_cdp 走附加路径, patchright 补丁不生效
          launch_persistent_context 走启动路径, patchright 补丁完整生效
        """
        logger.info("[Native] 初始化: patchright launch_persistent_context...")

        if not _USE_PATCHRIGHT:
            logger.error("[Native] patchright 未安装! 此方案需要 patchright 才能生效")
            logger.error("[Native] 安装: pip install patchright && patchright install chromium")
            raise RuntimeError("patchright 未安装, 无法启动零 CDP 痕迹方案")

        # 1. 准备 user-data-dir
        self._prepare_user_data_dir()

        # 2. patchright launch_persistent_context
        self.playwright = await async_playwright().start()

        browser_channel = getattr(config, "NATIVE_BROWSER_CHANNEL", "chrome")

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            channel=browser_channel,
            headless=False,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--disable-features=InfiniteSessionRestore",
                "--start-maximized",
                # 注意: 不加 --enable-automation
                # 注意: 不加 --disable-blink-features=AutomationControlled (patchright 自己处理)
                # 注意: 不加 --disable-extensions (保留真实扩展增强真实性)
            ],
            # 不设 viewport: 让 Chrome 使用 --start-maximized 的自然视口
            # 设固定 viewport 会导致 CSS 坐标与物理窗口不一致 (尤其在高 DPI 下)
            # 不设 locale/timezone_id/user_agent: 使用系统真实值 (避免指纹矛盾)
        )

        # 3. 获取 page (persistent context 自动创建一个空白页)
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)

        logger.info("[Native] 浏览器初始化完成 (patchright launch_persistent_context)")

        # 4. 确保窗口最大化且可见 (解决高 DPI 下窗口最小化/遮挡问题)
        # 4a. win32gui 先恢复+最大化 OS 窗口
        self._ensure_window_maximized()
        # 4b. CDP 强制更新 Chrome 内部窗口状态 (触发 resize 事件, 更新 DOM screenX)
        await self._ensure_window_maximized_async()
        # 4c. 等待 DOM window 属性更新 (screenX 从 -21333 变为正常值)
        await self._wait_for_dom_window_update(timeout=8)

        # 5. 指纹诊断
        await self._diagnose_fingerprint()

    # ========================================================
    # 窗口管理 (高 DPI 兼容)
    # ========================================================
    def _get_dpi_scale(self) -> float:
        """
        获取系统 DPI 缩放比例
        返回: 物理像素 / CSS像素 的比值 (如 1.5 表示 150% 缩放)

        用于坐标转换: PyAutoGUI 使用物理像素, Chrome DOM 使用 CSS 像素
        """
        try:
            import ctypes

            hdc = ctypes.windll.user32.GetDC(0)
            LOGPIXELSX = 88
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            ctypes.windll.user32.ReleaseDC(0, hdc)
            scale = dpi / 96.0
            logger.info(f"[Native] 系统 DPI: {dpi} ({scale * 100:.0f}% 缩放)")
            return scale
        except Exception as e:
            logger.warning(f"[Native] 获取 DPI 失败, 默认 1.0: {e}")
            return 1.0

    def _ensure_window_maximized(self):
        """
        确保 Chrome 窗口最大化且可见 (win32gui + CDP 双重保障)

        问题: launch_persistent_context 启动的 Chrome 可能以最小化状态启动
              win32gui ShowWindow(SW_MAXIMIZE) 能最大化 OS 窗口,
              但 Chrome 内部状态和 DOM 仍认为窗口最小化 (screenX=-21333)

        解决:
          1. win32gui 先恢复+最大化 OS 窗口
          2. CDP Browser.setWindowBounds 强制更新 Chrome 内部窗口状态
          3. 等待 DOM 更新 (screenX 从 -21333 变为正常值)
        """
        if platform.system() != "Windows":
            return

        try:
            import win32con
            import win32gui

            # 步骤1: win32gui 恢复+最大化
            hwnd = None
            for _ in range(10):
                hwnd = self._find_main_chrome_window()
                if hwnd:
                    break
                time.sleep(0.5)

            if not hwnd:
                logger.warning("[Native] _ensure_window_maximized: 未找到 Chrome 窗口")
                return

            if win32gui.IsIconic(hwnd):
                logger.info("[Native] Chrome 窗口已最小化, 正在恢复...")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(1)

            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            time.sleep(0.5)

            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            time.sleep(0.5)

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            logger.info(
                f"[Native] win32gui 窗口已最大化: ({left}, {top}, {right}, {bottom}), "
                f"尺寸={right - left}x{bottom - top}"
            )

        except Exception as e:
            logger.warning(f"[Native] win32gui 最大化异常: {e}")

    async def _ensure_window_maximized_async(self):
        """
        异步版窗口最大化: 使用 CDP 强制更新 Chrome 内部窗口状态

        CDP Browser.setWindowBounds 通过 Chrome 内部窗口管理器操作,
        会正确触发 resize 事件并更新 DOM (window.screenX 等)
        """
        try:
            # 创建 CDP session
            client = await self.context.new_cdp_session(self.page)

            # 获取当前窗口 ID 和状态
            result = await client.send("Browser.getWindowForTarget")
            window_id = result.get("windowId")
            if not window_id:
                logger.warning("[Native] CDP: 无法获取 windowId")
                await client.detach()
                return

            bounds_result = await client.send("Browser.getWindowBounds", {"windowId": window_id})
            bounds = bounds_result.get("bounds", {})
            state = bounds.get("windowState", "unknown")
            logger.info(
                f"[Native] CDP 窗口状态: {state}, "
                f"bounds=({bounds.get('left')}, {bounds.get('top')}, "
                f"{bounds.get('width')}x{bounds.get('height')})"
            )

            # 如果窗口不是最大化/正常状态, 强制设置为最大化
            if state in ("minimized", "unknown"):
                logger.info("[Native] CDP: 窗口最小化, 强制最大化...")
                await client.send(
                    "Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "maximized"}}
                )
                await asyncio.sleep(2)
                logger.info("[Native] CDP: 窗口已通过 CDP 最大化")
            elif state == "normal":
                logger.info("[Native] CDP: 窗口正常, 强制最大化...")
                await client.send(
                    "Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "maximized"}}
                )
                await asyncio.sleep(2)

            await client.detach()

        except Exception as e:
            logger.warning(f"[Native] CDP 窗口管理异常 (不影响主流程): {e}")

    async def _wait_for_dom_window_update(self, timeout: int = 5) -> bool:
        """
        等待 DOM window 属性更新 (screenX 从 -21333 变为正常值)

        在 CDP 最大化窗口后, DOM 需要时间接收 resize 事件并更新
        返回 True 表示 screenX 已更新为有效值
        """
        for i in range(timeout):
            await asyncio.sleep(1)
            try:
                screen_x = await self.page.evaluate("window.screenX")
                if screen_x is not None and screen_x > -1000:
                    screen_y = await self.page.evaluate("window.screenY")
                    logger.info(f"[Native] DOM window 已更新: screenX={screen_x}, screenY={screen_y} (第 {i + 1}s)")
                    return True
            except Exception:
                pass
            if (i + 1) % 2 == 0:
                logger.info(f"[Native] 等待 DOM window 更新... (第 {i + 1}/{timeout}s)")
        return False

    # ========================================================
    # 指纹诊断
    # ========================================================
    async def _diagnose_fingerprint(self):
        """验证 patchright 反检测补丁是否生效"""
        logger.info("[Native] ====== 指纹诊断 ======")
        try:
            await self.page.goto("about:blank", wait_until="domcontentloaded")

            diagnostics = await self.page.evaluate("""() => {
                const result = {};
                result.webdriver = String(navigator.webdriver);
                result.webdriver_type = typeof navigator.webdriver;
                result.has_chrome = typeof window.chrome !== 'undefined';
                result.userAgent = navigator.userAgent;
                result.platform = navigator.platform;
                result.languages = JSON.stringify(navigator.languages);
                result.hardwareConcurrency = navigator.hardwareConcurrency;
                result.plugins_count = navigator.plugins.length;
                try {
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl');
                    if (gl) {
                        result.webglVendor = gl.getParameter(gl.VENDOR);
                        result.webglRenderer = gl.getParameter(gl.RENDERER);
                    }
                } catch(e) { result.webglError = e.message; }
                result.cdc_traces = Object.keys(document).filter(k => k.match(/^cdc_|^[$]cdc_/)).length;
                return result;
            }""")

            issues = []
            if diagnostics.get("webdriver") == "true":
                issues.append("navigator.webdriver = true (检测到自动化!)")
            if diagnostics.get("cdc_traces", 0) > 0:
                issues.append(f"检测到 {diagnostics['cdc_traces']} 个 cdc_ 痕迹")

            logger.info(
                f"[Native] webdriver: {diagnostics.get('webdriver')} (type: {diagnostics.get('webdriver_type')})"
            )
            logger.info(f"[Native] UA: {diagnostics.get('userAgent', '')[:80]}")
            logger.info(f"[Native] platform: {diagnostics.get('platform')}")
            logger.info(f"[Native] languages: {diagnostics.get('languages')}")
            logger.info(f"[Native] hardwareConcurrency: {diagnostics.get('hardwareConcurrency')}")
            logger.info(f"[Native] plugins: {diagnostics.get('plugins_count')} 个")
            logger.info(
                f"[Native] WebGL: {diagnostics.get('webglVendor', '?')} / {diagnostics.get('webglRenderer', '?')[:60]}"
            )
            logger.info(f"[Native] cdc_ 痕迹: {diagnostics.get('cdc_traces', 0)} 个")

            if issues:
                logger.warning(f"[Native] 指纹诊断发现 {len(issues)} 个问题:")
                for issue in issues:
                    logger.warning(f"  - {issue}")
            else:
                logger.info("[Native] 指纹诊断通过: 无自动化痕迹")

        except Exception as e:
            logger.warning(f"[Native] 指纹诊断失败 (不影响主流程): {e}")
        logger.info("[Native] ========================")

    # ========================================================
    # 导航 (覆盖: 跳过已失效的 Atos 源网站, 直接访问 SuccessFactors)
    # ========================================================
    async def navigate_to_target(self):
        """
        导航到目标页面 (复用父类完整导航流程)

        流程: Atos 源网站 → profileWidget 登录元素 / Apply now 链接 → SuccessFactors 登录页
        与音频方案完全一致, 父类已处理:
          - JS 框架就绪检测 (j2w.TC.handleViewProfileAction)
          - profileWidget 优先点击 + Apply 链接回退
          - 链接优先级缓存
          - SF 页面慢加载等待
        """
        await super().navigate_to_target()

    # ========================================================
    # 坐标计算: 获取 checkbox 屏幕坐标
    # ========================================================
    async def _get_checkbox_screen_position(self) -> tuple[float, float]:
        """
        计算 reCAPTCHA checkbox 的屏幕坐标 (物理像素, 供 PyAutoGUI 使用)

        关键修复 (v3):
          1. 最大化窗口没有左边框 — chrome_w_css 是 DPI 虚拟化伪影, 不是真实边框
             旧代码用 chrome_w_css/2 作为左边框, 导致 X 坐标偏右 ~118px (完全错过 checkbox)
          2. 用系统 DPI 缩放 (1.5) 替代窗口尺寸比例 (1.5153)
             窗口尺寸包含最大化溢出 (-11px), 不是纯 CSS→物理 比例
          3. 用 window.screenX/Y (CSS) 作为窗口屏幕位置, 乘以 DPI 得物理位置

        坐标公式:
          CSS 屏幕坐标 = window.screenX + horizontal_chrome_offset + viewport_x
          CSS 屏幕坐标 = window.screenY + vertical_chrome_offset   + viewport_y
          物理屏幕坐标 = CSS 屏幕坐标 * dpi_scale

        其中:
          - horizontal_chrome_offset = 0 (最大化窗口无左边框)
          - vertical_chrome_offset = outerH - innerH (标题栏+标签+地址栏, 全在顶部)
          - dpi_scale = 系统 DPI / 96 (如 144/96 = 1.5)
        """
        # 1. 获取 reCAPTCHA checkbox bounding_box (CSS 像素, 相对于视口)
        anchor_frame = await self._get_recaptcha_frame("anchor")
        if not anchor_frame:
            raise RuntimeError("未找到 reCAPTCHA anchor iframe")

        checkbox = anchor_frame.locator(".recaptcha-checkbox-border")
        box = await checkbox.bounding_box()
        if not box:
            raise RuntimeError("无法获取 checkbox bounding_box (可能不可见)")

        # 2. 获取 Chrome CSS 尺寸
        win = await self.page.evaluate("""() => ({
            screenX: window.screenX,
            screenY: window.screenY,
            outerW: window.outerWidth,
            outerH: window.outerHeight,
            innerW: window.innerWidth,
            innerH: window.innerHeight,
            dpr: window.devicePixelRatio,
            screenW: window.screen.width,
            screenH: window.screen.height
        })""")

        # 3. 计算 checkbox 中心 (CSS, 相对于视口)
        checkbox_center_x = box["x"] + box["width"] / 2
        checkbox_center_y = box["y"] + box["height"] / 2

        # 4. CSS chrome 偏移
        chrome_h_css = win["outerH"] - win["innerH"]  # 标题栏+标签+地址栏 (全在顶部)
        chrome_w_css = win["outerW"] - win["innerW"]  # DPI 虚拟化伪影 (不是真实边框)

        # 5. 系统 DPI 缩放 (CSS → 物理像素)
        dpi_scale = self._get_dpi_scale()

        # 6. 计算 CSS 屏幕坐标
        # 最大化窗口: 水平无左边框 (chrome_w_css 是伪影), 垂直 chrome 全在顶部
        # window.screenX/Y 是窗口外框在 CSS 屏幕上的位置
        is_maximized = abs(win["screenX"]) <= 20 and abs(win["screenY"]) <= 20
        h_chrome_offset = 0 if is_maximized else chrome_w_css / 2
        v_chrome_offset = chrome_h_css

        css_screen_x = win["screenX"] + h_chrome_offset + checkbox_center_x
        css_screen_y = win["screenY"] + v_chrome_offset + checkbox_center_y

        # 7. 转换为物理屏幕坐标
        screen_x_phys = css_screen_x * dpi_scale
        screen_y_phys = css_screen_y * dpi_scale

        # 8. 诊断日志
        logger.info("[Native] ====== 坐标计算诊断 (v3: DPI+screenX) ======")
        logger.info(f"[Native] DPI 缩放: {dpi_scale} ({dpi_scale * 100:.0f}%)")
        logger.info(f"[Native] devicePixelRatio: {win['dpr']}")
        logger.info(f"[Native] DOM screen 尺寸: {win['screenW']}x{win['screenH']} (CSS)")
        logger.info(f"[Native] DOM viewport 尺寸: {win['innerW']}x{win['innerH']} (CSS)")
        logger.info(f"[Native] DOM window 尺寸: {win['outerW']}x{win['outerH']} (CSS)")
        logger.info(f"[Native] window.screenX/Y: ({win['screenX']}, {win['screenY']}) (CSS)")
        logger.info(f"[Native] 窗口状态: {'最大化' if is_maximized else '普通'}")
        logger.info(f"[Native] Chrome 偏移: h_chrome={h_chrome_offset}, v_chrome={v_chrome_offset} (CSS)")
        logger.info(f"[Native] checkbox 视口坐标: ({checkbox_center_x:.1f}, {checkbox_center_y:.1f}) (CSS)")
        logger.info(f"[Native] CSS 屏幕坐标: ({css_screen_x:.1f}, {css_screen_y:.1f})")
        logger.info(f"[Native] 物理 屏幕坐标: ({screen_x_phys:.1f}, {screen_y_phys:.1f})")

        # 9. 边界检查 + PyAutoGUI 屏幕尺寸
        try:
            import pyautogui

            screen_w, screen_h = pyautogui.size()
            logger.info(f"[Native] PyAutoGUI 屏幕尺寸: {screen_w}x{screen_h}")
            if screen_x_phys < 0 or screen_y_phys < 0:
                logger.warning(f"[Native] 物理坐标为负值: ({screen_x_phys:.1f}, {screen_y_phys:.1f})")
            if screen_x_phys > screen_w or screen_y_phys > screen_h:
                logger.warning(
                    f"[Native] 物理坐标超出屏幕 ({screen_w}x{screen_h}): ({screen_x_phys:.1f}, {screen_y_phys:.1f})"
                )
        except Exception:
            pass
        logger.info("[Native] =============================")

        return (screen_x_phys, screen_y_phys)

    async def _inject_click_listener(self) -> bool:
        """
        在 reCAPTCHA anchor iframe 内注入 mousedown 监听器
        用于验证 PyAutoGUI 点击是否命中 checkbox

        返回 True 表示监听器注入成功
        点击后调用 _check_click_received() 检查是否收到事件
        """
        try:
            anchor_frame = await self._get_recaptcha_frame("anchor")
            if not anchor_frame:
                return False

            await anchor_frame.evaluate("""() => {
                window.__click_received = false;
                window.__click_detail = null;
                document.addEventListener('mousedown', function(e) {
                    window.__click_received = true;
                    window.__click_detail = {
                        isTrusted: e.isTrusted,
                        clientX: e.clientX,
                        clientY: e.clientY,
                        target: e.target.className
                    };
                }, { once: true, capture: true });
            }""")
            logger.info("[Native] 已注入 mousedown 监听器 (验证点击命中)")
            return True
        except Exception as e:
            logger.warning(f"[Native] 注入点击监听器失败: {e}")
            return False

    async def _check_click_received(self) -> dict | None:
        """
        检查 mousedown 监听器是否收到了点击事件
        返回事件详情 dict, 或 None (未收到)
        """
        try:
            anchor_frame = await self._get_recaptcha_frame("anchor")
            if not anchor_frame:
                return None

            result = await anchor_frame.evaluate("""() => ({
                received: window.__click_received || false,
                detail: window.__click_detail || null
            })""")
            if result.get("received"):
                logger.info(f"[Native] ✓ 点击监听器收到 mousedown 事件: {result['detail']}")
            else:
                logger.warning("[Native] ✗ 点击监听器未收到 mousedown 事件 (点击可能未命中 checkbox)")
            return result
        except Exception as e:
            logger.warning(f"[Native] 检查点击监听器异常: {e}")
            return None

    # ========================================================
    # Win32 精确坐标校准 (OS 级客户端区域, 无需截图)
    # ========================================================
    async def _get_checkbox_position_by_win32(self) -> tuple[float, float] | None:
        """
        使用 win32gui GetClientRect + ClientToScreen 精确获取 checkbox 物理坐标

        核心优势 (相比截图差异法):
          - 直接使用 OS 级窗口信息, 无需截图, 无渲染时机问题
          - GetClientRect 返回客户端区域 (排除窗口边框), 精确到物理像素
          - ClientToScreen 将客户端区域原点转换为屏幕物理坐标
          - 通过 DOM outerHeight-innerHeight 计算 Chrome UI 高度 (标签栏+地址栏)

        算法:
          1. ClientToScreen(0,0) → 客户端区域在物理屏幕上的原点 (排除 OS 窗口边框)
          2. GetWindowRect → 整个窗口矩形 (含边框), 用于计算真实 DPI
          3. DOM: outerW/outerH (CSS, 含边框) vs innerW/innerH (CSS, 纯视口)
          4. DPI = GetWindowRect 尺寸 / outer 尺寸 (两者都含边框, 比值准确)
          5. Chrome UI 高度 (物理) = (outerH - innerH) * DPI_y
          6. 视口物理原点 = (client_origin_x, client_origin_y + chrome_ui_h)
          7. checkbox 物理坐标 = 视口原点 + checkbox_CSS * DPI

        返回: (physical_x, physical_y) 或 None (校准失败)
        """
        try:
            import pyautogui
            import win32gui

            # 1. 确保 Chrome 在前台
            if not self._verify_chrome_foreground():
                logger.error("[Native] Win32校准: Chrome 不在前台, 无法校准")
                return None

            # 2. 获取 Chrome 窗口句柄
            hwnd = self._find_main_chrome_window()
            if not hwnd:
                logger.warning("[Native] Win32校准: 未找到 Chrome 窗口")
                return None

            # 3. 获取窗口矩形 (含边框, 物理像素)
            win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
            win_w_phys = win_right - win_left
            win_h_phys = win_bottom - win_top

            # 4. 获取客户端区域 (排除边框, 物理像素)
            #    GetClientRect 返回相对于窗口左上角的坐标: (0, 0, w, h)
            _, _, client_w, client_h = win32gui.GetClientRect(hwnd)

            # ClientToScreen 将客户端区域原点 (0, 0) 转换为屏幕物理坐标
            # 这个坐标排除了 OS 窗口边框, 是 Chrome 可绘制区域的起点
            client_x, client_y = win32gui.ClientToScreen(hwnd, (0, 0))

            logger.info(
                f"[Native] Win32校准: 窗口矩形=({win_left},{win_top},{win_right},{win_bottom}), "
                f"尺寸={win_w_phys}x{win_h_phys}"
            )
            logger.info(
                f"[Native] Win32校准: 客户端原点=({client_x},{client_y}), 尺寸={client_w}x{client_h} (物理像素)"
            )

            # 5. 获取 DOM 窗口信息 (CSS 像素)
            win = await self.page.evaluate("""() => ({
                outerW: window.outerWidth,
                outerH: window.outerHeight,
                innerW: window.innerWidth,
                innerH: window.innerHeight,
                screenX: window.screenX,
                screenY: window.screenY
            })""")

            logger.info(
                f"[Native] Win32校准: DOM outer={win['outerW']}x{win['outerH']}, "
                f"inner={win['innerW']}x{win['innerH']}, "
                f"screenX/Y=({win['screenX']},{win['screenY']}) (CSS)"
            )

            # 6. 计算真实 DPI (窗口矩形物理尺寸 / DOM outer CSS 尺寸)
            #    两者都包含窗口边框, 所以比值就是真实的 CSS→物理 缩放比
            if win["outerW"] > 0 and win["outerH"] > 0:
                dpi_x = win_w_phys / win["outerW"]
                dpi_y = win_h_phys / win["outerH"]
            else:
                dpi_scale = self._get_dpi_scale()
                dpi_x = dpi_y = dpi_scale
                logger.warning(f"[Native] Win32校准: DOM outer 尺寸异常, 使用系统 DPI={dpi_scale}")

            logger.info(
                f"[Native] Win32校准: 真实 DPI=({dpi_x:.4f},{dpi_y:.4f}) (系统 DPI={self._get_dpi_scale():.4f})"
            )

            # 7. 计算 Chrome UI 高度 (标签栏 + 地址栏, 全在顶部)
            #    修正: 旧方法 (outerH - innerH) * dpi_y 包含了窗口边框,
            #    但 ClientToScreen 已排除边框, 导致 Y 轴系统性偏移约 30px
            #    新方法: client_h (无边框) - innerH * dpi_y (视口物理高度)
            #    client_h 来自 GetClientRect, 已排除 OS 窗口边框
            viewport_h_phys = win["innerH"] * dpi_y
            chrome_ui_h_phys = client_h - viewport_h_phys
            chrome_ui_h_css = chrome_ui_h_phys / dpi_y if dpi_y > 0 else 0

            # 边框高度 (用于日志诊断)
            border_h_phys = win_h_phys - client_h
            logger.info(
                f"[Native] Win32校准: Chrome UI 高度={chrome_ui_h_css:.1f}CSS "
                f"({chrome_ui_h_phys:.1f}物理) [客户端区域法, 排除窗口边框 {border_h_phys}px]"
            )

            # 8. 视口物理原点
            #    客户端区域原点 + Chrome UI 高度 = 视口顶部
            #    X 方向: 现代 Chrome 无水平 UI (标签栏和地址栏都在顶部)
            #    即使窗口有左右边框, ClientToScreen 已排除边框
            viewport_phys_x = float(client_x)
            viewport_phys_y = float(client_y) + chrome_ui_h_phys

            # 视口物理尺寸 (用于验证)
            viewport_phys_w = win["innerW"] * dpi_x
            viewport_phys_h = win["innerH"] * dpi_y

            logger.info(
                f"[Native] Win32校准: 视口原点=({viewport_phys_x:.1f},{viewport_phys_y:.1f}), "
                f"尺寸={viewport_phys_w:.0f}x{viewport_phys_h:.0f} (物理像素)"
            )

            # 9. 获取 checkbox CSS 坐标 (相对于视口)
            anchor_frame = await self._get_recaptcha_frame("anchor")
            if not anchor_frame:
                logger.warning("[Native] Win32校准: 未找到 anchor iframe")
                return None

            checkbox = anchor_frame.locator(".recaptcha-checkbox-border")
            box = await checkbox.bounding_box()
            if not box:
                logger.warning("[Native] Win32校准: 无法获取 checkbox bounding_box")
                return None

            checkbox_css_x = box["x"] + box["width"] / 2
            checkbox_css_y = box["y"] + box["height"] / 2

            # 10. 计算 checkbox 物理坐标
            #     视口原点 + checkbox CSS 坐标 * 真实 DPI
            checkbox_phys_x = viewport_phys_x + checkbox_css_x * dpi_x
            checkbox_phys_y = viewport_phys_y + checkbox_css_y * dpi_y

            logger.info(
                f"[Native] Win32校准: checkbox CSS=({checkbox_css_x:.1f},{checkbox_css_y:.1f}) → "
                f"Phys=({checkbox_phys_x:.1f},{checkbox_phys_y:.1f})"
            )

            # 11. 边界检查
            screen_w, screen_h = pyautogui.size()
            if checkbox_phys_x < 0 or checkbox_phys_y < 0:
                logger.warning(f"[Native] Win32校准: checkbox 坐标为负 ({checkbox_phys_x:.1f},{checkbox_phys_y:.1f})")
                return None
            if checkbox_phys_x > screen_w or checkbox_phys_y > screen_h:
                logger.warning(
                    f"[Native] Win32校准: checkbox 坐标超出屏幕 "
                    f"({checkbox_phys_x:.1f},{checkbox_phys_y:.1f}) > "
                    f"({screen_w}x{screen_h})"
                )
                return None

            # 12. 交叉验证: 与系统 DPI 计算结果对比
            #     如果两者差距很大, 说明窗口尺寸与预期不符, 可能需要调整
            sys_dpi = self._get_dpi_scale()
            sys_checkbox_x = viewport_phys_x + checkbox_css_x * sys_dpi
            sys_checkbox_y = viewport_phys_y + checkbox_css_y * sys_dpi
            diff_x = abs(checkbox_phys_x - sys_checkbox_x)
            diff_y = abs(checkbox_phys_y - sys_checkbox_y)

            if diff_x > 30 or diff_y > 30:
                logger.info(
                    f"[Native] Win32校准: 与系统DPI计算偏差 "
                    f"(Δx={diff_x:.1f}, Δy={diff_y:.1f}), "
                    f"真实DPI=({dpi_x:.4f},{dpi_y:.4f}), 系统DPI={sys_dpi:.4f}"
                )

            logger.info(
                f"[Native] ✓ Win32校准成功! "
                f"视口原点=({viewport_phys_x:.1f},{viewport_phys_y:.1f}), "
                f"DPI=({dpi_x:.4f},{dpi_y:.4f}), "
                f"checkbox CSS=({checkbox_css_x:.1f},{checkbox_css_y:.1f}) → "
                f"Phys=({checkbox_phys_x:.1f},{checkbox_phys_y:.1f})"
            )

            return (checkbox_phys_x, checkbox_phys_y)

        except ImportError:
            logger.warning("[Native] Win32校准: win32gui 不可用 (非 Windows?)")
            return None
        except Exception as e:
            logger.warning(f"[Native] Win32校准异常: {e}")
            import traceback

            logger.warning(f"[Native] {traceback.format_exc()}")
            return None

    # ========================================================
    # 截图差异校准获取坐标 (fallback: DPI 无关, 经验性映射)
    # ========================================================
    async def _get_checkbox_position_by_markers(self) -> tuple[float, float] | None:
        """
        通过全屏品红覆盖 + 截图差异法校准 CSS→物理坐标映射

        核心思路:
          1. 截图 A (无覆盖)
          2. 注入全屏品红覆盖层 (position:fixed, 100vw×100vh)
          3. 截图 B (有覆盖)
          4. 差异检测找到视口在物理屏幕上的精确边界
          5. 从视口边界计算 checkbox 物理坐标

        优势 (相比小标记方案):
          - 全屏覆盖产生数万像素变化, 远超噪声水平
          - 直接获取视口边界, 无需猜测窗口边框/chrome 偏移
          - 不依赖单个标记的渲染可见性
          - 覆盖层 z-index 最高, 不受页面内容遮挡

        返回: (physical_x, physical_y) 或 None (校准失败)
        """
        try:
            import os

            import numpy as np
            import pyautogui

            # 0. 关键: 确保 Chrome 在前台 (pyautogui.screenshot 捕获物理屏幕)
            # 如果 Chrome 不在前台, 截图会捕获到其他应用 (如 TRAE IDE), 导致校准完全失败
            if not self._verify_chrome_foreground():
                logger.error("[Native] 截图差异: Chrome 不在前台, 无法校准")
                return None

            # 1. 获取 checkbox CSS 坐标 (相对于视口)
            anchor_frame = await self._get_recaptcha_frame("anchor")
            if not anchor_frame:
                logger.warning("[Native] 截图差异: 未找到 anchor iframe")
                return None

            checkbox = anchor_frame.locator(".recaptcha-checkbox-border")
            box = await checkbox.bounding_box()
            if not box:
                logger.warning("[Native] 截图差异: 无法获取 checkbox bounding_box")
                return None

            checkbox_css_x = box["x"] + box["width"] / 2
            checkbox_css_y = box["y"] + box["height"] / 2

            dpi_scale = self._get_dpi_scale()

            # 2. 获取视口尺寸
            viewport = await self.page.evaluate("""() => ({
                w: window.innerWidth,
                h: window.innerHeight
            })""")

            logger.info(
                f"[Native] 截图差异: 全屏覆盖法, "
                f"viewport={viewport['w']}x{viewport['h']}, DPI={dpi_scale}, "
                f"checkbox CSS=({checkbox_css_x:.1f},{checkbox_css_y:.1f})"
            )

            # 3. 截图 A (无覆盖)
            screen_a = pyautogui.screenshot()
            screen_a_np = np.array(screen_a)  # RGB

            # 4. 注入全屏品红覆盖层
            await self.page.evaluate("""() => {
                const overlay = document.createElement('div');
                overlay.id = '__cal_overlay';
                overlay.style.cssText = `
                    position: fixed;
                    left: 0;
                    top: 0;
                    width: 100vw;
                    height: 100vh;
                    background: rgb(255, 0, 255);
                    z-index: 2147483647;
                    pointer-events: none;
                `;
                document.body.appendChild(overlay);
            }""")

            # 等待覆盖层渲染
            await asyncio.sleep(0.8)

            # 5. 截图 B (有覆盖)
            screen_b = pyautogui.screenshot()
            screen_b_np = np.array(screen_b)

            # 6. 立即清除覆盖层
            await self._remove_calibration_markers()

            # 7. 计算差异
            diff = np.abs(screen_b_np.astype(np.int16) - screen_a_np.astype(np.int16))
            diff_sum = np.sum(diff, axis=2)  # R+G+B 差异总和

            # 差异阈值: 变化总和 > 100 (品红 vs 普通背景差异很大)
            diff_threshold = 100
            changed_mask = diff_sum > diff_threshold

            total_changed = int(np.sum(changed_mask))

            # 8. 获取屏幕尺寸
            screen_w, screen_h = pyautogui.size()
            logger.info(f"[Native] 截图差异: 差异像素={total_changed}, 屏幕尺寸={screen_w}x{screen_h}")

            # 预期变化量: 视口面积 * DPI^2 (至少视口的一半应被覆盖)
            expected_min = int(viewport["w"] * viewport["h"] * dpi_scale * dpi_scale * 0.3)
            logger.info(
                f"[Native] 截图差异: 预期最少变化像素={expected_min} "
                f"(viewport {viewport['w']}x{viewport['h']} * DPI² * 0.3)"
            )

            if total_changed < expected_min:
                # 保存调试截图
                debug_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "screenshots")
                os.makedirs(debug_dir, exist_ok=True)
                screen_a.save(os.path.join(debug_dir, "calib_before.png"))
                screen_b.save(os.path.join(debug_dir, "calib_after.png"))
                logger.warning(
                    f"[Native] 截图差异: 变化像素不足 ({total_changed} < {expected_min}), "
                    f"覆盖层可能未渲染, 已保存调试截图"
                )
                return None

            # 9. 找到变化区域的边界框 (视口在物理屏幕上的位置)
            changed_coords = np.where(changed_mask)
            all_y = changed_coords[0]  # 行 (Y)
            all_x = changed_coords[1]  # 列 (X)

            # 使用 1%-99% 分位数过滤极端离群点 (避免噪声边缘)
            # 注意: 5%-95% 会切掉 10% 边界, 导致检测到的视口只有实际大小的 ~90%
            x_low = int(np.percentile(all_x, 1))
            x_high = int(np.percentile(all_x, 99))
            y_low = int(np.percentile(all_y, 1))
            y_high = int(np.percentile(all_y, 99))

            # 视口物理边界
            viewport_phys_left = float(x_low)
            viewport_phys_top = float(y_low)
            viewport_phys_right = float(x_high)
            viewport_phys_bottom = float(y_high)
            viewport_phys_w = viewport_phys_right - viewport_phys_left
            viewport_phys_h = viewport_phys_bottom - viewport_phys_top

            logger.info(
                f"[Native] 截图差异: 视口物理边界=({viewport_phys_left:.0f},{viewport_phys_top:.0f})"
                f"-({viewport_phys_right:.0f},{viewport_phys_bottom:.0f}), "
                f"尺寸={viewport_phys_w:.0f}x{viewport_phys_h:.0f}"
            )

            # 10. 验证: 视口物理尺寸应接近 CSS 尺寸 * DPI
            expected_phys_w = viewport["w"] * dpi_scale
            expected_phys_h = viewport["h"] * dpi_scale
            w_ratio = viewport_phys_w / expected_phys_w if expected_phys_w > 0 else 0
            h_ratio = viewport_phys_h / expected_phys_h if expected_phys_h > 0 else 0

            logger.info(
                f"[Native] 截图差异: 尺寸验证 "
                f"物理={viewport_phys_w:.0f}x{viewport_phys_h:.0f}, "
                f"预期={expected_phys_w:.0f}x{expected_phys_h:.0f}, "
                f"比例=({w_ratio:.2f}, {h_ratio:.2f})"
            )

            if w_ratio < 0.7 or w_ratio > 1.3 or h_ratio < 0.7 or h_ratio > 1.3:
                logger.warning(
                    f"[Native] 截图差异: 视口尺寸比例异常 (w={w_ratio:.2f}, h={h_ratio:.2f}), 校准可能不准确"
                )

            # 11. 计算 checkbox 物理坐标
            #     核心修复: 使用实际视口→物理像素比例, 而非系统 DPI
            #     系统 DPI (1.5) 是全局缩放, 但 Chrome 窗口可能未填满屏幕,
            #     实际 CSS→物理 映射比例 = 视口物理尺寸 / 视口 CSS 尺寸
            actual_dpi_x = viewport_phys_w / viewport["w"] if viewport["w"] > 0 else dpi_scale
            actual_dpi_y = viewport_phys_h / viewport["h"] if viewport["h"] > 0 else dpi_scale

            # 如果实际 DPI 与系统 DPI 差异较大, 使用实际 DPI (更准确)
            if abs(actual_dpi_x - dpi_scale) > 0.1 or abs(actual_dpi_y - dpi_scale) > 0.1:
                logger.info(
                    f"[Native] 截图差异: 使用实际 DPI ({actual_dpi_x:.3f}, {actual_dpi_y:.3f}) "
                    f"替代系统 DPI ({dpi_scale:.3f})"
                )
                effective_dpi_x = actual_dpi_x
                effective_dpi_y = actual_dpi_y
            else:
                effective_dpi_x = dpi_scale
                effective_dpi_y = dpi_scale

            checkbox_phys_x = viewport_phys_left + checkbox_css_x * effective_dpi_x
            checkbox_phys_y = viewport_phys_top + checkbox_css_y * effective_dpi_y

            # 12. 边界检查
            if checkbox_phys_x < 0 or checkbox_phys_y < 0:
                logger.warning(f"[Native] 截图差异: checkbox 坐标为负 ({checkbox_phys_x:.1f},{checkbox_phys_y:.1f})")
                return None
            if checkbox_phys_x > screen_w or checkbox_phys_y > screen_h:
                logger.warning(
                    f"[Native] 截图差异: checkbox 坐标超出屏幕 "
                    f"({checkbox_phys_x:.1f},{checkbox_phys_y:.1f}) > "
                    f"({screen_w}x{screen_h})"
                )
                return None

            logger.info(
                f"[Native] ✓ 截图差异校准成功! "
                f"viewport origin=({viewport_phys_left:.1f},{viewport_phys_top:.1f}), "
                f"DPI=({effective_dpi_x:.3f},{effective_dpi_y:.3f}), "
                f"checkbox CSS=({checkbox_css_x:.1f},{checkbox_css_y:.1f}) → "
                f"Phys=({checkbox_phys_x:.1f},{checkbox_phys_y:.1f})"
            )

            return (checkbox_phys_x, checkbox_phys_y)

        except Exception as e:
            logger.warning(f"[Native] 截图差异校准异常: {e}")
            import traceback

            logger.warning(f"[Native] {traceback.format_exc()}")
            await self._remove_calibration_markers()
            return None

    async def _remove_calibration_markers(self):
        """清除校准标记 (安全调用, 兼容所有标记 ID)"""
        try:
            await self.page.evaluate("""
                document.getElementById('__cal_overlay')?.remove();
                document.getElementById('__cal_marker')?.remove();
                document.getElementById('__cal_marker_1')?.remove();
                document.getElementById('__cal_marker_2')?.remove();
            """)
        except Exception:
            pass

    # ========================================================
    # OS 级点击 (PyAutoGUI)
    # ========================================================
    @staticmethod
    def _get_process_name(pid: int) -> str:
        """
        通过 PID 获取进程可执行文件名 (小写, 如 'chrome.exe')

        用于区分 Chrome 浏览器和 Electron 应用 (如 TRAE IDE):
        两者窗口类名都是 Chrome_WidgetWin_1, 但进程名不同
        (chrome.exe vs trae.exe / electron.exe)
        """
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_uint32(1024)
                if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return os.path.basename(buf.value).lower()
                return ""
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return ""

    def _find_main_chrome_window(self):
        """
        使用 win32gui EnumWindows 查找主 Chrome 窗口

        关键修复: TRAE IDE 是 Electron 应用, 窗口类名也是 Chrome_WidgetWin_1.
        旧代码仅按面积排序, 会误选 TRAE IDE 窗口 (面积可能比 Chrome 更大).
        新代码通过进程名过滤, 只选择 chrome.exe 的窗口.

        策略:
          1. 枚举所有 Chrome_WidgetWin_1 窗口
          2. 通过 PID 获取进程名, 过滤掉非 chrome.exe 的窗口 (排除 Electron 应用)
          3. 在 chrome.exe 窗口中选择面积最大且可见的作为主窗口
        """
        if platform.system() != "Windows":
            return None

        import win32gui
        import win32process

        candidates = []
        skipped_non_chrome = []

        def _enum_callback(hwnd, _):
            if not win32gui.IsWindow(hwnd):
                return
            class_name = win32gui.GetClassName(hwnd)
            if class_name != "Chrome_WidgetWin_1":
                return
            if not win32gui.IsWindowVisible(hwnd):
                return

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            if width <= 10 or height <= 10:
                return

            # 通过进程名过滤: 只接受 chrome.exe, 排除 Electron 应用
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = self._get_process_name(pid)

            if proc_name != "chrome.exe":
                skipped_non_chrome.append((hwnd, proc_name, pid, width * height))
                return

            candidates.append((hwnd, left, top, right, bottom, width * height, pid))

        win32gui.EnumWindows(_enum_callback, None)

        if skipped_non_chrome:
            logger.info(
                f"[Native] 过滤掉 {len(skipped_non_chrome)} 个非 chrome.exe 窗口: "
                + ", ".join(f"{n}(pid={p})" for _, n, p, _ in skipped_non_chrome[:3])
            )

        if not candidates:
            logger.warning("[Native] EnumWindows 未找到 chrome.exe 主窗口")
            logger.warning("[Native] 所有 Chrome_WidgetWin_1 窗口均非 chrome.exe (可能被 Electron 应用占据)")
            return None

        candidates.sort(key=lambda c: c[5], reverse=True)
        best = candidates[0]
        hwnd, left, top, right, bottom, area, pid = best
        logger.info(
            f"[Native] EnumWindows 找到 {len(candidates)} 个 chrome.exe 窗口, "
            f"主窗口 hwnd={hwnd} pid={pid}: ({left}, {top}, {right}, {bottom}), 面积={area}"
        )
        return hwnd

    def _force_foreground(self, hwnd) -> bool:
        """
        强制将窗口切换到前台, 绕过 Windows 前台锁机制

        Windows 前台锁阻止后台进程调用 SetForegroundWindow 窃取焦点.
        当 Python 脚本运行在 TRAE IDE 内时, TRAE IDE 是前台进程,
        直接 SetForegroundWindow 会被系统拒绝 (前台锁).

        解决方案: AttachThreadInput
          1. 获取前台窗口线程和目标窗口线程
          2. 将两个线程的输入队列临时关联 (AttachThreadInput)
          3. 在关联状态下调用 SetForegroundWindow (此时有权限)
          4. 解除关联

        附加技巧: 发送 Alt 键事件重置前台锁超时计时器
        """
        try:
            import ctypes

            import win32con
            import win32gui
            import win32process

            # 获取前台窗口及其线程
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd == hwnd:
                return True

            foreground_thread_id, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)
            target_thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)

            # 方法1: AttachThreadInput (最可靠)
            attached = False
            if foreground_thread_id != target_thread_id:
                try:
                    ctypes.windll.user32.AttachThreadInput(target_thread_id, foreground_thread_id, True)
                    attached = True
                except Exception:
                    pass

            # 发送 Alt 键重置前台锁 (模拟用户输入)
            try:
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)  # Alt up
            except Exception:
                pass

            # 尝试多种方式切换前台
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception:
                    pass

            # 解除 AttachThreadInput
            if attached:
                try:
                    ctypes.windll.user32.AttachThreadInput(target_thread_id, foreground_thread_id, False)
                except Exception:
                    pass

            time.sleep(0.5)

            # 验证是否成功切换到前台
            new_foreground = win32gui.GetForegroundWindow()
            if new_foreground == hwnd:
                return True

            # 方法2: 如果 AttachThreadInput 失败, 尝试 SystemParametersInfo 修改前台锁超时
            try:
                # 临时将前台锁超时设为 0
                old_timeout = ctypes.c_uint32()
                ctypes.windll.user32.SystemParametersInfoW(
                    0x2000,
                    0,
                    ctypes.byref(old_timeout),
                    0,  # SPI_GETFOREGROUNDLOCKTIMEOUT
                )
                ctypes.windll.user32.SystemParametersInfoW(
                    0x2001,
                    0,
                    0,
                    0,  # SPI_SETFOREGROUNDLOCKTIMEOUT, 0
                )
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
                # 恢复原超时
                ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, ctypes.byref(old_timeout), 0)
            except Exception:
                pass

            time.sleep(0.3)
            new_foreground = win32gui.GetForegroundWindow()
            return new_foreground == hwnd

        except Exception as e:
            logger.warning(f"[Native] _force_foreground 异常: {e}")
            return False

    def _verify_chrome_foreground(self) -> bool:
        """
        使用 win32gui 验证 Chrome 窗口在前台且可见
        使用 AttachThreadInput 绕过 Windows 前台锁机制

        关键: Python 脚本运行在 TRAE IDE 内时, TRAE IDE 是前台进程.
              直接 SetForegroundWindow 会被 Windows 前台锁拒绝,
              导致 pyautogui.screenshot() 捕获到 TRAE IDE 而非 Chrome.
              必须用 AttachThreadInput 强制切换.
        """
        try:
            import win32con
            import win32gui

            hwnd = self._find_main_chrome_window()
            if not hwnd:
                logger.error("[Native] 无法定位 Chrome 主窗口")
                return False

            # 检查窗口是否最小化
            if win32gui.IsIconic(hwnd):
                logger.warning("[Native] Chrome 窗口已最小化, 正在恢复...")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.5)

            # 检查是否已在前台
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd == hwnd:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                logger.info(
                    f"[Native] Chrome 已在前台: ({left}, {top}, {right}, {bottom}), 尺寸={right - left}x{bottom - top}"
                )
                return True

            # Chrome 不在前台, 使用 AttachThreadInput 强制切换
            logger.info(
                f"[Native] Chrome 不在前台 (当前前台 hwnd={foreground_hwnd}), 使用 AttachThreadInput 强制切换..."
            )

            # 最多重试 3 次
            for retry in range(3):
                if self._force_foreground(hwnd):
                    logger.info(f"[Native] ✓ Chrome 已切换到前台 (第 {retry + 1} 次尝试)")
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    logger.info(
                        f"[Native] Chrome 主窗口矩形: ({left}, {top}, {right}, {bottom}), "
                        f"尺寸={right - left}x{bottom - top}"
                    )
                    return True
                logger.warning(f"[Native] 前台切换失败 (第 {retry + 1} 次), 重试...")
                time.sleep(1)

            logger.error("[Native] 3 次尝试均无法将 Chrome 切换到前台")
            logger.error("[Native] pyautogui 截图/点击将无法命中 Chrome, 请手动切换到 Chrome 窗口")
            return False

        except ImportError:
            logger.warning("[Native] win32gui 不可用 (非 Windows?), 跳过窗口验证")
            return True
        except Exception as e:
            logger.error(f"[Native] Chrome 窗口验证失败: {e}")
            return False

    async def _ensure_window_ready_for_click(self) -> bool:
        """
        确保 Chrome 窗口在 OS 和 DOM 两个层面都处于可点击状态

        问题: 页面导航/重载后, Chrome DOM 可能报告窗口最小化 (screenX=-21333),
        即使 win32gui 显示窗口已最大化. 这会导致:
        1. 坐标计算完全错误 (screenX=-21333 → 负坐标)
        2. 全屏覆盖校准失败 (覆盖层不可见)

        解决:
        1. win32gui 恢复+最大化 OS 窗口
        2. CDP 强制更新 Chrome 内部窗口状态
        3. 等待 DOM window.screenX 更新为有效值
        4. 验证 screenX/screenY 为有效值后返回
        """
        # Step 1: OS 级窗口管理
        if not self._verify_chrome_foreground():
            return False

        # Step 2: 检查 Chrome DOM 窗口状态
        try:
            screen_x = await self.page.evaluate("window.screenX")
            if screen_x is not None and screen_x > -1000:
                # 窗口状态正常, 无需修复
                return True

            logger.warning(f"[Native] Chrome DOM 窗口状态异常 (screenX={screen_x}), 重新同步窗口状态...")
        except Exception as e:
            logger.warning(f"[Native] 检查 DOM 窗口状态失败: {e}")

        # Step 3: CDP 强制最大化 + 等待 DOM 更新
        self._ensure_window_maximized()
        await self._ensure_window_maximized_async()
        updated = await self._wait_for_dom_window_update(timeout=10)

        if not updated:
            logger.error("[Native] DOM 窗口状态未能恢复, 坐标计算将不准确")
            return False

        # Step 4: 最终验证
        try:
            screen_x = await self.page.evaluate("window.screenX")
            screen_y = await self.page.evaluate("window.screenY")
            if screen_x is not None and screen_x > -1000:
                logger.info(f"[Native] ✓ 窗口状态已恢复: screenX={screen_x}, screenY={screen_y}")
                return True
            else:
                logger.error(f"[Native] 窗口状态恢复失败: screenX={screen_x}")
                return False
        except Exception as e:
            logger.error(f"[Native] 最终窗口状态验证失败: {e}")
            return False

    def _os_level_click(self, target_x: float, target_y: float):
        """
        使用 PyAutoGUI 模拟真人鼠标移动 + 点击
        关键: 浏览器收到的是 isTrusted=true 的真实 OS 事件 (Win32 SendInput)

        与 Playwright page.mouse.click() 的区别:
          - Playwright 点击: isTrusted=false (合成事件, reCAPTCHA 可检测)
          - PyAutoGUI 点击: isTrusted=true (OS 级真实事件, 无法区分人工/自动)
        """
        import pyautogui

        # 禁用 fail-safe: 贝塞尔曲线控制点有随机偏移, 可能在屏幕边缘触发误报
        # 坐标已在 _get_checkbox_screen_position 中验证, 无需 fail-safe 保护
        pyautogui.FAILSAFE = False

        # 1. 获取当前鼠标位置作为起点
        start_x, start_y = pyautogui.position()
        logger.info(f"[Native] 鼠标起点: ({start_x}, {start_y}), 目标: ({target_x:.0f}, {target_y:.0f})")

        # 2. 贝塞尔曲线移动 (模拟人类鼠标轨迹)
        path = self._generate_bezier_path(start_x, start_y, target_x, target_y)
        total = len(path)

        for i, (x, y) in enumerate(path):
            if i == 0:
                continue

            # 加减速: 前30%慢(加速), 中40%快(匀速), 后30%慢(减速)
            progress = i / total
            if progress < 0.3:
                delay = random.uniform(0.015, 0.035)
            elif progress < 0.7:
                delay = random.uniform(0.008, 0.018)
            else:
                delay = random.uniform(0.02, 0.045)

            pyautogui.moveTo(int(x), int(y), duration=0)
            time.sleep(delay)

        # 3. 微调到精确位置 (含人类手抖偏移)
        time.sleep(random.uniform(0.1, 0.3))
        final_x = target_x + random.uniform(-2, 2)
        final_y = target_y + random.uniform(-2, 2)
        pyautogui.moveTo(int(final_x), int(final_y), duration=0)

        # 4. 点击前犹豫 (人类反应时间)
        time.sleep(random.uniform(0.15, 0.5))

        # 5. 点击
        pyautogui.click()
        logger.info(f"[Native] OS 级点击完成: ({final_x:.1f}, {final_y:.1f})")

    @staticmethod
    def _generate_bezier_path(start_x, start_y, end_x, end_y, steps=None):
        """
        生成贝塞尔曲线路径 (含控制点随机偏移 + 微小抖动)
        模拟人类鼠标: 开始慢→中间快→结束慢
        """
        dist = math.hypot(end_x - start_x, end_y - start_y)
        if steps is None:
            steps = max(15, min(50, int(dist / 15)))

        offset = min(dist * 0.3, 100)
        ctrl1_x = start_x + (end_x - start_x) * 0.3 + random.uniform(-offset, offset)
        ctrl1_y = start_y + (end_y - start_y) * 0.3 + random.uniform(-offset, offset)
        ctrl2_x = start_x + (end_x - start_x) * 0.7 + random.uniform(-offset, offset)
        ctrl2_y = start_y + (end_y - start_y) * 0.7 + random.uniform(-offset, offset)

        path = []
        for i in range(steps + 1):
            t = i / steps
            # ease-in-out 缓动
            t_eased = t * t * (3 - 2 * t)
            u = 1 - t_eased
            x = u**3 * start_x + 3 * u**2 * t_eased * ctrl1_x + 3 * u * t_eased**2 * ctrl2_x + t_eased**3 * end_x
            y = u**3 * start_y + 3 * u**2 * t_eased * ctrl1_y + 3 * u * t_eased**2 * ctrl2_y + t_eased**3 * end_y
            # 微小抖动 (人手不可能完全稳定)
            x += random.uniform(-1.5, 1.5)
            y += random.uniform(-1.5, 1.5)
            path.append((x, y))
        return path

    # ========================================================
    # 结果检测 (Playwright DOM 检测, 替代 OpenCV)
    # ========================================================
    async def _detect_result_by_dom(self, wait_time: int = 8) -> str:
        """
        使用 Playwright DOM 检测 reCAPTCHA 结果 (无需 OpenCV)
        返回: "passed" | "challenge" | "timeout"

        检测方式:
          - "passed": anchor iframe 内出现 .recaptcha-checkbox-checked
          - "challenge": bframe iframe 内出现可见的挑战内容
            (.rc-imageselect-challenge 或 .rc-imageselect-instructions 可见)
          - "timeout": 超时未检测到任何变化

        关键修复: bframe iframe 在 reCAPTCHA 加载时就存在于 DOM 中 (隐藏状态),
                  不能仅凭 bframe 存在就判定为挑战。
                  必须检测 bframe 内的实际挑战元素是否可见。
        """
        for i in range(wait_time):
            await asyncio.sleep(1)

            # 检测1: checkbox 是否已勾选 (直接通过)
            try:
                anchor_frame = await self._get_recaptcha_frame("anchor")
                if anchor_frame:
                    checked = anchor_frame.locator(".recaptcha-checkbox-checked")
                    if await checked.count() > 0:
                        logger.info(f"[Native] checkbox 已勾选! (第 {i + 1}s)")
                        await self._take_screenshot(f"native_passed_{i + 1}")
                        return "passed"
            except Exception as e:
                logger.debug(f"[Native] 检测 checkbox 状态异常: {e}")

            # 检测2: 是否出现可见的图像挑战弹窗
            # 关键: bframe iframe 在页面加载时就存在 (hidden), 不能仅凭存在判定挑战
            # 必须检测 bframe 内的实际挑战元素 (.rc-imageselect-challenge) 是否可见
            try:
                bframe = await self._get_recaptcha_frame("bframe")
                if bframe:
                    # 检测 bframe 内的挑战内容元素
                    challenge_selectors = [
                        ".rc-imageselect-challenge",  # 图像挑战容器
                        ".rc-imageselect-instructions",  # 提示文本
                        ".rc-imageselect-target",  # 图像网格
                    ]
                    challenge_visible = False
                    for sel in challenge_selectors:
                        try:
                            el = bframe.locator(sel).first
                            if await el.count() > 0:
                                if await el.is_visible():
                                    logger.info(f"[Native] 检测到可见挑战元素 '{sel}' (第 {i + 1}s)")
                                    challenge_visible = True
                                    break
                        except Exception:
                            continue

                    if challenge_visible:
                        await self._take_screenshot(f"native_challenge_{i + 1}")
                        return "challenge"
            except Exception as e:
                logger.debug(f"[Native] 检测 bframe 异常: {e}")

            # 检测3: checkbox 是否显示错误/过期状态 (可能需要重试)
            try:
                anchor_frame = await self._get_recaptcha_frame("anchor")
                if anchor_frame:
                    expired = anchor_frame.locator(".recaptcha-checkbox-expired")
                    if await expired.count() > 0:
                        logger.warning(f"[Native] checkbox 显示过期状态 (第 {i + 1}s)")
                        # 过期不是挑战, 是超时, 继续等待或重试
            except Exception:
                pass

            # 检测4: checkbox 是否处于 spinner 加载状态 (reCAPTCHA 正在验证)
            # spinner 状态表示点击已生效, reCAPTCHA 正在处理, 应继续等待
            try:
                anchor_frame = await self._get_recaptcha_frame("anchor")
                if anchor_frame:
                    spinner = anchor_frame.locator(".recaptcha-checkbox-spinner")
                    if await spinner.count() > 0:
                        logger.info(f"[Native] checkbox 处于加载状态 (spinner), reCAPTCHA 正在验证 (第 {i + 1}s)")
                    elif i == 0:
                        logger.info(f"[Native] checkbox 未显示 spinner, 可能点击未生效 (第 {i + 1}s)")
            except Exception:
                pass

            # 每 5 秒输出一次详细日志 (避免 30 秒等待期间日志过于稀疏)
            if (i + 1) % 5 == 0:
                logger.info(f"[Native] 等待结果... ({i + 1}/{wait_time}s)")
            else:
                logger.debug(f"[Native] 等待结果... ({i + 1}/{wait_time}s)")

        logger.warning(f"[Native] 结果检测超时 ({wait_time}s)")
        await self._take_screenshot("native_timeout")
        return "timeout"

    # ========================================================
    # Fallback: 图像识别
    # ========================================================
    async def _fallback_to_image(self) -> str | None:
        """
        Fallback: 使用 ImageRuntime 解决图像挑战 (挑战弹窗已显示)
        ImageRuntime 使用 CLS/SEG/CLIP 三引擎

        由于使用 launch_persistent_context, 浏览器会话仍可用
        只需共享 page/context 给 ImageRuntime

        关键: 跳过 ImageRuntime 的 checkbox 点击
        - 挑战弹窗已显示, checkbox 无需再点击
        - ImageRuntime._click_checkbox() 使用 Playwright force=True 点击 (isTrusted=false)
        - 在挑战已显示时再次点击 checkbox 会关闭弹窗, 导致 Fallback 失败

        失败处理:
        - 图像识别失败时 raise RuntimeError, 不返回 None
        - 返回 None 会被上层误判为 "浏览器内通过" (false positive)
        - raise 后由 solve_recaptcha 的调用方 (E2E测试) 捕获并正确记录失败
        """
        if not getattr(config, "NATIVE_FALLBACK_TO_IMAGE", True):
            logger.info("[Native] NATIVE_FALLBACK_TO_IMAGE=False, 跳过 Fallback")
            raise RuntimeError("NATIVE_FALLBACK_TO_IMAGE=False, 图像挑战无法求解")

        logger.info("[Native] Fallback 到图像识别方案...")

        try:
            from runtimes.runtime_image import ImageRuntime

            image_runtime = ImageRuntime()
            # 共享浏览器会话 (launch_persistent_context 模式)
            image_runtime.playwright = self.playwright
            image_runtime.browser = None  # persistent context 模式无 browser 对象
            image_runtime.context = self.context
            image_runtime.page = self.page

            # 关键: 挑战弹窗已显示, 跳过 checkbox 点击
            # ImageRuntime._attempt_solve() 第一步会调用 _click_checkbox(),
            # 使用 Playwright force=True 点击 (isTrusted=false),
            # 在挑战已显示时再次点击 checkbox 会关闭 bframe 弹窗, 导致 Fallback 失败
            async def _skip_checkbox():
                logger.info("[Native] Fallback: 跳过 checkbox 点击 (挑战弹窗已显示)")
                return True

            image_runtime._click_checkbox = _skip_checkbox

            sitekey = await self.extract_sitekey()
            result = await image_runtime.solve_recaptcha(sitekey, self.page.url)

            # ImageRuntime.solve_recaptcha 返回 None 表示浏览器内通过 (token 已自动提交)
            # 返回 token 字符串表示需要注入
            # 如果 ImageRuntime 内部所有尝试都失败, 它会 raise RuntimeError
            logger.info(
                f"[Native] 图像识别 Fallback 完成, result={'None(浏览器内通过)' if result is None else 'token'}"
            )
            return result
        except RuntimeError:
            # 图像识别失败的 RuntimeError 直接向上传播
            raise
        except Exception as e:
            logger.error(f"[Native] 图像识别 Fallback 异常: {e}", exc_info=True)
            raise RuntimeError(f"图像识别 Fallback 异常: {e}") from e

    # ========================================================
    # 核心: 求解 reCAPTCHA (两阶段编排)
    # ========================================================
    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        零 CDP 痕迹绕过: 两阶段流程

        阶段1: 获取 checkbox 屏幕坐标 (Playwright bounding_box)
        阶段2: PyAutoGUI OS 级点击 → Playwright DOM 检测结果

        无需 CDP 断连: patchright 从源头不发 Runtime.enable
        CDP 连接存在但不泄漏检测向量
        """
        logger.info("[Native] 开始零 CDP 痕迹求解 reCAPTCHA v2...")

        max_attempts = config.RECAPTCHA_MAX_RETRIES

        for attempt in range(1, max_attempts + 1):
            logger.info(f"[Native] 第 {attempt}/{max_attempts} 次尝试...")

            # ===== 阶段 1: 获取 checkbox 屏幕坐标 =====
            try:
                # 先检查是否已通过
                anchor_frame = await self._get_recaptcha_frame("anchor")
                if anchor_frame:
                    checked = anchor_frame.locator(".recaptcha-checkbox-checked")
                    if await checked.count() > 0:
                        logger.info("[Native] reCAPTCHA 已自动通过 (无需点击)!")
                        return None

                # 检查是否已出现图像挑战 (前一次点击可能延迟触发了挑战)
                bframe = await self._get_recaptcha_frame("bframe")
                if bframe:
                    try:
                        challenge_el = bframe.locator(".rc-imageselect-challenge").first
                        if await challenge_el.count() > 0 and await challenge_el.is_visible():
                            logger.warning("[Native] 检测到图像挑战已出现 (前次点击延迟触发), 进入 Fallback")
                            self._challenge_detected = True
                            fallback_result = await self._fallback_to_image()
                            return fallback_result
                    except Exception:
                        pass

                # 检查 checkbox 是否处于 spinner 状态 (仅重试时检查, 首次尝试直接点击)
                # 首次尝试时 reCAPTCHA 可能仍在初始加载, spinner 是正常的, 不应等待
                if attempt > 1 and anchor_frame:
                    try:
                        spinner = anchor_frame.locator(".recaptcha-checkbox-spinner")
                        if await spinner.count() > 0:
                            logger.info(
                                "[Native] checkbox 处于 spinner 状态 (前次点击正在验证), 等待完成 (最多 30s)..."
                            )
                            spinner_result = await self._detect_result_by_dom(30)
                            if spinner_result == "passed":
                                logger.info("[Native] reCAPTCHA spinner 验证通过!")
                                return None
                            if spinner_result == "challenge":
                                logger.warning("[Native] spinner 后触发了图像挑战, 进入 Fallback")
                                self._challenge_detected = True
                                fallback_result = await self._fallback_to_image()
                                return fallback_result
                            # spinner 超时: 可能是 reCAPTCHA 卡在加载状态, 尝试刷新
                            logger.warning("[Native] spinner 等待超时, 尝试刷新 reCAPTCHA...")
                            try:
                                # 点击 checkbox 区域可能触发重新验证
                                await self.page.evaluate("document.getElementById('recaptcha-anchor').click()")
                            except Exception:
                                pass
                            await asyncio.sleep(3)
                            continue
                    except Exception:
                        pass

                # 确保 Chrome 窗口在 OS 和 DOM 两个层面都处于可点击状态
                # (必须在校准前, 否则 screenX=-21333 导致覆盖层不可见)
                if not await self._ensure_window_ready_for_click():
                    logger.error("[Native] Chrome 窗口状态异常, 无法安全点击")
                    await asyncio.sleep(2)
                    continue

                # 优先使用 Win32 精确校准 (OS 级客户端区域, 无需截图)
                win32_pos = await self._get_checkbox_position_by_win32()
                if win32_pos:
                    screen_x, screen_y = win32_pos
                    logger.info(f"[Native] 使用 Win32 校准坐标: ({screen_x:.1f}, {screen_y:.1f})")
                else:
                    # Fallback 1: 截图差异校准 (DPI 无关, 经验性映射)
                    logger.warning("[Native] Win32 校准失败, 回退到截图差异法...")
                    marker_pos = await self._get_checkbox_position_by_markers()
                    if marker_pos:
                        screen_x, screen_y = marker_pos
                        logger.info(f"[Native] 使用截图差异校准坐标: ({screen_x:.1f}, {screen_y:.1f})")
                    else:
                        # Fallback 2: 坐标计算 (v3: DPI + screenX)
                        logger.warning("[Native] 截图差异校准失败, 回退到坐标计算...")
                        screen_x, screen_y = await self._get_checkbox_screen_position()
                        logger.info(f"[Native] 使用坐标计算: ({screen_x:.1f}, {screen_y:.1f})")
                self._checkbox_screen_pos = (screen_x, screen_y)
            except Exception as e:
                logger.error(f"[Native] 获取 checkbox 坐标失败: {e}")
                await asyncio.sleep(2)
                continue

            # ===== 阶段 2: OS 级点击 + 螺旋搜索 + 结果检测 =====

            # 关键: 点击前再次验证 Chrome 在前台
            # (校准过程中 TRAE IDE 可能抢回前台, 导致点击落在错误窗口)
            if not self._verify_chrome_foreground():
                logger.error("[Native] 点击前 Chrome 不在前台, 跳过本次尝试")
                await asyncio.sleep(2)
                continue

            # 螺旋搜索: 先尝试初始坐标, 若未命中则螺旋扩展搜索
            # 搜索半径 (物理像素): 30px 步长, 覆盖 ±150px 范围
            # 在 150% DPI 下 30px ≈ 20 CSS px, checkbox 约 28 CSS px
            # 大范围覆盖确保即使坐标计算有 ~120px 偏差也能命中
            spiral_offsets = [
                (0, 0),  # 初始坐标
                (30, 0),
                (30, 30),
                (0, 30),
                (-30, 30),
                (-30, 0),
                (-30, -30),
                (0, -30),
                (30, -30),
                (60, 0),
                (60, 60),
                (0, 60),
                (-60, 60),
                (-60, 0),
                (-60, -60),
                (0, -60),
                (60, -60),
                (90, 0),
                (90, 90),
                (0, 90),
                (-90, 90),
                (-90, 0),
                (-90, -90),
                (0, -90),
                (90, -90),
                (120, 0),
                (120, 120),
                (0, 120),
                (-120, 120),
                (-120, 0),
                (-120, -120),
                (0, -120),
                (120, -120),
                (150, 0),
                (150, 150),
                (0, 150),
                (-150, 150),
                (-150, 0),
                (-150, -150),
                (0, -150),
                (150, -150),
            ]

            # 如果前一次尝试找到了命中偏移, 优先尝试该偏移
            if self._spiral_hit_offset:
                saved = self._spiral_hit_offset
                spiral_offsets = [saved] + [o for o in spiral_offsets if o != saved]
                logger.info(f"[Native] 使用上次命中偏移优先: ({saved[0]},{saved[1]})")

            click_hit = False
            for i, (dx, dy) in enumerate(spiral_offsets):
                target_x = screen_x + dx
                target_y = screen_y + dy

                try:
                    # 注入点击监听器 (每次点击前重新注入, once: true)
                    await self._inject_click_listener()

                    # OS 级点击
                    self._os_level_click(target_x, target_y)

                    # 验证点击是否命中
                    await asyncio.sleep(0.4)
                    click_result = await self._check_click_received()

                    if click_result and click_result.get("received"):
                        click_hit = True
                        logger.info(
                            f"[Native] ✓ 螺旋搜索第 {i + 1} 个位置命中! "
                            f"偏移=({dx},{dy}), 坐标=({target_x:.1f},{target_y:.1f})"
                        )
                        # 记录成功偏移, 供后续尝试直接使用
                        self._spiral_hit_offset = (dx, dy)
                        break
                    else:
                        if i == 0:
                            logger.warning("[Native] 初始坐标未命中, 启动螺旋搜索...")
                        elif i % 4 == 0:
                            logger.info(f"[Native] 螺旋搜索 {i + 1}/{len(spiral_offsets)}: 偏移=({dx},{dy}) 未命中")
                except Exception as e:
                    if "Target page, context or browser has been closed" in str(e):
                        logger.error(f"[Native] 浏览器已关闭, 终止螺旋搜索: {e}")
                        raise RuntimeError(f"浏览器在螺旋搜索期间关闭: {e}") from e
                    logger.warning(f"[Native] 螺旋搜索位置 {i + 1} 异常: {e}")
                    continue

            if not click_hit:
                logger.warning(
                    f"[Native] 螺旋搜索 {len(spiral_offsets)} 个位置均未命中, "
                    f"继续等待结果 (可能点击到 checkbox 附近仍有效)"
                )

            # 结果检测 (Playwright DOM)
            wait_time = getattr(config, "NATIVE_CLICK_RESULT_WAIT", 8)
            result = await self._detect_result_by_dom(wait_time)

            if result == "passed":
                logger.info("[Native] reCAPTCHA checkbox 验证通过! (未触发图像挑战)")
                await self._take_screenshot("native_success")
                return None

            if result == "challenge":
                logger.warning("[Native] 触发了图像挑战, 进入 Fallback 流程")
                self._challenge_detected = True
                fallback_result = await self._fallback_to_image()
                return fallback_result

            # result == "timeout"
            logger.warning(f"[Native] 第 {attempt} 次尝试超时, 重试...")
            await asyncio.sleep(2)

        logger.error(f"[Native] {max_attempts} 次尝试均未通过")
        if self._challenge_detected:
            logger.error("[Native] 图像挑战被触发, 可能需要配合代理 IP 或其他方案")
        # 不返回 None (会被误判为"浏览器内通过"), raise 让调用方正确捕获失败
        raise RuntimeError(
            f"Native runtime {max_attempts} 次尝试均未通过 reCAPTCHA"
            + (" (图像挑战被触发)" if self._challenge_detected else " (全部超时)")
        )

    # ========================================================
    # 清理 (覆盖)
    # ========================================================
    async def close(self):
        """
        关闭: persistent context + 清理临时 profile
        注意: launch_persistent_context 模式下, 关闭 context 会自动关闭 Chrome
        """
        # 关闭 persistent context (会自动关闭 Chrome 进程)
        if self.context:
            try:
                await self.context.close()
                logger.info("[Native] persistent context 已关闭")
            except Exception as e:
                logger.warning(f"[Native] context.close() 异常: {e}")

        if self.playwright:
            try:
                await self.playwright.stop()
                logger.info("[Native] playwright 已停止")
            except Exception as e:
                logger.warning(f"[Native] playwright.stop() 异常: {e}")

        # 清理临时 profile (绝不删除真实 profile!)
        if self._user_data_dir and not self._using_real_profile:
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
                logger.info("[Native] 临时 profile 已清理")
            except Exception:
                pass
        elif self._using_real_profile:
            logger.info("[Native] 使用的是真实 profile, 跳过清理")
