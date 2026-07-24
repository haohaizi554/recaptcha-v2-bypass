"""
方案 1: 音频识别 (faster-whisper 本地模型, 免费)
================================================
流程:
  1. 点击 reCAPTCHA checkbox
  2. 切换到音频挑战
  3. 下载音频文件
  4. 使用 faster-whisper 本地模型识别 (主引擎)
     失败时回退到 Google Speech Recognition (备选引擎)
  5. 输入识别结果并验证

优点: 完全免费, 无需 API Key, 本地运行
缺点: 需要下载模型 (~150MB base), 首次运行较慢
"""

import logging

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)


class AudioRuntime(BaseBypassRuntime):
    """音频识别方案运行时"""

    method_name = "audio"
    method_desc = "音频识别 (faster-whisper 本地模型, 免费)"

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """
        使用音频识别方式在浏览器内完成 reCAPTCHA
        返回 None (在浏览器内完成, 无需 token 注入)
        """
        from audio_solver import AudioRecaptchaSolver

        logger.info("[Audio] 使用音频识别方案求解 reCAPTCHA v2...")

        solver = AudioRecaptchaSolver(self.page)
        success = await solver.solve(max_retries=config.RECAPTCHA_MAX_RETRIES)

        if not success:
            raise RuntimeError("[Audio] 音频识别方案求解失败")

        logger.info("[Audio] reCAPTCHA 音频求解成功!")
        return None
