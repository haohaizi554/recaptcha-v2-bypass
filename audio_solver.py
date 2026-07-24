"""
reCAPTCHA v2 音频识别求解器 (Playwright 版, 免费方案)
=============================================
不依赖付费 API, 通过以下流程绕过 reCAPTCHA v2:
  1. 点击 "I'm not a robot" 复选框
  2. 如果弹出图像挑战, 切换到音频挑战
  3. 下载音频文件
  4. 使用 faster-whisper 本地模型识别音频内容 (主引擎)
     失败时回退到 Google Speech Recognition (备选引擎)
  5. 输入识别结果提交

识别引擎优先级: whisper > google
依赖: faster-whisper (主引擎), pydub (备选引擎格式转换)
"""

import asyncio
import logging
import os
import re
import tempfile
import threading

import requests

import config

logger = logging.getLogger(__name__)

# ============================================================
# 依赖检测
# ============================================================
try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False
    logger.warning("faster_whisper 未安装, Whisper 识别不可用")

try:
    import speech_recognition as sr
    HAS_SPEECH_REC = True
except ImportError:
    HAS_SPEECH_REC = False
    logger.warning("speech_recognition 未安装, Google 识别不可用")

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False
    logger.warning("pydub 未安装, MP3→WAV 转换不可用")


# ============================================================
# WhisperModelManager: 模块级单例, 后台线程预加载
# ============================================================
class WhisperModelManager:
    """
    faster-whisper 模型管理器 (单例模式)
    在后台线程预加载模型, 导航期间并行下载, 不阻塞主流程
    """

    _instance = None
    _lock = threading.Lock()
    _model = None
    _load_lock = threading.Lock()
    _loaded = False
    _loading = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def preload(self):
        """启动后台线程预加载 Whisper 模型"""
        if self._loaded or self._loading:
            return
        with self._load_lock:
            if self._loaded or self._loading:
                return
            self._loading = True
            thread = threading.Thread(target=self._load_model, daemon=True)
            thread.start()
            logger.info("[Whisper] 后台预加载已启动")

    def _load_model(self):
        """在后台线程中加载模型"""
        try:
            logger.info(
                f"[Whisper] 开始加载模型: {config.WHISPER_MODEL_SIZE} "
                f"(device={config.WHISPER_DEVICE}, compute={config.WHISPER_COMPUTE_TYPE})"
            )
            self._model = WhisperModel(
                config.WHISPER_MODEL_SIZE,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE_TYPE,
            )
            self._loaded = True
            self._loading = False
            logger.info("[Whisper] 模型加载完成 (后台线程)")
        except Exception as e:
            self._loading = False
            logger.error(f"[Whisper] 模型加载失败: {e}")

    def get_model(self):
        """
        获取已加载的模型实例
        如果模型尚未加载完成, 阻塞等待
        """
        if not HAS_WHISPER:
            return None
        if self._loaded:
            return self._model
        # 等待后台加载完成
        if self._loading:
            logger.info("[Whisper] 等待模型加载完成...")
            while self._loading and not self._loaded:
                threading.Event().wait(0.5)
        return self._model if self._loaded else None


# ============================================================
# AudioRecaptchaSolver: 主求解器
# ============================================================
class AudioRecaptchaSolver:
    """
    通过音频挑战方式求解 reCAPTCHA v2 (Playwright 版)
    需要配合 Playwright Page 实例使用
    """

    def __init__(self, page):
        """
        page: Playwright Page 实例
        """
        self.page = page
        self.temp_dir = tempfile.mkdtemp(prefix="recaptcha_audio_")

        # 启动 Whisper 模型后台预加载 (导航期间并行下载)
        if config.AUDIO_RECOGNIZER == "whisper" and HAS_WHISPER:
            WhisperModelManager().preload()

    async def solve(self, max_retries: int = None) -> bool:
        """
        执行音频求解流程
        返回 True 表示成功
        """
        if max_retries is None:
            max_retries = config.RECAPTCHA_MAX_RETRIES

        # 检查至少有一个识别引擎可用
        if not HAS_WHISPER and not HAS_SPEECH_REC:
            logger.error("无可用识别引擎 (whisper 和 google 均未安装)")
            return False

        for attempt in range(1, max_retries + 1):
            logger.info(f"[Audio] 第 {attempt}/{max_retries} 次尝试...")

            # 等待 reCAPTCHA 完全渲染
            if not await self._wait_for_recaptcha_render(config.RECAPTCHA_RENDER_WAIT):
                logger.warning("[Audio] reCAPTCHA 未完全渲染, 等待后重试")
                await asyncio.sleep(config.RECAPTCHA_RETRY_DELAY)
                continue

            try:
                if await self._attempt_solve():
                    logger.info("[Audio] reCAPTCHA 音频求解成功!")
                    return True
            except Exception as e:
                logger.warning(f"[Audio] 第 {attempt} 次尝试失败: {e}")
                await self._reset_challenge()

            await asyncio.sleep(config.RECAPTCHA_RETRY_DELAY)

        logger.error("[Audio] 音频求解失败, 已达最大重试次数")
        return False

    async def _attempt_solve(self) -> bool:
        """单次求解尝试"""
        # Step 1: 点击 reCAPTCHA checkbox
        if not await self._click_checkbox():
            logger.warning("[Audio] 无法点击 reCAPTCHA checkbox")
            return False

        await asyncio.sleep(3)

        # Step 2: 检查是否直接通过 (无需图像挑战)
        if await self._is_checked():
            logger.info("[Audio] reCAPTCHA checkbox 直接通过, 无需音频挑战")
            return True

        # Step 3: 切换到音频挑战
        if not await self._switch_to_audio():
            logger.warning("[Audio] 无法切换到音频挑战")
            return False

        await asyncio.sleep(2)

        # Step 4: 下载并识别音频
        audio_url = await self._get_audio_url()
        if not audio_url:
            logger.warning("[Audio] 无法获取音频 URL")
            return False

        audio_text = await self._download_and_recognize(audio_url)
        if not audio_text:
            logger.warning("[Audio] 音频识别失败")
            return False

        logger.info(f"[Audio] 识别结果: {audio_text}")

        # Step 5: 输入识别结果
        await self._enter_audio_response(audio_text)
        await asyncio.sleep(2)

        # Step 6: 验证结果
        if await self._is_checked():
            logger.info("[Audio] reCAPTCHA 音频验证通过!")
            return True

        logger.warning("[Audio] 音频验证未通过")
        return False

    # ========================================================
    # 等待 reCAPTCHA 渲染
    # ========================================================
    async def _wait_for_recaptcha_render(self, timeout: int = 30) -> bool:
        """
        等待 reCAPTCHA 完全渲染
        验证 anchor iframe 内 checkbox 元素已存在 (不只是 iframe URL)
        """
        logger.info(f"[Audio] 等待 reCAPTCHA 渲染 (最多 {timeout}s)...")
        for i in range(timeout):
            frame = self._get_recaptcha_frame("anchor")
            if frame:
                try:
                    checkbox = frame.locator(".recaptcha-checkbox-border")
                    if await checkbox.count() > 0:
                        logger.info(f"[Audio] reCAPTCHA 已完全渲染 (checkbox 元素已就绪, 第 {i+1}s)")
                        return True
                except Exception:
                    pass
            await asyncio.sleep(1)

        logger.warning(f"[Audio] reCAPTCHA 渲染等待超时 ({timeout}s)")
        return False

    # ========================================================
    # 获取 reCAPTCHA Frame (Playwright 方式)
    # ========================================================
    def _get_recaptcha_frame(self, frame_type: str = "anchor"):
        """
        获取 reCAPTCHA iframe (Playwright 自动管理 frames, 无需切换)
        frame_type: "anchor" (checkbox) 或 "bframe" (挑战弹窗)
        """
        for frame in self.page.frames:
            url = frame.url
            if frame_type == "anchor" and "recaptcha/api2/anchor" in url:
                return frame
            if frame_type == "bframe" and "recaptcha/api2/bframe" in url:
                return frame
        return None

    # ========================================================
    # 操作 reCAPTCHA
    # ========================================================
    async def _click_checkbox(self) -> bool:
        """点击 reCAPTCHA 'I'm not a robot' checkbox"""
        try:
            frame = self._get_recaptcha_frame("anchor")
            if not frame:
                logger.warning("[Audio] 未找到 reCAPTCHA anchor iframe")
                return False

            # 使用 Playwright 的 frame 定位器点击 checkbox (force=True 绕过遮挡)
            checkbox = frame.locator(".recaptcha-checkbox-border")
            await checkbox.click(force=True)
            logger.info("[Audio] 已点击 reCAPTCHA checkbox")
            return True

        except Exception as e:
            logger.warning(f"[Audio] 点击 checkbox 失败: {e}")
            return False

    async def _is_checked(self) -> bool:
        """检查 reCAPTCHA 是否已通过"""
        try:
            frame = self._get_recaptcha_frame("anchor")
            if not frame:
                return False

            checkbox = frame.locator(".recaptcha-checkbox")
            aria_checked = await checkbox.get_attribute("aria-checked")

            if aria_checked == "true":
                logger.info("[Audio] reCAPTCHA checkbox 已勾选")
                return True
            return False

        except Exception:
            return False

    async def _switch_to_audio(self) -> bool:
        """切换到音频挑战"""
        try:
            frame = self._get_recaptcha_frame("bframe")
            if not frame:
                logger.warning("[Audio] 未找到挑战 iframe (bframe)")
                return False

            audio_btn = frame.locator(".rc-button-audio")
            await audio_btn.click(force=True)
            logger.info("[Audio] 已切换到音频挑战")

            await asyncio.sleep(2)
            return True

        except Exception as e:
            logger.warning(f"[Audio] 切换音频挑战失败: {e}")
            return False

    async def _get_audio_url(self) -> str:
        """获取音频下载 URL"""
        try:
            frame = self._get_recaptcha_frame("bframe")
            if not frame:
                return ""

            audio_link = frame.locator(".rc-audiochallenge-tdownload-link")
            audio_url = await audio_link.get_attribute("href")

            logger.info(f"[Audio] 音频 URL: {audio_url}")
            return audio_url or ""

        except Exception as e:
            logger.warning(f"[Audio] 获取音频 URL 失败: {e}")
            return ""

    async def _enter_audio_response(self, text: str):
        """输入音频识别结果"""
        try:
            frame = self._get_recaptcha_frame("bframe")
            if not frame:
                return

            response_input = frame.locator("#audio-response")
            await response_input.fill(text)
            logger.info(f"[Audio] 已输入识别结果: {text}")

            # 点击验证按钮 (force=True 绕过遮挡)
            verify_btn = frame.locator("#recaptcha-verify-button")
            await verify_btn.click(force=True)
            logger.info("[Audio] 已点击验证按钮")

            await asyncio.sleep(3)

        except Exception as e:
            logger.warning(f"[Audio] 输入识别结果失败: {e}")

    async def _reset_challenge(self):
        """重置挑战 (点击刷新按钮)"""
        try:
            frame = self._get_recaptcha_frame("bframe")
            if frame:
                reset_btn = frame.locator("#recaptcha-reload-button")
                await reset_btn.click(force=True)
                await asyncio.sleep(2)
        except Exception:
            pass

    # ========================================================
    # 音频下载与识别 (在线程池中运行同步操作)
    # ========================================================
    async def _download_and_recognize(self, audio_url: str) -> str:
        """下载音频文件并使用语音识别转换为文字"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._download_and_recognize_sync, audio_url
        )

    def _download_and_recognize_sync(self, audio_url: str) -> str:
        """
        同步: 下载音频文件并识别 (在线程池中调用)
        按优先级尝试多识别引擎: whisper → google
        """
        try:
            # 下载音频文件 (添加请求头避免被 Google 拦截)
            audio_path = os.path.join(self.temp_dir, "audio.mp3")
            resp = requests.get(
                audio_url,
                timeout=30,
                headers=config.AUDIO_DOWNLOAD_HEADERS,
            )
            with open(audio_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"[Audio] 音频已下载: {audio_path} ({len(resp.content)} bytes)")

            # 按优先级尝试识别引擎
            # 引擎 1: faster-whisper (主引擎, 直接读 MP3)
            if config.AUDIO_RECOGNIZER == "whisper" and HAS_WHISPER:
                text = self._recognize_with_whisper(audio_path)
                if text:
                    digits = self._extract_digits(text)
                    if digits:
                        logger.info(f"[Audio] Whisper 识别提取数字: {digits}")
                        return digits
                    logger.info(f"[Audio] Whisper 识别结果无数字, 返回原文: {text}")
                    return text
                logger.warning("[Audio] Whisper 识别失败, 尝试 Google 备选引擎")

            # 引擎 2: Google Speech Recognition (备选引擎, 需 WAV)
            if HAS_SPEECH_REC:
                wav_path = os.path.join(self.temp_dir, "audio.wav")
                if self._convert_mp3_to_wav(audio_path, wav_path):
                    text = self._recognize_with_google(wav_path)
                    if text:
                        digits = self._extract_digits(text)
                        if digits:
                            logger.info(f"[Audio] Google 识别提取数字: {digits}")
                            return digits
                        logger.info(f"[Audio] Google 识别结果无数字, 返回原文: {text}")
                        return text
                else:
                    logger.warning("[Audio] MP3→WAV 转换失败, Google 识别不可用")

            logger.error("[Audio] 所有识别引擎均失败")
            return ""

        except Exception as e:
            logger.warning(f"[Audio] 音频处理失败: {e}")
            return ""

    # ========================================================
    # 识别引擎: faster-whisper (主引擎)
    # ========================================================
    def _recognize_with_whisper(self, audio_path: str) -> str:
        """
        使用 faster-whisper 识别音频
        直接读取 MP3, 无需格式转换
        """
        try:
            model = WhisperModelManager().get_model()
            if model is None:
                logger.warning("[Whisper] 模型未加载")
                return ""

            logger.info(f"[Whisper] 开始识别: {audio_path}")
            segments, info = model.transcribe(
                audio_path,
                beam_size=config.WHISPER_BEAM_SIZE,
                language=config.WHISPER_LANGUAGE,
            )

            text = " ".join(seg.text.strip() for seg in segments)
            logger.info(f"[Whisper] 原始识别结果: '{text}'")
            return text

        except Exception as e:
            logger.warning(f"[Whisper] 识别失败: {e}")
            return ""

    # ========================================================
    # 识别引擎: Google Speech Recognition (备选引擎)
    # ========================================================
    def _recognize_with_google(self, wav_path: str) -> str:
        """
        使用 Google Speech Recognition 识别 WAV 音频
        (备选引擎, 精度较低)
        """
        try:
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)

            text = recognizer.recognize_google(audio_data)
            logger.info(f"[Google] 识别结果: '{text}'")
            return text

        except sr.UnknownValueError:
            logger.warning("[Google] 无法理解音频内容")
            return ""
        except sr.RequestError as e:
            logger.warning(f"[Google] 服务错误: {e}")
            return ""
        except Exception as e:
            logger.warning(f"[Google] 识别失败: {e}")
            return ""

    # ========================================================
    # 音频格式转换 (仅 Google 备选引擎需要)
    # ========================================================
    def _convert_mp3_to_wav(self, audio_path: str, wav_path: str) -> bool:
        """
        将 MP3 转换为 WAV (仅 Google 备选引擎需要)
        使用 pydub (依赖 ffmpeg)
        """
        if not HAS_PYDUB:
            logger.warning("[Audio] pydub 未安装, 无法转换 MP3→WAV")
            return False

        try:
            audio = AudioSegment.from_mp3(audio_path)
            audio.export(wav_path, format="wav")
            logger.info(f"[Audio] 已转换为 WAV: {wav_path}")
            return True
        except Exception as e:
            logger.warning(f"[Audio] pydub 转换失败: {e}")
            return False

    # ========================================================
    # 数字提取 (增强版)
    # ========================================================
    def _extract_digits(self, text: str) -> str:
        """
        从识别文本中提取数字
        支持: "one two three" / "1 2 3" / "first second third" / 混合形式
        """
        if not text:
            return ""

        # 完整词 → 数字
        word_map = {
            "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
            "first": "1", "second": "2", "third": "3", "fourth": "4",
            "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
            "ninth": "9",
        }

        digits = ""
        # 按空格和标点分词
        tokens = re.split(r"[\s,;.]+", text.strip().lower())

        for token in tokens:
            # 直接是数字
            if token.isdigit():
                digits += token
            # 英文单词 → 数字
            elif token in word_map:
                digits += word_map[token]
            # 可能包含数字的混合词 (如 "1st", "2nd")
            else:
                num_match = re.search(r"\d+", token)
                if num_match:
                    digits += num_match.group()

        return digits
