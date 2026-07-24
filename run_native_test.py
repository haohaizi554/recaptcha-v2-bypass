"""
零 CDP 痕迹方案 端到端测试
=============================
测试 runtime_native.py 是否能避免触发 reCAPTCHA 图像挑战

核心验证点:
  1. patchright launch_persistent_context 从源头消除 CDP 痕迹
     - navigator.webdriver = undefined
     - 无 cdc_ 痕迹
  2. PyAutoGUI OS 级点击产生 isTrusted=true 事件
  3. reCAPTCHA checkbox 直接通过 (不触发图像挑战)
  4. 若触发挑战, Fallback 到 ImageRuntime (跳过 checkbox 重复点击)

运行方式:
  python run_native_test.py

注意:
  - 测试完成后浏览器保持打开 (Ctrl+C 退出)
  - 会自动关闭已运行的 Chrome 以释放真实 profile 锁
  - PyAutoGUI 点击需要 Chrome 窗口在前台 (请勿切换窗口)
"""

import asyncio
import logging
import os
import sys

# 添加项目根目录到路径
PROJECT_ROOT = r"d:\desktop\successfactor"
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from runtimes.runtime_native import NativeRuntime

# 确保截图目录存在
os.makedirs(os.path.join(PROJECT_ROOT, "screenshots"), exist_ok=True)

# 配置日志: console + file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(PROJECT_ROOT, "screenshots", "native_test.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("NativeTest")


async def main():
    logger.info("=" * 60)
    logger.info("  零 CDP 痕迹方案 端到端测试")
    logger.info("  patchright launch_persistent_context + PyAutoGUI OS 级点击")
    logger.info("  目标: 不触发 reCAPTCHA 图像挑战, 直接通过 checkbox")
    logger.info("=" * 60)

    runtime = NativeRuntime()
    # 保持浏览器打开: 测试完成后用户可手动检查页面
    # run() 的 finally 块会进入 while True 循环, Ctrl+C 退出
    runtime._keep_browser_open = True

    try:
        # 执行完整流程: init_browser → navigate → solve → inject → verify
        # run() 内部会调用所有步骤, 包括指纹诊断
        result = await runtime.run()

        if result:
            logger.info("=" * 60)
            logger.info("  >>> 零 CDP 痕迹方案验证通过! reCAPTCHA 已绕过")
            logger.info("=" * 60)
        else:
            logger.error("=" * 60)
            logger.error("  >>> 零 CDP 痕迹方案验证失败")
            if runtime._challenge_detected:
                logger.error("  >>> 图像挑战被触发 (可能需要配合住宅代理 IP)")
            logger.error("=" * 60)

        return result

    except KeyboardInterrupt:
        logger.info(">>> 用户中断 (Ctrl+C), 浏览器保持打开")
        return False
    except Exception as e:
        logger.error(f">>> 测试异常: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        # asyncio.run 内部的 KeyboardInterrupt 已经处理
        # 这里捕获外层可能的重复中断
        print("\n  已退出, 浏览器可能仍保持打开")
        sys.exit(0)
