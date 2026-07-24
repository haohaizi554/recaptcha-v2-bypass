"""
reCAPTCHA v2 自动化绕过脚本 (Playwright 版)
=============================================
目标: 自动通过 SuccessFactors 登录页面的 reCAPTCHA 验证
技术栈: Playwright + playwright-stealth (反检测)
流程:
  1. 使用 Playwright stealth 模式打开目标页面 (规避反爬检测)
  2. 自动提取 reCAPTCHA sitekey
  3. 调用验证码求解服务 (2captcha / CapSolver) 获取 token
     或使用音频识别方案 (免费)
  4. 将 token 注入页面表单 (API方案) 或在浏览器内完成 (音频方案)
  5. 填写账号信息并提交表单
  6. 验证是否成功进入下一步
"""

import asyncio
import logging
import os
import sys

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

import config
from captcha_solver import solve_recaptcha

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reCAPTCHA-Bypass")


class RecaptchaBypass:
    """reCAPTCHA v2 自动化绕过主类 (Playwright 版)"""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.screenshot_dir = config.SCREENSHOT_DIR
        if config.SAVE_SCREENSHOTS:
            os.makedirs(self.screenshot_dir, exist_ok=True)

    # ========================================================
    # 浏览器初始化
    # ========================================================
    async def init_browser(self):
        """初始化 Playwright 浏览器 (stealth 模式, 规避反爬检测)"""
        logger.info("初始化 Playwright 浏览器 (stealth 模式)...")

        self.playwright = await async_playwright().start()

        # 使用系统 Chrome (channel="chrome"), 避免下载 Chromium
        self.browser = await self.playwright.chromium.launch(
            headless=config.BROWSER_HEADLESS,
            channel="chrome",
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-popup-blocking",
                "--window-size=1920,1080",
            ],
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        # 使用 playwright-stealth v2 API 注入反检测脚本
        stealth = Stealth()
        await stealth.apply_stealth_async(self.context)

        self.page = await self.context.new_page()
        self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)

        logger.info("浏览器初始化完成 (stealth 模式)")

    # ========================================================
    # 页面导航
    # ========================================================
    async def navigate_to_target(self):
        """
        导航到 SuccessFactors 登录页面
        策略: 前 3 次通过 Atos 源网站跳转, 后 3 次直接访问 URL
        """
        max_retries = config.NAV_MAX_RETRIES
        atos_retries = max_retries // 2  # 前 3 次用 Atos 跳转

        for attempt in range(1, max_retries + 1):
            logger.info(f"导航尝试 {attempt}/{max_retries}")

            try:
                if attempt <= atos_retries:
                    # 策略 1: 通过 Atos 源网站跳转
                    success = await self._navigate_via_atos()
                else:
                    # 策略 2: 直接访问 SuccessFactors URL
                    if not config.NAV_DIRECT_URL_FALLBACK:
                        continue
                    logger.info(f"切换到直接 URL 访问策略: {config.TARGET_URL}")
                    success = await self._navigate_direct()

                if not success:
                    logger.warning(f"导航策略未到达登录页, 重试...")
                    continue

                # 已到达登录页, 等待 reCAPTCHA 渲染
                await self._take_screenshot("01_login_page")
                title = await self.page.title()
                logger.info(f"登录页标题: {title}")
                logger.info(f"当前 URL: {self.page.url}")

                if await self._wait_for_recaptcha():
                    logger.info("reCAPTCHA 已就绪, 开始求解流程")
                    return
                else:
                    logger.warning("reCAPTCHA 未加载, 但继续尝试 (可能使用 API 方案)")
                    return

            except Exception as e:
                logger.warning(f"导航尝试 {attempt} 异常: {e}")

        logger.error(f"{max_retries} 次导航尝试均失败")
        raise RuntimeError("无法到达 SuccessFactors 登录页")

    async def _navigate_via_atos(self) -> bool:
        """
        通过 Atos 源网站点击 Apply now 跳转到登录页
        使用多选择器策略, 同时处理新标签页和同页跳转
        返回 True 表示成功到达登录页
        """
        logger.info(f"加载源网站: {config.SOURCE_URL}")
        try:
            await self.page.goto(
                config.SOURCE_URL,
                wait_until="commit",
                timeout=config.NAV_PAGE_LOAD_TIMEOUT,
            )
            await asyncio.sleep(5)
        except Exception as e:
            logger.warning(f"源页面加载较慢, 继续尝试: {e}")
            await asyncio.sleep(3)

        await self._dismiss_cookie_notice()

        try:
            title = await self.page.title()
            logger.info(f"源页面标题: {title}")
        except Exception:
            logger.warning("无法获取源页面标题")

        # 尝试所有 "Apply now" 链接, 直到找到通往 SuccessFactors 的那个
        apply_locator = self.page.locator("a:has-text('Apply now')")
        link_count = await apply_locator.count()
        logger.info(f"找到 {link_count} 个 Apply now 链接")

        if link_count == 0:
            # 回退到其他选择器
            for selector in ["a[href*='successfactors']", "a[href*='career']", "button:has-text('Apply')", "a:has-text('Apply')"]:
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

        # 逐个尝试每个链接
        for link_idx in range(link_count):
            link = apply_locator.nth(link_idx)
            try:
                href = await link.get_attribute("href")
            except Exception:
                href = "(无法获取)"
            logger.info(f"尝试 Apply 链接 [{link_idx}/{link_count}]: href={href}")

            # 记录当前页面数量 (用于检测新标签页)
            pages_before = len(self.context.pages)

            # 点击链接, 同时监听新标签页
            try:
                async with self.context.expect_page(timeout=15000) as new_page_info:
                    await link.click()
                new_page = await new_page_info.value
                logger.info(f"检测到新标签页: {new_page.url}")
                self.page = new_page
                self.page.set_default_timeout(config.BROWSER_TIMEOUT * 1000)
            except Exception:
                logger.info("未检测到新标签页, 检查当前页面是否已跳转...")

            # 等待跳转到 SuccessFactors
            logger.info("等待跳转到 SuccessFactors 登录页...")
            reached_sf = False
            for i in range(15):
                await asyncio.sleep(1)
                current_url = self.page.url
                if "successfactors" in current_url:
                    logger.info(f"已跳转到: {current_url}")
                    reached_sf = True
                    break

            if reached_sf:
                # 等待登录表单加载
                try:
                    await self.page.wait_for_selector(
                        "#username",
                        timeout=config.NAV_FORM_WAIT_TIMEOUT,
                    )
                    logger.info("登录表单已加载")
                    return True
                except Exception:
                    logger.warning("等待 #username 超时")

            # 当前链接未到达 SuccessFactors, 关闭新标签页, 回到源页面尝试下一个
            logger.warning(f"链接 [{link_idx}] 未到达 SuccessFactors, 尝试下一个链接")

            # 关闭可能打开的新标签页
            if len(self.context.pages) > pages_before:
                for p in self.context.pages[pages_before:]:
                    try:
                        await p.close()
                    except Exception:
                        pass
                # 恢复 self.page 到源页面
                self.page = self.context.pages[0]

            # 重新加载源页面
            if link_idx < link_count - 1:
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
        """
        直接访问 SuccessFactors URL 作为回退
        返回 True 表示成功到达登录页
        """
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

        # 等待登录表单加载
        try:
            await self.page.wait_for_selector(
                "#username",
                timeout=config.NAV_FORM_WAIT_TIMEOUT,
            )
            logger.info("登录表单已加载 (直接访问)")
            return True
        except Exception:
            logger.warning("等待 #username 超时 (直接访问)")
            return False

    async def _wait_for_recaptcha(self) -> bool:
        """
        等待 reCAPTCHA 完全渲染
        验证 anchor iframe 内 checkbox 元素已存在
        """
        logger.info("等待 reCAPTCHA 渲染...")
        timeout = config.RECAPTCHA_RENDER_WAIT

        for i in range(timeout):
            frames = self.page.frames
            for frame in frames:
                url = frame.url
                if "recaptcha/api2/anchor" in url:
                    try:
                        checkbox = frame.locator(".recaptcha-checkbox-border")
                        if await checkbox.count() > 0:
                            logger.info(f"reCAPTCHA 已完全渲染 (checkbox 元素已就绪, 第 {i+1}s)")
                            await self._take_screenshot("02_recaptcha_loaded")

                            # 打印所有 frame 用于调试
                            all_frames = self.page.frames
                            logger.info(f"当前页面共有 {len(all_frames)} 个 frame:")
                            for f in all_frames:
                                logger.info(f"  Frame: {f.url[:120]}")
                            return True
                    except Exception:
                        pass

            # 也检查 grecaptcha 对象 (某些情况下 iframe 延迟加载)
            try:
                gtype = await self.page.evaluate("typeof grecaptcha")
                if gtype != "undefined":
                    logger.info(f"grecaptcha 对象已加载 (第 {i+1}s), 继续等待 iframe...")
            except Exception:
                pass

            await asyncio.sleep(1)
            if (i + 1) % 5 == 0:
                logger.info(f"等待 reCAPTCHA... (第 {i+1}/{timeout}s)")

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
        """
        从页面自动提取 reCAPTCHA sitekey
        优先从 data-sitekey 属性获取, 回退到 iframe URL 解析
        """
        # 方法1: 从 .g-recaptcha 元素的 data-sitekey 属性获取
        try:
            sitekey = await self.page.get_attribute(".g-recaptcha", "data-sitekey")
            if sitekey:
                logger.info(f"从 .g-recaptcha 提取到 sitekey: {sitekey}")
                return sitekey
        except Exception:
            pass

        # 方法2: 从 reCAPTCHA iframe 的 URL 参数中解析
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

        # 方法3: 使用 config 中预配置的 sitekey
        logger.warning(f"无法从页面提取 sitekey, 使用预配置值: {config.RECAPTCHA_SITEKEY}")
        return config.RECAPTCHA_SITEKEY

    # ========================================================
    # 求解 reCAPTCHA
    # ========================================================
    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        调用验证码求解服务获取 reCAPTCHA token
        返回 token (audio 模式返回 None, 因为已在浏览器内完成)
        """
        if config.SOLVER_METHOD == "audio":
            return await self._solve_via_audio()

        logger.info(f"使用 {config.SOLVER_METHOD} 求解 reCAPTCHA v2...")

        # 在线程池中运行同步的 API 调用
        token = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: solve_recaptcha(
                method=config.SOLVER_METHOD,
                sitekey=sitekey,
                page_url=page_url,
                twocaptcha_key=config.TWOCAPTCHA_API_KEY,
                capsolver_key=config.CAPSOLVER_API_KEY,
            ),
        )

        logger.info(f"reCAPTCHA token 获取成功 (长度: {len(token)})")
        return token

    async def _solve_via_audio(self) -> None:
        """使用音频识别方式在浏览器内完成 reCAPTCHA"""
        from audio_solver import AudioRecaptchaSolver
        logger.info("使用音频识别方案求解 reCAPTCHA v2...")
        solver = AudioRecaptchaSolver(self.page)
        success = await solver.solve(max_retries=config.RECAPTCHA_MAX_RETRIES)
        if not success:
            raise RuntimeError("音频识别方案求解失败")

    # ========================================================
    # 注入 token 并提交表单
    # ========================================================
    async def inject_token_and_submit(self, token: str | None):
        """
        将 reCAPTCHA token 注入页面表单, 填写账号, 提交
        token 为 None 时 (audio 模式) 跳过注入, 直接填写并提交
        """
        if token:
            logger.info("注入 reCAPTCHA token 到表单...")

            inject_script = f"""
            // 设置 g-recaptcha-response textarea
            var textarea = document.getElementById('g-recaptcha-response');
            if (!textarea) {{
                textarea = document.createElement('textarea');
                textarea.id = 'g-recaptcha-response';
                textarea.name = 'g-recaptcha-response';
                textarea.style.display = 'none';
                document.body.appendChild(textarea);
            }}
            textarea.value = '{token}';

            // 设置 recaptcha_response_field (SuccessFactors 自定义字段)
            var sfField = document.getElementById('recaptcha_response_field');
            if (sfField) {{
                sfField.value = '{token}';
            }}

            // 调用 reCAPTCHA 回调函数 (如果存在)
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
            logger.info("音频模式: reCAPTCHA 已在浏览器内完成, 跳过 token 注入")

        # 填写账号信息
        await self._fill_credentials()

        # 提交表单
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

        try:
            submit_btn = self.page.locator("input[type='submit']")
            await submit_btn.click()
            logger.info("表单已提交")
        except Exception:
            logger.info("未找到提交按钮, 使用 JS 提交表单")
            await self.page.evaluate("document.getElementById('careerform').submit();")

        await asyncio.sleep(3)
        await self._take_screenshot("03_after_submit")

    # ========================================================
    # 验证结果
    # ========================================================
    async def verify_result(self) -> bool:
        """
        验证是否成功通过 reCAPTCHA 并进入下一步
        返回 True 表示成功

        判断逻辑:
        - 页面已跳转 (非登录页) → reCAPTCHA 通过
        - 仍在登录页但有 "invalid"/"error" 等账号错误 → reCAPTCHA 通过, 账号验证失败 (预期)
        - 仍在登录页且无错误信息 → reCAPTCHA 可能未通过
        """
        current_url = self.page.url
        title = await self.page.title()
        page_content = await self.page.content()
        content_lower = page_content.lower()

        logger.info(f"当前 URL: {current_url}")
        logger.info(f"当前页面标题: {title}")

        # 检查是否仍在登录页
        if "Sign In" in title and "career" in current_url:
            # 优先检查账号错误信息 (说明表单已提交, reCAPTCHA 已通过)
            account_error_keywords = [
                "invalid email", "invalid password", "incorrect",
                "invalid login", "authentication failed",
                "email address or password", "login failed",
            ]
            for keyword in account_error_keywords:
                if keyword in content_lower:
                    logger.info(f"检测到账号错误: '{keyword}'")
                    logger.info("reCAPTCHA 已通过! 账号验证失败 (使用测试账号, 预期行为)")
                    await self._take_screenshot("04_success")
                    return True

            # 检查通用错误关键词
            general_errors = ["error", "failed"]
            for keyword in general_errors:
                if keyword in content_lower and "recaptcha" not in content_lower.split(keyword)[0][-200:]:
                    logger.info(f"检测到错误关键词: '{keyword}'")
                    logger.info("reCAPTCHA 可能已通过, 但验证失败 (预期行为)")
                    await self._take_screenshot("04_success")
                    return True

            # 无错误信息, 仍在登录页 → reCAPTCHA 可能未通过
            if "recaptcha" in content_lower:
                logger.error("reCAPTCHA 验证可能失败 - 页面仍显示 reCAPTCHA 且无错误信息")
                return False

            logger.warning("仍在登录页, 可能 reCAPTCHA 未通过")
            return False

        logger.info("页面已跳转, reCAPTCHA 验证通过!")
        await self._take_screenshot("04_success")
        return True

    # ========================================================
    # 辅助方法
    # ========================================================
    async def _take_screenshot(self, name: str):
        """保存截图"""
        if not config.SAVE_SCREENSHOTS:
            return
        path = os.path.join(self.screenshot_dir, f"{name}.png")
        try:
            await self.page.screenshot(path=path)
            logger.info(f"截图已保存: {path}")
        except Exception as e:
            logger.warning(f"截图失败: {e}")

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
    async def run(self) -> bool:
        """
        执行完整的 reCAPTCHA 绕过流程
        返回 True 表示成功
        """
        try:
            # Step 1: 初始化浏览器
            await self.init_browser()

            # Step 2: 导航到目标页面
            await self.navigate_to_target()

            # Step 3: 提取 reCAPTCHA sitekey
            sitekey = await self.extract_sitekey()
            page_url = self.page.url
            logger.info(f"页面 URL: {page_url}")
            logger.info(f"Sitekey: {sitekey}")

            # Step 4: 求解 reCAPTCHA
            token = await self.solve_recaptcha(sitekey, page_url)

            # Step 5: 注入 token 并提交表单
            await self.inject_token_and_submit(token)

            # Step 6: 验证结果
            success = await self.verify_result()

            if success:
                logger.info("=" * 60)
                logger.info("  reCAPTCHA 自动化绕过成功!")
                logger.info("=" * 60)
            else:
                logger.error("=" * 60)
                logger.error("  reCAPTCHA 自动化绕过失败")
                logger.error("=" * 60)

            return success

        except Exception as e:
            logger.error(f"执行过程中发生异常: {e}", exc_info=True)
            await self._take_screenshot("error")
            return False

        finally:
            logger.info("浏览器保持打开状态 (不自动关闭), 可手动查看页面...")
            # 不关闭浏览器, 保持打开供用户查看
            while True:
                await asyncio.sleep(60)


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  reCAPTCHA v2 自动化绕过工具 (Playwright) - ApplyKitty 面试题")
    logger.info("=" * 60)

    bypass = RecaptchaBypass()
    result = asyncio.run(bypass.run())

    sys.exit(0 if result else 1)
