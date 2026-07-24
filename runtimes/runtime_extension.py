"""
方案 5: 浏览器扩展 (NopeCHA, 免费)
====================================
流程:
  1. 加载 NopeCHA Chrome 扩展
  2. 导航到目标页面
  3. 点击 reCAPTCHA checkbox
  4. NopeCHA 自动识别并解决图像挑战
  5. 等待 reCAPTCHA 通过

工作原理:
  NopeCHA 是一款免费的 Chrome 扩展, 支持自动求解 reCAPTCHA v2
  通过本地 AI 模型识别图像挑战中的目标

优点: 免费, 自动化程度高, 无需手动干预
缺点: 需要下载扩展, 仅支持非无头模式, 扩展更新可能影响兼容性

扩展获取:
  1. Chrome Web Store: https://chrome.google.com/webstore/detail/nopecha-captcha-solver/dknliebolcfipdbfhohdchdbmldibjco
  2. 手动下载 CRX 并解压到 extensions/nopecha/ 目录
  3. 或使用 extensions/nopecha/ 中已解压的扩展文件
"""

import asyncio
import logging
import os

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


class ExtensionRuntime(BaseBypassRuntime):
    """浏览器扩展方案运行时 (NopeCHA)"""

    method_name = "extension"
    method_desc = "浏览器扩展 (NopeCHA, 免费)"

    def __init__(self, extension_path: str = None):
        """
        extension_path: NopeCHA 扩展目录路径
                         未指定时使用 config.NOPECHA_EXTENSION_PATH
        """
        super().__init__()
        self.extension_path = extension_path or config.NOPECHA_EXTENSION_PATH
        # 扩展模式必须非无头
        self._original_headless = config.BROWSER_HEADLESS

    def _get_browser_args(self) -> list:
        """添加扩展加载参数"""
        args = super()._get_browser_args()

        ext_path = self.extension_path
        if ext_path and os.path.isdir(ext_path):
            args.append(f"--disable-extensions-except={ext_path}")
            args.append(f"--load-extension={ext_path}")
            logger.info(f"[Extension] 扩展路径: {ext_path}")
        else:
            logger.warning(
                f"[Extension] 扩展目录不存在: {ext_path}\n"
                "请下载 NopeCHA 扩展并解压到该目录, 或修改 config.NOPECHA_EXTENSION_PATH\n"
                "Chrome Web Store: https://chrome.google.com/webstore/detail/"
                "nopecha-captcha-solver/dknliebolcfipdbfhohdchdbmldibjco"
            )

        return args

    async def init_browser(self):
        """
        重写浏览器初始化: 扩展模式必须使用非无头 + persistent context
        """
        if not self.extension_path or not os.path.isdir(self.extension_path):
            raise RuntimeError(
                f"[Extension] NopeCHA 扩展目录不存在: {self.extension_path}\n"
                "请下载 NopeCHA 扩展并解压到该目录\n"
                "Chrome Web Store ID: dknliebolcfipdbfhohdchdbmldibjco"
            )

        # 扩展不支持无头模式
        if config.BROWSER_HEADLESS:
            logger.warning("[Extension] 扩展模式不支持无头, 切换到有头模式")
            config.BROWSER_HEADLESS = False

        await super().init_browser()
        config.BROWSER_HEADLESS = self._original_headless

    async def _post_navigate_hook(self):
        """导航完成后, 等待 NopeCHA 扩展就绪"""
        logger.info("[Extension] 等待 NopeCHA 扩展就绪...")
        await asyncio.sleep(5)

        # 检查扩展是否已加载
        try:
            # 尝试访问扩展的 service worker 或 popup 页面
            extension_id = config.NOPECHA_EXTENSION_ID
            pages = self.context.pages
            logger.info(f"[Extension] 当前打开的页面数: {len(pages)}")
        except Exception as e:
            logger.warning(f"[Extension] 扩展状态检查失败: {e}")

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        使用 NopeCHA 扩展自动求解 reCAPTCHA
        返回 None (在浏览器内完成)
        """
        logger.info("[Extension] 使用 NopeCHA 扩展求解 reCAPTCHA v2...")

        # Step 1: 点击 checkbox 触发挑战
        try:
            frame = await self._get_recaptcha_frame("anchor")
            if not frame:
                raise RuntimeError("[Extension] 未找到 reCAPTCHA anchor iframe")

            checkbox = frame.locator(".recaptcha-checkbox-border")
            if await checkbox.count() == 0:
                raise RuntimeError("[Extension] 未找到 reCAPTCHA checkbox")

            await checkbox.click(force=True)
            logger.info("[Extension] 已点击 reCAPTCHA checkbox, 等待 NopeCHA 自动求解...")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"[Extension] 点击 checkbox 失败: {e}")

        # Step 2: 等待 NopeCHA 自动求解 (最多 120 秒)
        max_wait = config.EXTENSION_SOLVE_TIMEOUT
        check_interval = 2
        elapsed = 0

        while elapsed < max_wait:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # 检查 checkbox 是否已通过
            try:
                frame = await self._get_recaptcha_frame("anchor")
                if frame:
                    checkbox_el = frame.locator(".recaptcha-checkbox")
                    aria_checked = await checkbox_el.get_attribute("aria-checked")
                    if aria_checked == "true":
                        logger.info(
                            f"[Extension] NopeCHA 求解成功! (耗时 {elapsed}s)"
                        )
                        return None
            except Exception:
                pass

            if elapsed % 10 == 0:
                logger.info(
                    f"[Extension] 等待 NopeCHA 求解中... ({elapsed}/{max_wait}s)"
                )

        raise RuntimeError(
            f"[Extension] NopeCHA 求解超时 ({max_wait}s), "
            "扩展可能未正确加载或版本不兼容"
        )
