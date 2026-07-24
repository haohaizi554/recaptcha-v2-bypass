"""
reCAPTCHA v2 自动化绕过 - 基础运行时
=====================================
封装所有共享逻辑: 浏览器初始化、页面导航、表单提交、结果验证
各求解方案继承此类, 仅需实现 solve_recaptcha() 方法

架构:
  BaseBypassRuntime (本文件)
    ├── AudioRuntime      (runtimes/runtime_audio.py)
    ├── APIRuntime        (runtimes/runtime_api.py)
    ├── ImageRuntime      (runtimes/runtime_image.py)
    ├── CookieRuntime     (runtimes/runtime_cookie.py)
    └── ExtensionRuntime  (runtimes/runtime_extension.py)
"""

import asyncio
import json
import logging
import os

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

import config

logger = logging.getLogger("reCAPTCHA-Bypass")


class BaseBypassRuntime:
    """
    reCAPTCHA v2 绕过基础运行时
    所有求解方案共享的浏览器管理、导航、表单提交、结果验证逻辑

    子类只需实现:
        solve_recaptcha(sitekey, page_url) -> str | None
        返回 token (API 方案) 或 None (浏览器内方案)

    可选覆盖:
        _get_browser_args() -> list     (添加浏览器启动参数, 如扩展加载)
        _post_context_init()            (context 创建后的钩子, 如设置 cookie)
        _post_navigate_hook()           (导航完成后的钩子, 如等待扩展就绪)
    """

    method_name = "base"
    method_desc = "基础运行时 (不应直接使用)"

    # 登录链接选择器 (按优先级排序)
    # 覆盖: profileWidget (新版SAP UI), loggedInStatus (旧版), 文本匹配 (多语言)
    SIGN_IN_SELECTORS = [
        # 最高优先级: 精确匹配 onclick 属性 (避免误匹配 profileWidget 内非登录链接)
        ".profileWidget a[onclick*='handleViewProfileAction']",
        # 通用 profileWidget 选择器 (兜底)
        ".profileWidget a",
        "a.loggedInStatus",  # 旧版 class
        "a:has-text('Sign In')",  # 英文
        "a:has-text('Login')",  # 英文变体
        "a:has-text('登入')",  # 繁体中文
        "a:has-text('登錄')",  # 繁体中文变体
        "a:has-text('登录')",  # 简体中文
        "button:has-text('Sign In')",  # 按钮形式
        "button:has-text('Login')",  # 按钮形式
    ]

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.screenshot_dir = config.SCREENSHOT_DIR
        self._keep_browser_open = True
        if config.SAVE_SCREENSHOTS:
            os.makedirs(self.screenshot_dir, exist_ok=True)

    # ========================================================
    # 可覆盖的钩子
    # ========================================================
    def _get_browser_args(self) -> list:
        """返回浏览器启动参数, 子类可覆盖以添加扩展等"""
        return [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-popup-blocking",
            "--window-size=1920,1080",
        ]

    def _get_context_options(self) -> dict:
        """返回浏览器 context 选项, 子类可覆盖"""
        return {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

    async def _post_context_init(self):
        """context 创建后的钩子, 子类可覆盖 (如设置 cookie)"""
        pass

    async def _post_navigate_hook(self):
        """导航完成后的钩子, 子类可覆盖 (如等待扩展就绪)"""
        pass

    # ========================================================
    # 浏览器初始化
    # ========================================================
    async def init_browser(self):
        """初始化 Playwright 浏览器 (stealth 模式, 规避反爬检测)"""
        logger.info(f"[{self.method_name}] 初始化 Playwright 浏览器 (stealth 模式)...")

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=config.BROWSER_HEADLESS,
            channel="chrome",
            args=self._get_browser_args(),
        )

        self.context = await self.browser.new_context(**self._get_context_options())

        # 使用 playwright-stealth v2 API 注入反检测脚本
        stealth = Stealth()
        await stealth.apply_stealth_async(self.context)

        # 子类钩子: context 创建后 (设置 cookie 等)
        await self._post_context_init()

        self.page = await self.context.new_page()
        self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)

        logger.info(f"[{self.method_name}] 浏览器初始化完成 (stealth 模式)")

    # ========================================================
    # 页面导航
    # ========================================================
    async def navigate_to_target(self):
        """
        导航到 SuccessFactors 登录页面
        策略: 前 3 次通过 Atos 源网站跳转, 后 3 次直接访问 URL
        """
        max_retries = config.NAV_MAX_RETRIES
        atos_retries = max_retries // 2

        for attempt in range(1, max_retries + 1):
            logger.info(f"[{self.method_name}] 导航尝试 {attempt}/{max_retries}")

            try:
                if attempt <= atos_retries:
                    success = await self._navigate_via_atos()
                else:
                    if not config.NAV_DIRECT_URL_FALLBACK:
                        continue
                    logger.info(f"切换到直接 URL 访问策略: {config.TARGET_URL}")
                    success = await self._navigate_direct()

                if not success:
                    logger.warning("导航策略未到达登录页, 重试...")
                    continue

                # 防御: 页面可能在导航过程中关闭
                if self.page is None or self.page.is_closed():
                    logger.warning("导航成功但页面已关闭, 重试...")
                    continue

                await self._take_screenshot("01_login_page")
                try:
                    title = await self.page.title()
                    logger.info(f"登录页标题: {title}")
                    logger.info(f"当前 URL: {self.page.url}")
                except Exception as e:
                    logger.warning(f"获取页面信息失败: {e}")

                if await self._wait_for_recaptcha():
                    logger.info("reCAPTCHA 已就绪, 开始求解流程")
                else:
                    logger.warning("reCAPTCHA 未加载, 但继续尝试 (可能使用 API 方案)")

                # 子类钩子: 导航完成后 (等待扩展就绪等)
                await self._post_navigate_hook()
                return

            except Exception as e:
                logger.warning(f"导航尝试 {attempt} 异常: {e}")

        logger.error(f"{max_retries} 次导航尝试均失败")
        raise RuntimeError("无法到达 SuccessFactors 登录页")

    # ========================================================
    # Apply 链接索引持久化 (优先尝试上次成功的链接)
    # ========================================================
    def _load_link_cache(self) -> int | None:
        """读取上次成功的 Apply 链接索引, 失败返回 None"""
        try:
            with open(config.NAV_LINK_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
                idx = data.get("success_link_idx")
                if isinstance(idx, int) and idx >= 0:
                    return idx
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        except Exception as e:
            logger.debug(f"读取链接缓存失败: {e}")
        return None

    def _save_link_cache(self, idx: int):
        """保存成功的 Apply 链接索引"""
        try:
            with open(config.NAV_LINK_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"success_link_idx": idx}, f)
            logger.info(f"已缓存成功链接索引: [{idx}]")
        except Exception as e:
            logger.debug(f"保存链接缓存失败: {e}")

    def _build_link_order(self, link_count: int, hrefs: list[str] | None = None) -> list[int]:
        """
        构建链接尝试顺序 (三级优先):
          1. 上次成功的缓存索引 (最高优先)
          2. href 匹配 NAV_PREFERRED_HREF_PATTERN 的链接 (首次运行无缓存时生效)
          3. 其余链接按原序

        参数:
          link_count: 链接总数
          hrefs: 各链接的 href 列表 (可选, 用于模式匹配)

        例: link_count=3, cached=2 → [2, 0, 1]
            link_count=3, cached=None, hrefs=['/lp/Talent', '/talentcommunity/apply/', '/talentcommunity/apply/']
              → [1, 2, 0]  (匹配模式的优先)
        """
        remaining = list(range(link_count))
        order: list[int] = []

        # 优先级1: 缓存索引
        cached = self._load_link_cache()
        if cached is not None and 0 <= cached < link_count:
            order.append(cached)
            remaining.remove(cached)
            logger.info(f"优先尝试上次成功的链接 [{cached}]")

        # 优先级2: href 匹配模式
        pattern = getattr(config, "NAV_PREFERRED_HREF_PATTERN", "")
        if pattern and hrefs and not order:
            matched = [i for i in remaining if i < len(hrefs) and hrefs[i] and pattern in hrefs[i]]
            if matched:
                order.extend(matched)
                for i in matched:
                    remaining.remove(i)
                logger.info(f"优先尝试 href 匹配 '{pattern}' 的链接: {matched}")

        # 优先级3: 其余按原序
        order.extend(remaining)

        logger.info(f"链接尝试顺序: {order}")
        return order

    async def _navigate_via_atos(self) -> bool:
        """通过 Atos 源网站跳转到登录页
        策略: 优先点击 profileWidget 登录元素, 失败后回退到 Apply now 链接
        """
        logger.info(f"加载源网站: {config.SOURCE_URL}")
        try:
            await self.page.goto(
                config.SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=config.NAV_PAGE_LOAD_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"源页面加载较慢, 继续尝试: {e}")

        # 等待页面 JS 框架完全就绪 (j2w.TC.handleViewProfileAction 可调用)
        logger.info("等待页面 JS 框架就绪...")
        js_ready = False
        for wait_i in range(20):
            try:
                ready = await self.page.evaluate("""() => {
                    return !!(window.j2w && window.j2w.TC && window.j2w.TC.handleViewProfileAction);
                }""")
                if ready:
                    js_ready = True
                    logger.info(f"JS 框架就绪 (j2w.TC.handleViewProfileAction 已绑定, 第 {wait_i + 1}s)")
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        if not js_ready:
            logger.warning("JS 框架未就绪 (j2w.TC 不可用), 继续尝试 (可能使用其他选择器)")

        await self._dismiss_cookie_notice()

        try:
            title = await self.page.title()
            logger.info(f"源页面标题: {title}")
        except Exception:
            logger.warning("无法获取源页面标题")

        # ---- 策略1: 优先点击 profileWidget 登录元素 (源页面上直接有) ----
        # 等待 profileWidget 元素出现 (JS 框架就绪后通常已渲染)
        logger.info("等待 profileWidget 元素渲染...")
        try:
            await self.page.wait_for_selector(
                ".profileWidget, .profileWidget a, a.loggedInStatus, a:has-text('Sign In')",
                timeout=15000,
                state="attached",
            )
            logger.info("profileWidget 元素已在 DOM 中")
        except Exception:
            logger.warning("等待 profileWidget 超时, 页面可能未完全加载")

        logger.info("尝试点击 profileWidget 登录元素...")
        # 记录源页面状态, 用于失败后恢复
        source_pages_before = len(self.context.pages)
        if await self._click_sign_in_link():
            logger.info("profileWidget 登录元素点击成功, 已到达登录页")
            return True
        logger.warning("profileWidget 登录元素未到达登录页, 回退到 Apply now 链接")

        # 恢复源页面状态: 关闭可能打开的新标签页, 回到源页面
        current_pages = self.context.pages
        if len(current_pages) > source_pages_before:
            for p in current_pages[source_pages_before:]:
                try:
                    await p.close()
                except Exception:
                    pass
            self.page = self.context.pages[0]

        # 如果页面已导航走, 重新加载源页面
        try:
            current_url = self.page.url
        except Exception:
            current_url = ""
        if "jobs.atos.net" not in current_url:
            logger.info("页面已导航走, 重新加载源页面...")
            try:
                await self.page.goto(
                    config.SOURCE_URL,
                    wait_until="commit",
                    timeout=config.NAV_PAGE_LOAD_TIMEOUT,
                )
                await asyncio.sleep(3)
                await self._dismiss_cookie_notice()
            except Exception as e:
                logger.warning(f"重新加载源页面失败: {e}")
                return False

        # ---- 策略2: 回退到 Apply now 链接 ----
        apply_locator = self.page.locator("a:has-text('Apply now')")
        link_count = await apply_locator.count()
        logger.info(f"找到 {link_count} 个 Apply now 链接")

        if link_count == 0:
            for selector in [
                "a[href*='successfactors']",
                "a[href*='career']",
                "button:has-text('Apply')",
                "a:has-text('Apply')",
            ]:
                try:
                    loc = self.page.locator(selector)
                    if await loc.count() > 0:
                        apply_locator = loc
                        link_count = await loc.count()
                        logger.info(f"回退选择器 {selector} 找到 {link_count} 个链接")
                        break
                except Exception:
                    continue

        if link_count == 0:
            logger.warning("未找到任何 Apply 链接")
            return False

        # 收集所有链接的 href (用于优先级排序)
        hrefs: list[str] = []
        for i in range(link_count):
            try:
                h = await apply_locator.nth(i).get_attribute("href") or ""
            except Exception:
                h = ""
            hrefs.append(h)
        logger.info(f"链接 href 列表: {hrefs}")

        # 构建尝试顺序: 缓存索引 > href 模式匹配 > 原序
        link_order = self._build_link_order(link_count, hrefs)

        for order_pos, link_idx in enumerate(link_order):
            link = apply_locator.nth(link_idx)
            href = hrefs[link_idx] if link_idx < len(hrefs) else "(无法获取)"
            logger.info(f"尝试 Apply 链接 [{link_idx}/{link_count}] (第{order_pos + 1}次): href={href}")

            pages_before = len(self.context.pages)

            try:
                async with self.context.expect_page(timeout=15000) as new_page_info:
                    # no_wait_after=True: 不等待导航完成, 避免 TargetClosedError
                    await link.click(no_wait_after=True)
                new_page = await new_page_info.value
                # 防御: 新标签页可能已关闭 (重定向/弹窗拦截)
                if new_page.is_closed():
                    logger.warning("新标签页已关闭, 检查当前页面是否已跳转...")
                else:
                    logger.info(f"检测到新标签页: {new_page.url}")
                    self.page = new_page
                    self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)
            except Exception:
                logger.info("未检测到新标签页, 检查当前页面是否已跳转...")

            logger.info("等待跳转到 SuccessFactors 登录页...")
            reached_sf = False
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    current_url = self.page.url
                except Exception:
                    current_url = ""
                if "successfactors" in current_url:
                    logger.info(f"已跳转到: {current_url}")
                    reached_sf = True
                    break

            if reached_sf:
                logger.info("等待 SuccessFactors 登录页完全加载...")
                try:
                    await self.page.wait_for_selector(
                        "#username",
                        timeout=config.NAV_FORM_WAIT_TIMEOUT,
                    )
                    logger.info("登录表单已加载")
                    # 持久化成功索引
                    self._save_link_cache(link_idx)
                    return True
                except Exception:
                    logger.info("未找到 #username, 尝试点击登录链接...")

                # 点击登录链接进入登录页 (支持 profileWidget/登入/Sign In 多语言)
                if await self._click_sign_in_link():
                    # 持久化成功索引
                    self._save_link_cache(link_idx)
                    return True
                logger.warning("所有登录链接选择器均失败")

            logger.warning(f"链接 [{link_idx}] 未到达 SuccessFactors, 尝试下一个链接")

            if len(self.context.pages) > pages_before:
                for p in self.context.pages[pages_before:]:
                    try:
                        await p.close()
                    except Exception:
                        pass
                self.page = self.context.pages[0]

            if order_pos < len(link_order) - 1:
                try:
                    await self.page.goto(
                        config.SOURCE_URL,
                        wait_until="commit",
                        timeout=config.NAV_PAGE_LOAD_TIMEOUT,
                    )
                    await asyncio.sleep(3)
                    await self._dismiss_cookie_notice()
                except Exception as e:
                    logger.warning(f"重新加载源页面失败: {e}")
                    return False

        logger.warning("所有 Apply 链接均未到达 SuccessFactors")
        return False

    async def _navigate_direct(self) -> bool:
        """直接访问 SuccessFactors URL 作为回退"""
        logger.info(f"直接访问: {config.TARGET_URL}")
        try:
            await self.page.goto(
                config.TARGET_URL,
                wait_until="domcontentloaded",
                timeout=config.NAV_PAGE_LOAD_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"直接访问失败: {e}")
            return False

        await asyncio.sleep(3)

        # 尝试等待 #username (登录表单)
        try:
            await self.page.wait_for_selector(
                "#username",
                timeout=config.NAV_FORM_WAIT_TIMEOUT,
            )
            logger.info("登录表单已加载 (直接访问)")
            return True
        except Exception:
            logger.info("未找到 #username, 尝试点击登录链接...")

        # 点击登录链接进入登录页 (支持 profileWidget/登入/Sign In 多语言)
        if await self._click_sign_in_link():
            return True
        logger.warning("所有登录链接选择器均失败")

        return False

    async def _click_sign_in_link(self) -> bool:
        """
        点击 Sign In / 登入 链接跳转到登录页.
        支持 profileWidget (新版SAP UI, onclick JS 跳转) 和传统选择器.

        优化策略:
          1. 多策略点击: 直接调用 JS 函数 → dispatchEvent → el.click() → Playwright click
          2. 新标签页检测: handleViewProfileAction 可能用 window.open() 打开新页
          3. URL 变化监控: SPA 导航 (history.pushState) 检测
          4. 多目标轮询: 同时检查当前页和新增页面的 #username

        返回 True 表示成功点击并进入登录页.
        """
        for sel_idx, sel in enumerate(self.SIGN_IN_SELECTORS):
            try:
                link = self.page.locator(sel).first
                # 等待元素出现 (页面可能还在加载/转圈)
                # 只对前2个高优先级选择器 (profileWidget) 等待, 其余快速检测
                wait_timeout = 8000 if sel_idx < 2 else 1000
                try:
                    await link.wait_for(state="attached", timeout=wait_timeout)
                except Exception:
                    continue
                if await link.count() == 0:
                    continue
                visible = await link.is_visible()
                if not visible:
                    continue

                href = await link.get_attribute("href") or ""
                onclick = await link.get_attribute("onclick") or ""
                text = (await link.text_content() or "").strip()
                logger.info(
                    f"点击登录链接: selector='{sel}', text='{text}', href='{href[:60]}', onclick='{onclick[:80]}'"
                )

                # 记录点击前状态用于变化检测
                url_before = self.page.url
                pages_before = len(self.context.pages)

                # ---- 多策略点击 ----
                # 对于 handleViewProfileAction (href="#" + onclick JS 跳转):
                #   策略A: 直接调用 j2w.TC.handleViewProfileAction (最可靠, 绕过 isTrusted 检查)
                #   策略B: dispatchEvent(MouseEvent)  (比 el.click() 更完整的事件生命周期)
                #   策略C: el.click()                 (简单合成点击)
                #   策略D: Playwright link.click()    (浏览器原生点击)
                click_strategies = []

                if "handleViewProfileAction" in onclick:
                    # SAP UI 框架函数: 直接调用最可靠
                    click_strategies.append("direct_call")
                    click_strategies.append("dispatch_event")
                    click_strategies.append("element_click")
                elif href == "#":
                    click_strategies.append("dispatch_event")
                    click_strategies.append("element_click")
                else:
                    click_strategies.append("playwright_click")

                element_handle = await link.element_handle()

                for strategy_idx, strategy in enumerate(click_strategies):
                    try:
                        logger.info(f"  点击策略 [{strategy_idx + 1}/{len(click_strategies)}]: {strategy}")

                        if strategy == "direct_call":
                            # 直接调用 SAP 框架函数, 绕过 isTrusted 检查
                            # 返回值用于检测: false=函数不存在或未就绪, true=已调用
                            call_result = await self.page.evaluate("""() => {
                                if (window.j2w && window.j2w.TC && window.j2w.TC.handleViewProfileAction) {
                                    const evt = new MouseEvent('click', {
                                        bubbles: true, cancelable: true, view: window
                                    });
                                    window.j2w.TC.handleViewProfileAction(evt);
                                    return true;
                                }
                                return false;
                            }""")
                            if call_result is False:
                                raise RuntimeError("j2w.TC.handleViewProfileAction 未就绪, JS 框架可能未完全加载")
                            logger.info("  direct_call 已调用 j2w.TC.handleViewProfileAction")

                        elif strategy == "dispatch_event":
                            # 派发完整 MouseEvent (bubbles + cancelable)
                            await self.page.evaluate(
                                """(el) => {
                                const evt = new MouseEvent('click', {
                                    bubbles: true, cancelable: true, view: window
                                });
                                el.dispatchEvent(evt);
                            }""",
                                element_handle,
                            )

                        elif strategy == "element_click":
                            # el.click() 合成点击
                            await self.page.evaluate("(el) => el.click()", element_handle)

                        elif strategy == "playwright_click":
                            # Playwright 原生点击 (不等待导航, 由后续轮询处理)
                            await link.click(no_wait_after=True)

                    except Exception as click_err:
                        logger.warning(f"  策略 {strategy} 失败: {click_err}")
                        continue

                    # ---- 多目标结果检测 ----
                    # 检测3种可能: 当前页SPA更新 / 新标签页打开 / URL变化
                    if await self._poll_login_form_appeared(url_before, pages_before):
                        return True

                    logger.info(f"  策略 {strategy} 后未检测到登录表单, 尝试下一个策略")

                logger.warning(f"selector '{sel}' 所有点击策略均未到达登录页, 尝试下一个选择器")
            except Exception as e:
                logger.warning(f"selector '{sel}' 失败: {e}")
                continue

        return False

    async def _poll_login_form_appeared(self, url_before: str, pages_before: int, timeout_s: int = None) -> bool:
        """
        轮询检测登录表单是否出现.
        同时检测3种导航结果:
          1. 当前页 SPA 更新: #username 在 self.page 出现
          2. 新标签页打开: context.pages 新增页面含 #username
          3. URL 变化: 当前页 URL 改变 (SPA history.pushState)

        参数:
          url_before: 点击前的 URL (用于检测 SPA 导航)
          pages_before: 点击前的页面数 (用于检测新标签页)
          timeout_s: 轮询超时秒数 (默认使用 config.NAV_FORM_WAIT_TIMEOUT)
        """
        if timeout_s is None:
            timeout_s = config.NAV_FORM_WAIT_TIMEOUT // 1000

        for i in range(timeout_s):
            await asyncio.sleep(1)

            # 检测1: 当前页 #username 出现 (SPA 更新)
            try:
                if await self.page.locator("#username").count() > 0:
                    logger.info(f"登录表单已加载 (当前页, 第 {i + 1}s)")
                    return True
            except Exception:
                pass

            # 检测2: 新标签页打开, 切换到新页并检查 #username
            current_pages = self.context.pages
            if len(current_pages) > pages_before:
                for new_page in current_pages[pages_before:]:
                    try:
                        if await new_page.locator("#username").count() > 0:
                            logger.info(f"登录表单已加载 (新标签页, 第 {i + 1}s)")
                            logger.info(f"新标签页 URL: {new_page.url}")
                            self.page = new_page
                            self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)
                            return True
                    except Exception:
                        pass
                logger.info(f"检测到新标签页 ({len(current_pages) - pages_before}个), 但未含 #username")

            # 检测3: URL 变化 (SPA history.pushState 导航)
            current_url = self.page.url
            if current_url != url_before:
                logger.info(f"URL 已变化 (第 {i + 1}s): {url_before[:60]} → {current_url[:60]}")
                # URL 变化后额外等待 SPA 渲染
                await asyncio.sleep(2)
                try:
                    if await self.page.locator("#username").count() > 0:
                        logger.info(f"登录表单已加载 (URL变化后, 第 {i + 1}s)")
                        return True
                except Exception:
                    pass

            if (i + 1) % 5 == 0:
                logger.info(f"等待登录表单... (第 {i + 1}s)")

        return False

    async def _wait_for_recaptcha(self) -> bool:
        """等待 reCAPTCHA 完全渲染"""
        logger.info("等待 reCAPTCHA 渲染...")
        timeout = config.RECAPTCHA_RENDER_WAIT
        half_timeout = timeout // 2

        for i in range(timeout):
            # 防御: 页面关闭时终止等待
            if self.page is None or self.page.is_closed():
                logger.warning("页面已关闭, 停止等待 reCAPTCHA")
                return False
            frames = self.page.frames
            for frame in frames:
                url = frame.url
                if "recaptcha/api2/anchor" in url:
                    try:
                        checkbox = frame.locator(".recaptcha-checkbox-border")
                        if await checkbox.count() > 0:
                            logger.info(f"reCAPTCHA 已完全渲染 (checkbox 元素已就绪, 第 {i + 1}s)")
                            await self._take_screenshot("02_recaptcha_loaded")

                            all_frames = self.page.frames
                            logger.info(f"当前页面共有 {len(all_frames)} 个 frame:")
                            for f in all_frames:
                                logger.info(f"  Frame: {f.url[:120]}")
                            return True
                    except Exception:
                        pass

            try:
                gtype = await self.page.evaluate("typeof grecaptcha")
                if gtype != "undefined":
                    logger.info(f"grecaptcha 对象已加载 (第 {i + 1}s), 继续等待 iframe...")
            except Exception:
                pass

            # 半程重载: 如果一半时间过去仍未渲染, 尝试重载页面
            if i == half_timeout:
                logger.warning(f"reCAPTCHA 已等待 {half_timeout}s 未渲染, 尝试重载页面...")
                try:
                    await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                    logger.info("页面已重载, 继续等待 reCAPTCHA...")
                except Exception as e:
                    logger.warning(f"页面重载失败: {e}")

            await asyncio.sleep(1)
            if (i + 1) % 10 == 0:
                logger.info(f"等待 reCAPTCHA... (第 {i + 1}/{timeout}s)")

        await self._take_screenshot("02_recaptcha_loaded")
        logger.warning(f"reCAPTCHA 渲染等待超时 ({timeout}s)")
        return False

    async def _dismiss_cookie_notice(self):
        """关闭 cookie 通知弹窗"""
        try:
            accept_btn = self.page.locator("button:has-text('Accept')")
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                logger.info("已关闭 cookie 弹窗")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    # ========================================================
    # 提取 reCAPTCHA sitekey
    # ========================================================
    async def extract_sitekey(self) -> str:
        """从页面自动提取 reCAPTCHA sitekey"""
        try:
            sitekey = await self.page.get_attribute(".g-recaptcha", "data-sitekey")
            if sitekey:
                logger.info(f"从 .g-recaptcha 提取到 sitekey: {sitekey}")
                return sitekey
        except Exception:
            pass

        try:
            iframes = self.page.frames
            for frame in iframes:
                url = frame.url
                if "recaptcha/api2/anchor" in url and "k=" in url:
                    for param in url.split("&"):
                        if "k=" in param:
                            sitekey = param.split("k=")[-1]
                            logger.info(f"从 iframe URL 提取到 sitekey: {sitekey}")
                            return sitekey
        except Exception as e:
            logger.warning(f"从 iframe 提取 sitekey 失败: {e}")

        logger.warning(f"无法从页面提取 sitekey, 使用预配置值: {config.RECAPTCHA_SITEKEY}")
        return config.RECAPTCHA_SITEKEY

    # ========================================================
    # 求解 reCAPTCHA (子类必须实现)
    # ========================================================
    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        求解 reCAPTCHA (子类必须实现)
        返回 token (API 方案) 或 None (浏览器内方案)
        """
        raise NotImplementedError("子类必须实现 solve_recaptcha()")

    # ========================================================
    # 注入 token 并提交表单
    # ========================================================
    async def inject_token_and_submit(self, token: str | None):
        """将 reCAPTCHA token 注入页面表单, 填写账号, 提交"""
        if token:
            logger.info("注入 reCAPTCHA token 到表单...")

            inject_script = f"""
            var textarea = document.getElementById('g-recaptcha-response');
            if (!textarea) {{
                textarea = document.createElement('textarea');
                textarea.id = 'g-recaptcha-response';
                textarea.name = 'g-recaptcha-response';
                textarea.style.display = 'none';
                document.body.appendChild(textarea);
            }}
            textarea.value = '{token}';

            var sfField = document.getElementById('recaptcha_response_field');
            if (sfField) {{
                sfField.value = '{token}';
            }}

            if (typeof ___grecaptcha_cfg !== 'undefined') {{
                try {{
                    var clients = ___grecaptcha_cfg.clients;
                    for (var key in clients) {{
                        var client = clients[key];
                        (function walk(obj, depth) {{
                            if (depth > 5) return;
                            for (var k in obj) {{
                                if (typeof obj[k] === 'function' && k.length === 2) {{
                                    try {{ obj[k]('{token}'); }} catch(e) {{}}
                                }} else if (typeof obj[k] === 'object' && obj[k] !== null) {{
                                    walk(obj[k], depth + 1);
                                }}
                            }}
                        }})(client, 0);
                    }}
                }} catch(e) {{
                    console.log('回调调用异常:', e);
                }}
            }}
            """
            await self.page.evaluate(inject_script)
            logger.info("reCAPTCHA token 注入完成")
            await self._take_screenshot("02_token_injected")
        else:
            logger.info(f"[{self.method_name}] reCAPTCHA 已在浏览器内完成, 跳过 token 注入")

        await self._fill_credentials()
        await self._submit_form()

    async def _fill_credentials(self):
        """填写邮箱和密码"""
        logger.info("填写账号信息...")

        try:
            email_field = self.page.locator("#username")
            await email_field.wait_for(state="visible")
            await email_field.fill(config.ACCOUNT_EMAIL)
            logger.info(f"邮箱已填写: {config.ACCOUNT_EMAIL}")

            password_field = self.page.locator("#password")
            await password_field.fill(config.ACCOUNT_PASSWORD)
            logger.info("密码已填写")

        except Exception as e:
            logger.error(f"无法找到账号输入框: {e}")
            raise

    async def _submit_form(self):
        """提交登录表单"""
        logger.info("提交表单...")

        # 防御: 页面已关闭时无法提交
        if self.page is None or self.page.is_closed():
            logger.error("页面已关闭, 无法提交表单")
            return

        try:
            submit_btn = self.page.locator("input[type='submit']")
            await submit_btn.click()
            logger.info("表单已提交")
        except Exception:
            logger.info("未找到提交按钮, 使用 JS 提交表单")
            try:
                await self.page.evaluate("document.getElementById('careerform').submit();")
            except Exception as e:
                logger.warning(f"JS 提交表单也失败: {e}")

        await asyncio.sleep(3)
        await self._take_screenshot("03_after_submit")

    # ========================================================
    # 验证结果
    # ========================================================
    async def verify_result(self) -> bool:
        """
        验证是否成功通过 reCAPTCHA 并进入下一步
        返回 True 表示成功

        判断逻辑 (按优先级):
        1. 浏览器错误页 (chrome-error://, about:blank) -> False (误报防护)
        2. 页面已跳转 (非登录页, HTTP/HTTPS) -> True (reCAPTCHA 通过)
        3. 仍在登录页但有账号错误信息 -> True (reCAPTCHA 通过, 账号验证失败)
        4. 仍在登录页且无错误信息 -> False (reCAPTCHA 可能未通过)

        多语言兼容: 支持英文和中文页面标题/错误信息

        误报防护:
        - chrome-error://chromewebdata/ 是浏览器崩溃/网络错误页, 不是真实跳转
        - general_errors 只匹配可见错误提示元素, 不匹配整个 HTML (避免 CSS 类名/JS 代码误匹配)

        页面导航处理:
        - 表单提交后页面可能仍在导航中, page.content() 会抛异常
        - 使用重试机制等待页面稳定 (最多 3 次, 每次间隔 2s)
        """
        # 防御: 页面已关闭时无法验证
        if self.page is None or self.page.is_closed():
            logger.error("页面已关闭, 无法验证结果")
            return False

        # 等待页面稳定 (表单提交后可能仍在导航)
        current_url = ""
        title = ""
        page_content = ""
        content_lower = ""

        for retry in range(3):
            try:
                await asyncio.sleep(2 if retry > 0 else 0)
                current_url = self.page.url
                title = await self.page.title()
                page_content = await self.page.content()
                content_lower = page_content.lower()
                break
            except Exception as e:
                if retry < 2:
                    logger.warning(f"页面仍在导航中, 等待重试 ({retry + 1}/3): {e}")
                    await asyncio.sleep(2)
                else:
                    # 最后一次重试仍失败: 尝试仅获取 URL (URL 总是可读的)
                    logger.warning(f"page.content() 持续失败, 仅使用 URL 判断: {e}")
                    current_url = self.page.url
                    title = ""
                    page_content = ""
                    content_lower = ""

        logger.info(f"当前 URL: {current_url}")
        logger.info(f"当前页面标题: {title}")

        # ---- 1. 浏览器错误页检测 (误报防护) ----
        # chrome-error://chromewebdata/ = 浏览器崩溃/网络错误
        # about:blank = 空白页 (导航失败)
        BROWSER_ERROR_URL_PREFIXES = [
            "chrome-error://",
            "chrome://network-error",
            "about:blank",
            "about:neterror",
            "data:,",
        ]
        if any(current_url.startswith(prefix) for prefix in BROWSER_ERROR_URL_PREFIXES):
            logger.error(f"检测到浏览器错误页面: {current_url} - 这不是真实跳转, reCAPTCHA 验证失败")
            await self._take_screenshot("04_browser_error")
            return False

        # ---- 1b. 早期成功检测 (URL-based) ----
        # 表单提交后如果跳转到 portal/career 门户, 说明 reCAPTCHA 通过且登录成功
        # 这种情况页面可能仍在加载, page.content() 可能失败, 所以先检查 URL
        # 注意: "/portalcareer" 包含 "career", 必须用精确路径区分登录页和门户页
        # 登录页: /career? 或 /careers?company=Atos
        # 门户页: /portalcareer?
        url_lower = current_url.lower()
        is_portal_url = "/portalcareer" in url_lower or "/portal" in url_lower
        is_login_url = "/career?" in url_lower or "/careers?" in url_lower
        if is_portal_url and not is_login_url:
            logger.info(f"URL 包含门户关键词, reCAPTCHA 验证通过! URL: {current_url}")
            await self._take_screenshot("04_success")
            return True

        # ---- 2. 登录页判定 ----
        LOGIN_TITLE_KEYWORDS = [
            "Sign In",
            "Login",
            "Log In",  # 英文
            "登录",
            "登入",
            "登錄",  # 中文 (简/繁)
            "职业机会",
            "職業機會",  # 中文 SAP SuccessFactors 标题
        ]
        # URL 关键词 (SuccessFactors 登录页特征)
        LOGIN_URL_KEYWORDS = ["career", "successfactors", "login"]

        is_login_page = any(kw in title for kw in LOGIN_TITLE_KEYWORDS) and any(
            kw in current_url.lower() for kw in LOGIN_URL_KEYWORDS
        )

        if is_login_page:
            # ---- 3. 账号错误检测 (精确匹配, 只查可见错误提示) ----
            # 使用 JS 提取可见错误提示元素的文本, 避免匹配 CSS 类名/JS 代码中的关键词
            visible_error_text = await self._extract_visible_error_text()
            visible_error_lower = visible_error_text.lower()

            # 账号错误关键词 (中英文) - 表示 reCAPTCHA 已通过, 账号验证失败
            account_error_keywords = [
                # 英文
                "invalid email",
                "invalid password",
                "incorrect",
                "invalid login",
                "authentication failed",
                "email address or password",
                "login failed",
                "account is locked",
                "too many attempts",
                # 中文
                "邮箱或密码",
                "密码不正确",
                "登录失败",
                "无效",
                "认证失败",
                "不正确",
                "账号或密码",
                "用户名或密码",
                "账户已锁定",
                "尝试次数过多",
            ]
            for keyword in account_error_keywords:
                found = keyword in visible_error_lower if keyword.isascii() else keyword in visible_error_text
                if found:
                    logger.info(f"检测到账号错误: '{keyword}'")
                    logger.info("reCAPTCHA 已通过! 账号验证失败 (使用测试账号, 预期行为)")
                    await self._take_screenshot("04_success")
                    return True

            # reCAPTCHA 仍在页面上 -> 验证未通过
            if "recaptcha" in content_lower:
                logger.error("reCAPTCHA 验证可能失败 - 页面仍显示 reCAPTCHA 且无错误信息")
                return False

            logger.warning("仍在登录页, 可能 reCAPTCHA 未通过")
            return False

        # ---- 4. 非 HTTP(S) URL 检测 (额外的误报防护) ----
        if not current_url.startswith(("http://", "https://")):
            logger.error(f"非 HTTP(S) URL: {current_url} - 可能是浏览器内部页面, 验证失败")
            await self._take_screenshot("04_non_http")
            return False

        logger.info("页面已跳转, reCAPTCHA 验证通过!")
        await self._take_screenshot("04_success")
        return True

    async def _extract_visible_error_text(self) -> str:
        """
        提取页面上可见的错误提示文本

        只提取以下元素的可见文本内容, 避免匹配 CSS 类名/JS 代码:
        - .error-message, .error, .alert, .warning
        - [role='alert'], [class*='error'], [class*='Error']
        - #errorMsg, #errorMessage, .errorMsg
        - SAP SuccessFactors 特有: .errorText, .messageError

        返回: 所有匹配元素的可见文本拼接 (用换行分隔)
        """
        try:
            error_text = await self.page.evaluate("""() => {
                const selectors = [
                    '.error-message', '.error', '.alert', '.alert-danger',
                    '.warning', '[role="alert"]',
                    '#errorMsg', '#errorMessage', '.errorMsg',
                    '.errorText', '.messageError',
                    '[class*="error-message"]', '[class*="errorMessage"]',
                    '.field-error', '.form-error',
                    '.notification-error', '.toast-error',
                ];
                const texts = [];
                for (const sel of selectors) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            // 只收集可见元素
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                const text = el.textContent.trim();
                                if (text) texts.push(text);
                            }
                        }
                    } catch(e) {}
                }
                return texts.join('\\n');
            }""")
            return error_text or ""
        except Exception as e:
            logger.warning(f"提取可见错误文本失败: {e}, 回退到页面文本")
            # Fallback: 提取 body 可见文本 (比 HTML 安全得多)
            try:
                return await self.page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                return ""

    # ========================================================
    # 辅助方法
    # ========================================================
    async def _take_screenshot(self, name: str, full_page: bool = False):
        """保存截图 (优化: animations=disabled 减少闪屏)"""
        if not config.SAVE_SCREENSHOTS:
            return
        # 防御: 页面已关闭时跳过截图, 避免 TargetClosedError
        if self.page is None or self.page.is_closed():
            logger.debug(f"截图跳过 (页面已关闭): {name}")
            return
        path = os.path.join(self.screenshot_dir, f"{name}.png")
        try:
            await self.page.screenshot(
                path=path,
                full_page=full_page,
                animations="disabled",
                caret="hide",
            )
            logger.info(f"截图已保存: {path}")
        except Exception as e:
            logger.warning(f"截图失败: {e}")

    async def _get_recaptcha_frame(self, frame_type: str = "anchor"):
        """
        获取 reCAPTCHA iframe
        frame_type: "anchor" (checkbox) 或 "bframe" (挑战弹窗)
        """
        for frame in self.page.frames:
            url = frame.url
            if frame_type == "anchor" and "recaptcha/api2/anchor" in url:
                return frame
            if frame_type == "bframe" and "recaptcha/api2/bframe" in url:
                return frame
        return None

    async def close(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("浏览器已关闭")

    # ========================================================
    # 主流程
    # ========================================================
    @staticmethod
    def _async_exception_handler(loop, context):
        """
        自定义 asyncio 异常处理器.
        抑制页面关闭时 Playwright 内部 Future 产生的 TargetClosedError,
        避免无关的 "Future exception was never retrieved" 警告.
        """
        exception = context.get("exception")
        exc_type = type(exception).__name__ if exception else ""
        if exception and "TargetClosed" in exc_type:
            logger.debug(f"抑制 TargetClosedError (页面关闭时的异步操作): {exception}")
        else:
            loop.default_exception_handler(context)

    async def run(self) -> bool:
        """
        执行完整的 reCAPTCHA 绕过流程
        返回 True 表示成功
        """
        # 安装自定义异常处理器, 抑制页面关闭时的孤儿 Future 异常
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(self._async_exception_handler)

        try:
            logger.info("=" * 60)
            logger.info(f"  方案: {self.method_desc}")
            logger.info("=" * 60)

            # Step 1: 初始化浏览器
            await self.init_browser()

            # Step 2: 导航到目标页面
            await self.navigate_to_target()

            # Step 3: 提取 reCAPTCHA sitekey
            sitekey = await self.extract_sitekey()
            page_url = self.page.url
            logger.info(f"页面 URL: {page_url}")
            logger.info(f"Sitekey: {sitekey}")

            # Step 4: 求解 reCAPTCHA (子类实现)
            token = await self.solve_recaptcha(sitekey, page_url)

            # Step 5: 注入 token 并提交表单
            await self.inject_token_and_submit(token)

            # Step 6: 验证结果
            success = await self.verify_result()

            if success:
                logger.info("=" * 60)
                logger.info(f"  [{self.method_name}] reCAPTCHA 自动化绕过成功!")
                logger.info("=" * 60)
            else:
                logger.error("=" * 60)
                logger.error(f"  [{self.method_name}] reCAPTCHA 自动化绕过失败")
                logger.error("=" * 60)

            return success

        except Exception as e:
            logger.error(f"执行过程中发生异常: {e}", exc_info=True)
            await self._take_screenshot("error")
            return False

        finally:
            if self._keep_browser_open:
                logger.info(f"[{self.method_name}] 浏览器保持打开状态, 可手动查看页面...")
                while True:
                    await asyncio.sleep(60)
