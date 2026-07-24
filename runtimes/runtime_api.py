"""
方案 2: API 代解 (2captcha / CapSolver, 付费)
==============================================
流程:
  1. 提取 reCAPTCHA sitekey 和页面 URL
  2. 将任务提交给第三方 API 服务
  3. 轮询等待求解结果 (g-recaptcha-response token)
  4. 将 token 注入页面表单

优点: 成功率高 (90-95%), 速度快 (~30s), 实现简单
缺点: 需要付费 (2captcha ~$2.99/1000次, CapSolver ~$0.8/1000次)
"""

import asyncio
import logging

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


class APIRuntime(BaseBypassRuntime):
    """API 代解方案运行时 (2captcha / CapSolver)"""

    method_name = "api"
    method_desc = "API 代解 (2captcha / CapSolver, 付费)"

    def __init__(self, provider: str = None):
        """
        provider: "2captcha" | "capsolver"
                  未指定时使用 config.SOLVER_METHOD
        """
        super().__init__()
        self.provider = provider or config.SOLVER_METHOD
        if self.provider == "audio":
            # 如果 config 默认是 audio, 回退到 2captcha
            self.provider = "2captcha"

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str:
        """
        调用第三方 API 获取 reCAPTCHA token
        返回 token 字符串 (用于注入页面)
        """
        from captcha_solver import solve_recaptcha as api_solve

        logger.info(f"[API] 使用 {self.provider} 求解 reCAPTCHA v2...")

        # 验证 API Key
        if self.provider == "2captcha":
            if not config.TWOCAPTCHA_API_KEY or "YOUR_" in config.TWOCAPTCHA_API_KEY:
                raise ValueError("[API] 2captcha API Key 未配置, 请在 config.py 中设置 TWOCAPTCHA_API_KEY")
        elif self.provider == "capsolver":
            if not config.CAPSOLVER_API_KEY or "YOUR_" in config.CAPSOLVER_API_KEY:
                raise ValueError("[API] CapSolver API Key 未配置, 请在 config.py 中设置 CAPSOLVER_API_KEY")
        else:
            raise ValueError(f"[API] 不支持的 provider: {self.provider}")

        # 在线程池中运行同步的 API 调用
        token = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: api_solve(
                method=self.provider,
                sitekey=sitekey,
                page_url=page_url,
                twocaptcha_key=config.TWOCAPTCHA_API_KEY,
                capsolver_key=config.CAPSOLVER_API_KEY,
            ),
        )

        if not token:
            raise RuntimeError(f"[API] {self.provider} 返回空 token")

        logger.info(f"[API] reCAPTCHA token 获取成功 (长度: {len(token)})")
        return token
