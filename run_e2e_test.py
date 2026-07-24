"""
三条Runtime完整E2E测试
======================
顺序运行 Native / Audio / Stealth 三条runtime的完整流程:
  init_browser -> navigate_to_target -> extract_sitekey ->
  solve_recaptcha -> inject_token_and_submit -> verify_result

每条runtime之间清理Chrome进程, 确保独立环境。
每步骤记录成功/失败, 生成详细报告。

运行方式:
  python run_e2e_test.py              # 运行全部三条
  python run_e2e_test.py native       # 仅 Native
  python run_e2e_test.py audio stealth # 运行指定runtime
"""

import asyncio
import logging
import os
import sys
import time
import subprocess
import platform
import json

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import config

# 截图与报告目录
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots")
REPORT_DIR = os.path.join(SCREENSHOT_DIR, "e2e_reports")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# 配置日志: console + 统一日志文件
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(SCREENSHOT_DIR, "e2e_test.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("E2ETest")


# ============================================================
# Chrome 进程清理
# ============================================================
def kill_all_chrome():
    """终止所有Chrome进程"""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=10,
            )
        else:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True, timeout=10)
        logger.info("[Cleanup] 已终止所有Chrome进程")
    except Exception as e:
        logger.warning(f"[Cleanup] 终止Chrome失败: {e}")


def cleanup_chrome_locks():
    """清理Chrome profile残留锁文件"""
    if platform.system() != "Windows":
        return
    local_appdata = os.environ.get("LocalAppData", "")
    user_data_dir = os.path.join(local_appdata, "Google", "Chrome", "User Data")
    if not os.path.exists(user_data_dir):
        return
    default_dir = os.path.join(user_data_dir, "Default")
    cleaned = 0
    for dirpath in [user_data_dir, default_dir]:
        if not os.path.exists(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if fname.startswith("LOCK"):
                fpath = os.path.join(dirpath, fname)
                try:
                    os.remove(fpath)
                    cleaned += 1
                except Exception:
                    pass
    if cleaned > 0:
        logger.info(f"[Cleanup] 已清理 {cleaned} 个Chrome锁文件")


def full_chrome_cleanup():
    """完整的Chrome清理: kill + 等待 + 锁文件清理"""
    kill_all_chrome()
    time.sleep(8)
    cleanup_chrome_locks()
    time.sleep(2)
    logger.info("[Cleanup] Chrome环境清理完成")


# ============================================================
# 步骤追踪器
# ============================================================
class StepTracker:
    """追踪E2E流程每一步的成功/失败/耗时"""

    STEPS = [
        "init_browser",
        "navigate_to_target",
        "extract_sitekey",
        "solve_recaptcha",
        "inject_token_and_submit",
        "verify_result",
    ]

    def __init__(self, runtime_name: str):
        self.runtime_name = runtime_name
        self.results = {}
        self.start_time = time.perf_counter()

    def record(self, step: str, success: bool, duration: float, detail: str = ""):
        self.results[step] = (success, duration, detail)
        status = "✅" if success else "❌"
        logger.info(
            f"[{self.runtime_name}] {status} {step}: "
            f"{'成功' if success else '失败'} ({duration:.1f}s) {detail}"
        )

    def summary(self) -> dict:
        total = time.perf_counter() - self.start_time
        passed = sum(1 for v in self.results.values() if v[0])
        all_passed = all(v[0] for v in self.results.values()) if self.results else False
        return {
            "runtime": self.runtime_name,
            "all_passed": all_passed,
            "steps_passed": f"{passed}/{len(self.STEPS)}",
            "total_duration": f"{total:.1f}s",
            "steps": {
                k: {"success": v[0], "duration": f"{v[1]:.1f}s", "detail": v[2]}
                for k, v in self.results.items()
            },
        }


# ============================================================
# 调试信息保存
# ============================================================
async def save_debug_info(runtime, runtime_name: str, step: str):
    """保存调试信息: 截图 + 页面HTML + URL/标题"""
    debug_dir = os.path.join(SCREENSHOT_DIR, runtime_name, "debug")
    os.makedirs(debug_dir, exist_ok=True)
    timestamp = time.strftime("%H%M%S")

    try:
        screenshot_path = os.path.join(debug_dir, f"{step}_{timestamp}.png")
        await runtime.page.screenshot(path=screenshot_path, animations="disabled")
        logger.info(f"[{runtime_name}] 调试截图: {screenshot_path}")
    except Exception:
        pass

    try:
        html = await runtime.page.content()
        html_path = os.path.join(debug_dir, f"{step}_{timestamp}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass

    try:
        logger.info(f"[{runtime_name}] 当前URL: {runtime.page.url}")
        logger.info(f"[{runtime_name}] 当前标题: {await runtime.page.title()}")
    except Exception:
        pass


# ============================================================
# 单runtime E2E测试
# ============================================================
async def test_runtime_e2e(runtime_name: str, tracker: StepTracker) -> bool:
    """
    执行单条runtime的完整E2E流程 (分步调用, 记录每步结果)

    与 runtime.run() 的区别:
    - run() 在异常时直接return False, 无法知道哪一步失败
    - 本函数分步调用, 每步独立try/except, 精确记录失败位置
    - 设置 _keep_browser_open=False, 避免finally块阻塞
    """
    # 动态导入对应的runtime类
    if runtime_name == "native":
        from runtimes.runtime_native import NativeRuntime
        runtime = NativeRuntime()
    elif runtime_name == "audio":
        from runtimes.runtime_audio import AudioRuntime
        runtime = AudioRuntime()
    elif runtime_name == "stealth":
        from runtimes.runtime_stealth import StealthRuntime
        runtime = StealthRuntime()
    elif runtime_name == "image":
        from runtimes.runtime_image import ImageRuntime
        runtime = ImageRuntime()
    else:
        logger.error(f"未知runtime: {runtime_name}")
        return False

    # 关键: 设为False, 避免run()的finally块 while True 阻塞
    runtime._keep_browser_open = False

    # 设置独立的截图目录 (避免三条runtime截图互相覆盖)
    runtime.screenshot_dir = os.path.join(SCREENSHOT_DIR, runtime_name)
    os.makedirs(runtime.screenshot_dir, exist_ok=True)

    sitekey = None
    page_url = None
    token = None

    try:
        # Step 1: init_browser
        t0 = time.perf_counter()
        try:
            await runtime.init_browser()
            tracker.record("init_browser", True, time.perf_counter() - t0,
                          "浏览器已初始化")
        except Exception as e:
            tracker.record("init_browser", False, time.perf_counter() - t0, str(e))
            return False

        # Step 2: navigate_to_target
        t0 = time.perf_counter()
        try:
            await runtime.navigate_to_target()
            username_count = await runtime.page.locator("#username").count()
            if username_count > 0:
                tracker.record("navigate_to_target", True, time.perf_counter() - t0,
                              "已到达登录页 (#username)")
            else:
                tracker.record("navigate_to_target", False, time.perf_counter() - t0,
                              "未找到 #username")
                await save_debug_info(runtime, runtime_name, "navigate_failed")
                return False
        except Exception as e:
            tracker.record("navigate_to_target", False, time.perf_counter() - t0, str(e))
            await save_debug_info(runtime, runtime_name, "navigate_exception")
            return False

        # Step 3: extract_sitekey
        t0 = time.perf_counter()
        try:
            sitekey = await runtime.extract_sitekey()
            page_url = runtime.page.url
            tracker.record("extract_sitekey", True, time.perf_counter() - t0,
                          f"sitekey={sitekey[:20]}...")
        except Exception as e:
            tracker.record("extract_sitekey", False, time.perf_counter() - t0, str(e))
            return False

        # Step 4: solve_recaptcha
        t0 = time.perf_counter()
        solve_failed = False
        try:
            token = await runtime.solve_recaptcha(sitekey, page_url)
            detail = "浏览器内通过" if token is None else f"token={token[:20]}..."
            tracker.record("solve_recaptcha", True, time.perf_counter() - t0, detail)
        except Exception as e:
            tracker.record("solve_recaptcha", False, time.perf_counter() - t0, str(e))
            await save_debug_info(runtime, runtime_name, "solve_failed")
            token = None
            solve_failed = True

        # Step 5: inject_token_and_submit
        # 如果 solve_recaptcha 失败, 跳过表单提交 (避免触发频率限制)
        if solve_failed:
            tracker.record("inject_token_and_submit", False, 0.0,
                          "跳过 (solve_recaptcha 失败)")
            tracker.record("verify_result", False, 0.0,
                          "跳过 (solve_recaptcha 失败)")
            return False

        t0 = time.perf_counter()
        try:
            await runtime.inject_token_and_submit(token)
            tracker.record("inject_token_and_submit", True, time.perf_counter() - t0,
                          "表单已提交")
        except Exception as e:
            tracker.record("inject_token_and_submit", False, time.perf_counter() - t0, str(e))
            await save_debug_info(runtime, runtime_name, "submit_failed")
            return False

        # Step 6: verify_result
        t0 = time.perf_counter()
        try:
            success = await runtime.verify_result()
            title = await runtime.page.title()
            url = runtime.page.url
            tracker.record("verify_result", success, time.perf_counter() - t0,
                          f"title='{title}', url={url[:60]}")
            if not success:
                await save_debug_info(runtime, runtime_name, "verify_failed")
            return success
        except Exception as e:
            tracker.record("verify_result", False, time.perf_counter() - t0, str(e))
            await save_debug_info(runtime, runtime_name, "verify_exception")
            return False

    finally:
        try:
            await runtime.close()
        except Exception as e:
            logger.warning(f"[{runtime_name}] close()异常: {e}")


# ============================================================
# 主函数
# ============================================================
async def main():
    # 解析命令行参数
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    valid_runtimes = {"native", "audio", "stealth", "image"}
    selected = [a.lower() for a in args if a.lower() in valid_runtimes]
    if not selected:
        selected = ["native", "audio", "stealth"]

    logger.info("=" * 70)
    logger.info("  三条Runtime完整E2E测试")
    logger.info(f"  运行顺序: {' -> '.join(selected)}")
    logger.info(f"  账号: {config.ACCOUNT_EMAIL}")
    logger.info("=" * 70)

    all_results = []

    for i, runtime_name in enumerate(selected):
        logger.info("")
        logger.info("#" * 70)
        logger.info(f"#  [{i+1}/{len(selected)}] {runtime_name.upper()} E2E测试")
        logger.info("#" * 70)

        # 运行前清理Chrome (第一条也清理, 确保干净起点)
        logger.info(f"[{runtime_name}] 运行前Chrome清理...")
        full_chrome_cleanup()

        tracker = StepTracker(runtime_name)
        try:
            success = await test_runtime_e2e(runtime_name, tracker)
        except Exception as e:
            logger.error(f"[{runtime_name}] E2E测试异常: {e}", exc_info=True)
            success = False

        result = tracker.summary()
        result["overall_success"] = success
        all_results.append(result)

        # 运行后清理Chrome
        logger.info(f"[{runtime_name}] 运行后Chrome清理...")
        full_chrome_cleanup()

    # 生成汇总报告
    report_path = os.path.join(
        REPORT_DIR, f"e2e_report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # 打印汇总表
    logger.info("")
    logger.info("=" * 70)
    logger.info("  E2E测试结果汇总")
    logger.info("=" * 70)
    logger.info(f"  {'Runtime':<12} {'结果':<8} {'步骤':<10} {'耗时':<10}")
    logger.info("  " + "-" * 50)
    for r in all_results:
        status = "✅ 通过" if r["overall_success"] else "❌ 失败"
        logger.info(
            f"  {r['runtime']:<12} {status:<8} {r['steps_passed']:<10} {r['total_duration']:<10}"
        )

    # 打印每条runtime的步骤详情
    for r in all_results:
        logger.info("")
        logger.info(f"  --- {r['runtime'].upper()} 步骤详情 ---")
        for step_name in StepTracker.STEPS:
            if step_name in r["steps"]:
                step_data = r["steps"][step_name]
                icon = "✅" if step_data["success"] else "❌"
                logger.info(
                    f"    {icon} {step_name:<28} {step_data['duration']:<8} {step_data['detail']}"
                )
            else:
                logger.info(f"    -- {step_name:<28} {'--':<8} (未执行)")

    logger.info("")
    logger.info(f"  详细报告: {report_path}")
    logger.info("=" * 70)

    all_passed = all(r["overall_success"] for r in all_results)
    return all_passed


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n  已中断")
        sys.exit(0)
