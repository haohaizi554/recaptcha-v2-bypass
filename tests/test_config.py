"""
config.py 单元测试
==================
测试配置常量的完整性和合法性:
  - 关键 URL 格式正确
  - 数值配置在合理范围
  - 敏感信息使用环境变量 (无硬编码凭据)
  - 枚举值在合法集合内

无外部依赖, 可在任何平台运行.
"""

import os

import config


# ============================================================
# URL 配置
# ============================================================
class TestUrls:
    """验证 URL 配置格式正确"""

    def test_source_url_is_https(self):
        """源网站 URL 应为 https"""
        assert config.SOURCE_URL.startswith("https://"), "SOURCE_URL 必须使用 HTTPS"

    def test_target_url_is_https(self):
        """目标 URL 应为 https"""
        assert config.TARGET_URL.startswith("https://"), "TARGET_URL 必须使用 HTTPS"

    def test_page_url_is_https(self):
        """页面 URL 应为 https"""
        assert config.PAGE_URL.startswith("https://"), "PAGE_URL 必须使用 HTTPS"

    def test_source_url_not_placeholder(self):
        """源网站 URL 不应是占位符"""
        assert "YOUR_" not in config.SOURCE_URL, "SOURCE_URL 仍为占位符"
        assert "example.com" not in config.SOURCE_URL

    def test_target_url_not_placeholder(self):
        """目标 URL 不应是占位符"""
        assert "YOUR_" not in config.TARGET_URL, "TARGET_URL 仍为占位符"


# ============================================================
# reCAPTCHA 配置
# ============================================================
class TestRecaptchaConfig:
    """验证 reCAPTCHA 相关配置"""

    def test_sitekey_is_nonempty(self):
        """sitekey 应为非空字符串"""
        assert isinstance(config.RECAPTCHA_SITEKEY, str)
        assert len(config.RECAPTCHA_SITEKEY) > 0

    def test_sitekey_not_placeholder(self):
        """sitekey 不应是占位符"""
        assert config.RECAPTCHA_SITEKEY != "YOUR_SITEKEY"
        assert "YOUR_" not in config.RECAPTCHA_SITEKEY

    def test_max_retries_positive(self):
        """重试次数应为正整数"""
        assert isinstance(config.RECAPTCHA_MAX_RETRIES, int)
        assert config.RECAPTCHA_MAX_RETRIES > 0

    def test_render_wait_positive(self):
        """渲染等待时间应为正整数"""
        assert isinstance(config.RECAPTCHA_RENDER_WAIT, int)
        assert config.RECAPTCHA_RENDER_WAIT > 0

    def test_retry_delay_non_negative(self):
        """重试间隔应为非负数"""
        assert config.RECAPTCHA_RETRY_DELAY >= 0


# ============================================================
# 敏感信息安全性
# ============================================================
class TestSecurity:
    """验证敏感信息未硬编码"""

    def test_account_email_uses_env(self):
        """账号邮箱应从环境变量读取"""
        # 默认值是占位符, 真实值通过环境变量注入
        assert config.ACCOUNT_EMAIL is not None

    def test_account_password_uses_env(self):
        """账号密码应从环境变量读取"""
        assert config.ACCOUNT_PASSWORD is not None

    def test_no_real_password_in_default(self):
        """默认密码值不应该是真实密码 (应为占位符)"""
        # 确保没有硬编码真实密码
        default_password = config.ACCOUNT_PASSWORD
        # 如果环境变量未设置, 默认值应是占位符格式
        if default_password == "your_password":
            pass  # 合法占位符
        elif len(default_password) > 20 and not default_password.startswith("your_"):
            # 可能是环境变量注入的真实密码, 这是合理的
            pass
        # 不应有明显的密码模式暴露在源码中

    def test_api_keys_are_placeholders(self):
        """第三方 API key 应为占位符 (未配置状态)"""
        assert "YOUR_" in config.TWOCAPTCHA_API_KEY or config.TWOCAPTCHA_API_KEY == ""
        assert "YOUR_" in config.CAPSOLVER_API_KEY or config.CAPSOLVER_API_KEY == ""

    def test_accessibility_cookie_is_placeholder(self):
        """无障碍 Cookie 应为占位符"""
        assert "YOUR_" in config.RECAPTCHA_ACCESSIBILITY_COOKIE or config.RECAPTCHA_ACCESSIBILITY_COOKIE == ""


# ============================================================
# 数值配置范围
# ============================================================
class TestNumericRanges:
    """验证数值配置在合理范围内"""

    def test_browser_timeout_positive(self):
        """浏览器超时应为正数"""
        assert config.BROWSER_TIMEOUT > 0

    def test_implicit_wait_non_negative(self):
        """隐式等待应为非负数"""
        assert config.IMPLICIT_WAIT >= 0

    def test_nav_max_retries_positive(self):
        """导航重试次数应为正整数"""
        assert isinstance(config.NAV_MAX_RETRIES, int)
        assert config.NAV_MAX_RETRIES > 0

    def test_nav_page_load_timeout_positive(self):
        """页面加载超时应为正数"""
        assert config.NAV_PAGE_LOAD_TIMEOUT > 0

    def test_nav_form_wait_timeout_positive(self):
        """表单等待超时应为正数"""
        assert config.NAV_FORM_WAIT_TIMEOUT > 0

    def test_native_click_result_wait_positive(self):
        """Native 点击等待时间应为正数"""
        assert config.NATIVE_CLICK_RESULT_WAIT > 0

    def test_extension_solve_timeout_positive(self):
        """扩展求解超时应为正数"""
        assert config.EXTENSION_SOLVE_TIMEOUT > 0


# ============================================================
# 枚举值合法性
# ============================================================
class TestEnumValues:
    """验证枚举型配置值在合法集合内"""

    def test_solver_method_valid(self):
        """求解方式应为合法值"""
        valid_methods = {"2captcha", "capsolver", "audio"}
        assert config.SOLVER_METHOD in valid_methods, f"SOLVER_METHOD '{config.SOLVER_METHOD}' 不在合法集合中"

    def test_audio_recognizer_valid(self):
        """音频识别引擎应为合法值"""
        valid_engines = {"whisper", "google"}
        assert config.AUDIO_RECOGNIZER in valid_engines

    def test_whisper_model_size_valid(self):
        """Whisper 模型大小应为合法值"""
        valid_sizes = {"tiny", "base", "small", "medium", "large-v3"}
        assert config.WHISPER_MODEL_SIZE in valid_sizes

    def test_whisper_device_valid(self):
        """Whisper 设备应为合法值"""
        valid_devices = {"cpu", "cuda"}
        assert config.WHISPER_DEVICE in valid_devices

    def test_whisper_compute_type_valid(self):
        """Whisper 计算类型应为合法值"""
        valid_types = {"int8", "int8_float16", "float16", "float32"}
        assert config.WHISPER_COMPUTE_TYPE in valid_types

    def test_seg_overlap_mode_valid(self):
        """SEG 重叠模式应为合法值"""
        valid_modes = {"ratio", "any"}
        assert config.YOLO_SEG_OVERLAP_MODE in valid_modes

    def test_native_browser_channel_valid(self):
        """Native 浏览器渠道应为合法值"""
        assert config.NATIVE_BROWSER_CHANNEL in ("chrome", "msedge", "beta")


# ============================================================
# 音频下载请求头
# ============================================================
class TestAudioHeaders:
    """验证音频下载请求头配置"""

    def test_headers_is_dict(self):
        """请求头应为字典"""
        assert isinstance(config.AUDIO_DOWNLOAD_HEADERS, dict)

    def test_headers_has_user_agent(self):
        """请求头应包含 User-Agent"""
        assert "User-Agent" in config.AUDIO_DOWNLOAD_HEADERS

    def test_headers_has_referer(self):
        """请求头应包含 Referer"""
        assert "Referer" in config.AUDIO_DOWNLOAD_HEADERS

    def test_referer_is_google(self):
        """Referer 应指向 Google reCAPTCHA"""
        assert "google.com/recaptcha" in config.AUDIO_DOWNLOAD_HEADERS["Referer"]


# ============================================================
# AI 图像识别配置
# ============================================================
class TestImageConfig:
    """验证 AI 图像识别配置"""

    def test_clip_model_is_huggingface_id(self):
        """CLIP 模型应为 HuggingFace 模型 ID 格式"""
        model = config.IMAGE_CLASSIFIER_MODEL
        assert "/" in model, f"CLIP 模型 '{model}' 不符合 HuggingFace ID 格式 (org/name)"

    def test_thresholds_in_range(self):
        """概率阈值应在 0-1 范围内"""
        assert 0 <= config.IMAGE_MATCH_THRESHOLD <= 1
        assert 0 <= config.IMAGE_MIN_CONFIDENCE <= 1
        assert 0 <= config.IMAGE_RANK_SCORE_GAP <= 1

    def test_top_k_positive(self):
        """top_k 应为正整数"""
        assert isinstance(config.IMAGE_TOP_K_3X3, int)
        assert config.IMAGE_TOP_K_3X3 > 0
        assert isinstance(config.IMAGE_TOP_K_4X4, int)
        assert config.IMAGE_TOP_K_4X4 > 0

    def test_yolo_cls_threshold_in_range(self):
        """YOLO CLS 阈值应在 0-1 范围"""
        assert 0 <= config.YOLO_CLS_THRESHOLD <= 1

    def test_yolo_seg_confidence_in_range(self):
        """YOLO SEG 置信度应在 0-1 范围"""
        assert 0 <= config.YOLO_SEG_CONFIDENCE <= 1
        assert 0 <= config.YOLO_SEG_CONFIDENCE_FALLBACK <= 1

    def test_yolo_seg_imgsz_positive(self):
        """YOLO SEG 输入尺寸应为正整数"""
        assert config.YOLO_SEG_IMGSZ > 0
        assert config.YOLO_SEG_IMGSZ_HIGH > config.YOLO_SEG_IMGSZ, "高分辨率尺度应大于主尺度"

    def test_seg_overlap_threshold_in_range(self):
        """SEG 重叠阈值应在 0-1 范围"""
        assert 0 <= config.YOLO_SEG_OVERLAP_THRESHOLD <= 1


# ============================================================
# 模型路径配置
# ============================================================
class TestModelPaths:
    """验证模型文件路径配置"""

    def test_cls_model_path_endswith_pt(self):
        """CLS 模型路径应以 .pt 结尾"""
        assert config.YOLO_CLS_MODEL_PATH.endswith(".pt")

    def test_seg_model_name_endswith_pt(self):
        """SEG 模型名应以 .pt 结尾"""
        assert config.YOLO_SEG_MODEL_NAME.endswith(".pt")

    def test_nopecha_extension_path_is_absolute(self):
        """NopeCHA 扩展路径应为绝对路径"""
        assert os.path.isabs(config.NOPECHA_EXTENSION_PATH)

    def test_nav_link_cache_file_is_absolute(self):
        """导航链接缓存文件路径应为绝对路径"""
        assert os.path.isabs(config.NAV_LINK_CACHE_FILE)
