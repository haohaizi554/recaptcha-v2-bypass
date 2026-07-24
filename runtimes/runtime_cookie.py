"""
方案 4: 无障碍 Cookie (Accessibility Cookie, 免费)
==================================================
流程:
  1. 在浏览器 context 中设置 Google reCAPTCHA 无障碍 cookie
  2. 导航到目标页面
  3. reCAPTCHA 检测到无障碍 cookie 后自动通过 (无需点击 checkbox)
  4. 填写账号信息并提交

工作原理:
  Google 为视障用户提供了 reCAPTCHA 无障碍功能
  注册后获得一个 cookie, 设置后 reCAPTCHA 会自动通过
  注册地址: https://www.google.com/recaptcha/admin/accessibility

优点: 完全免费, 速度极快 (无需求解), 无需 AI 模型
缺点: 需要注册 Google 无障碍功能获取 cookie, cookie 有有效期
"""

import asyncio
import logging

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


class CookieRuntime(BaseBypassRuntime):
    """无障碍 Cookie 方案运行时"""

    method_name = "cookie"
    method_desc = "无障碍 Cookie (Accessibility Cookie, 免费)"

    def __init__(self, cookie_value: str = None):
        """
        cookie_value: 无障碍 cookie 值
                       未指定时使用 config.RECAPTCHA_ACCESSIBILITY_COOKIE
        """
        super().__init__()
        self.cookie_value = cookie_value or config.RECAPTCHA_ACCESSIBILITY_COOKIE

    async def _post_context_init(self):
        """在 context 创建后设置无障碍 cookie"""
        if not self.cookie_value or "YOUR_" in self.cookie_value:
            logger.warning(
                "[Cookie] 无障碍 cookie 未配置, "
                "请在 config.py 中设置 RECAPTCHA_ACCESSIBILITY_COOKIE "
                "或在面板中输入 cookie 值"
            )
            logger.info(
                "[Cookie] 获取方式: 访问 "
                "https://www.google.com/recaptcha/admin/accessibility 注册"
            )
            return

        logger.info("[Cookie] 设置 reCAPTCHA 无障碍 cookie...")

        # 先访问 Google 域名以设置 cookie
        temp_page = None
        try:
            temp_page = await self.context.new_page()
            await temp_page.goto("https://www.google.com/recaptcha", wait_until="commit")
            await asyncio.sleep(1)

            # 设置无障碍 cookie
            await self.context.add_cookies([
                {
                    "name": "recaptcha-accessibility-cookie",
                    "value": self.cookie_value,
                    "domain": ".google.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                }
            ])

            logger.info("[Cookie] 无障碍 cookie 已设置")
            await temp_page.close()
        except Exception as e:
            logger.warning(f"[Cookie] 设置 cookie 失败: {e}")
            if temp_page:
                try:
                    await temp_page.close()
                except Exception:
                    pass

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        无障碍 cookie 方案: cookie 已在 context 初始化时设置
        reCAPTCHA 应自动通过, 这里只需点击 checkbox 并验证
        返回 None (在浏览器内完成)
        """
        logger.info("[Cookie] 检查 reCAPTCHA 是否已通过无障碍 cookie...")

        await asyncio.sleep(2)

        # 尝试点击 checkbox (无障碍 cookie 模式下应直接通过)
        try:
            frame = await self._get_recaptcha_frame("anchor")
            if frame:
                checkbox = frame.locator(".recaptcha-checkbox-border")
                if await checkbox.count() > 0:
                    await checkbox.click(force=True)
                    logger.info("[Cookie] 已点击 reCAPTCHA checkbox")
                    await asyncio.sleep(3)

                    # 检查是否通过
                    checkbox_el = frame.locator(".recaptcha-checkbox")
                    aria_checked = await checkbox_el.get_attribute("aria-checked")
                    if aria_checked == "true":
                        logger.info("[Cookie] reCAPTCHA 已通过 (无障碍 cookie 生效)!")
                        return None

                    logger.warning(
                        "[Cookie] checkbox 未通过, 无障碍 cookie 可能已过期或无效"
                    )
                else:
                    logger.warning("[Cookie] 未找到 reCAPTCHA checkbox")
            else:
                logger.warning("[Cookie] 未找到 reCAPTCHA anchor iframe")
        except Exception as e:
            logger.warning(f"[Cookie] 点击 checkbox 失败: {e}")

        # 如果无障碍 cookie 未生效, 抛出异常
        raise RuntimeError(
            "[Cookie] 无障碍 cookie 方案失败, "
            "cookie 可能已过期, 请重新获取"
        )
