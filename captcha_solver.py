"""
reCAPTCHA v2 验证码求解模块
支持三种求解方式:
  1. 2captcha  API 服务
  2. CapSolver API 服务
  3. Audio     音频识别 (免费, 需要配合浏览器)
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)


# ============================================================
# 2captcha 求解器
# ============================================================
class TwoCaptchaSolver:
    """使用 2captcha API 求解 reCAPTCHA v2"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.submit_url = "https://2captcha.com/in.php"
        self.result_url = "https://2captcha.com/res.php"

    def solve(self, sitekey: str, page_url: str, timeout: int = 120) -> str:
        """
        提交 reCAPTCHA v2 任务并等待结果
        返回 g-recaptcha-response token
        """
        logger.info("[2captcha] 提交 reCAPTCHA v2 求解任务...")

        # 提交任务
        resp = requests.post(
            self.submit_url,
            data={
                "key": self.api_key,
                "method": "userrecaptcha",
                "googlekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=30,
        )
        data = resp.json()

        if data.get("status") != 1:
            raise RuntimeError(f"[2captcha] 提交失败: {data.get('request')}")

        task_id = data["request"]
        logger.info(f"[2captcha] 任务已提交, ID={task_id}, 等待求解...")

        # 轮询结果
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(5)
            resp = requests.get(
                self.result_url,
                params={
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                },
                timeout=30,
            )
            data = resp.json()

            if data.get("status") == 1:
                token = data["request"]
                logger.info(f"[2captcha] 求解成功! token 长度={len(token)}")
                return token
            elif data.get("request") == "CAPCHA_NOT_READY":
                logger.debug("[2captcha] 求解中, 继续等待...")
                continue
            else:
                raise RuntimeError(f"[2captcha] 求解失败: {data.get('request')}")

        raise TimeoutError(f"[2captcha] 求解超时 ({timeout}s)")


# ============================================================
# CapSolver 求解器
# ============================================================
class CapSolverSolver:
    """使用 CapSolver API 求解 reCAPTCHA v2"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.create_url = "https://api.capsolver.com/createTask"
        self.result_url = "https://api.capsolver.com/getTaskResult"

    def solve(self, sitekey: str, page_url: str, timeout: int = 120) -> str:
        """
        提交 reCAPTCHA v2 任务并等待结果
        返回 g-recaptcha-response token
        """
        logger.info("[CapSolver] 提交 reCAPTCHA v2 求解任务...")

        # 创建任务
        resp = requests.post(
            self.create_url,
            json={
                "clientKey": self.api_key,
                "task": {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            },
            timeout=30,
        )
        data = resp.json()

        if data.get("errorId") != 0:
            raise RuntimeError(f"[CapSolver] 创建任务失败: {data.get('errorDescription')}")

        task_id = data["taskId"]
        logger.info(f"[CapSolver] 任务已提交, ID={task_id}, 等待求解...")

        # 轮询结果
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(3)
            resp = requests.post(
                self.result_url,
                json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                },
                timeout=30,
            )
            data = resp.json()

            status = data.get("status", "")
            if status == "ready":
                token = data["solution"]["gRecaptchaResponse"]
                logger.info(f"[CapSolver] 求解成功! token 长度={len(token)}")
                return token
            elif status == "processing":
                logger.debug("[CapSolver] 求解中, 继续等待...")
                continue
            else:
                raise RuntimeError(f"[CapSolver] 求解失败: {data.get('errorDescription')}")

        raise TimeoutError(f"[CapSolver] 求解超时 ({timeout}s)")


# ============================================================
# 统一入口
# ============================================================
def solve_recaptcha(method: str, sitekey: str, page_url: str, twocaptcha_key: str = "", capsolver_key: str = "") -> str:
    """
    根据 method 选择求解器, 返回 reCAPTCHA token
    method: "2captcha" | "capsolver"
    """
    if method == "2captcha":
        solver = TwoCaptchaSolver(twocaptcha_key)
    elif method == "capsolver":
        solver = CapSolverSolver(capsolver_key)
    else:
        raise ValueError(f"不支持的求解方式: {method}")

    return solver.solve(sitekey, page_url)
