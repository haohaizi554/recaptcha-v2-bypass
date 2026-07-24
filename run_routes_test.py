"""
三条路径导航测试
================
1. Base 路径: Atos源网站 → Apply now → 登录链接 → 登录页 (#username)
2. Native 路径: 直接访问 → profileWidget登入 → 登录页 (#username)
3. Stealth 路径: 会话预热 → 父类导航 → 登录页 (#username)

每条路径仅测试导航阶段, 到达 #username 即算成功.
失败时保存当前页面 HTML 和截图供调试.

运行方式:
  python run_routes_test.py              # 测试全部三条路径
  python run_routes_test.py native       # 仅测试 Native 路径
  python run_routes_test.py base native  # 测试指定路径
"""

import asyncio
import logging
import os
import sys
import time

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 确保截图目录存在
os.makedirs(os.path.join(PROJECT_ROOT, "screenshots"), exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(PROJECT_ROOT, "screenshots", "routes_test.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("RoutesTest")


async def test_native_route() -> bool:
    """
    测试 Native 路径: 直接访问 SuccessFactors → profileWidget 登入 → #username
    """
    logger.info("=" * 60)
    logger.info("  [1/3] Native 路径导航测试")
    logger.info("  patchright launch_persistent_context + profileWidget 登入")
    logger.info("=" * 60)

    try:
        from runtimes.runtime_native import NativeRuntime

        runtime = NativeRuntime()
        runtime._keep_browser_open = False  # 测试完自动关闭

        t0 = time.perf_counter()

        # Step 1: 初始化浏览器
        await runtime.init_browser()
        logger.info("[Native] 浏览器初始化完成")

        # Step 2: 导航到目标页面
        await runtime.navigate_to_target()
        logger.info("[Native] 导航完成")

        # Step 3: 验证是否到达登录页
        username_count = await runtime.page.locator("#username").count()
        if username_count > 0:
            elapsed = time.perf_counter() - t0
            logger.info(f"[Native] ✅ 导航成功! 已到达登录页 (#username), 耗时 {elapsed:.1f}s")
            await runtime.page.screenshot(
                path=os.path.join(PROJECT_ROOT, "screenshots", "route_native_login.png"),
                animations="disabled",
            )
            await runtime.close()
            return True
        else:
            logger.error("[Native] ❌ 导航失败: 未找到 #username")
            await _save_debug_info(runtime, "native")
            await runtime.close()
            return False

    except Exception as e:
        logger.error(f"[Native] ❌ 测试异常: {e}", exc_info=True)
        return False


async def test_base_route() -> bool:
    """
    测试 Base 路径: Atos 源网站 → Apply now → 登录链接 → #username
    使用 AudioRuntime (继承 BaseBypassRuntime 的导航逻辑)
    """
    logger.info("=" * 60)
    logger.info("  [2/3] Base 路径导航测试")
    logger.info("  Atos 源网站 → Apply now → 登录链接 → #username")
    logger.info("=" * 60)

    try:
        from runtimes.runtime_audio import AudioRuntime

        runtime = AudioRuntime()
        runtime._keep_browser_open = False

        t0 = time.perf_counter()

        # Step 1: 初始化浏览器
        await runtime.init_browser()
        logger.info("[Base] 浏览器初始化完成")

        # Step 2: 导航到目标页面
        await runtime.navigate_to_target()
        logger.info("[Base] 导航完成")

        # Step 3: 验证是否到达登录页
        username_count = await runtime.page.locator("#username").count()
        if username_count > 0:
            elapsed = time.perf_counter() - t0
            logger.info(f"[Base] ✅ 导航成功! 已到达登录页 (#username), 耗时 {elapsed:.1f}s")
            await runtime.page.screenshot(
                path=os.path.join(PROJECT_ROOT, "screenshots", "route_base_login.png"),
                animations="disabled",
            )
            await runtime.close()
            return True
        else:
            logger.error("[Base] ❌ 导航失败: 未找到 #username")
            await _save_debug_info(runtime, "base")
            await runtime.close()
            return False

    except Exception as e:
        logger.error(f"[Base] ❌ 测试异常: {e}", exc_info=True)
        return False


async def test_stealth_route() -> bool:
    """
    测试 Stealth 路径: 会话预热 → 父类导航 → #username
    """
    logger.info("=" * 60)
    logger.info("  [3/3] Stealth 路径导航测试")
    logger.info("  会话预热 → Atos/直接访问 → 登录链接 → #username")
    logger.info("=" * 60)

    try:
        from runtimes.runtime_stealth import StealthRuntime

        runtime = StealthRuntime()
        runtime._keep_browser_open = False

        t0 = time.perf_counter()

        # Step 1: 初始化浏览器
        await runtime.init_browser()
        logger.info("[Stealth] 浏览器初始化完成")

        # Step 2: 导航到目标页面 (含会话预热)
        await runtime.navigate_to_target()
        logger.info("[Stealth] 导航完成")

        # Step 3: 验证是否到达登录页
        username_count = await runtime.page.locator("#username").count()
        if username_count > 0:
            elapsed = time.perf_counter() - t0
            logger.info(f"[Stealth] ✅ 导航成功! 已到达登录页 (#username), 耗时 {elapsed:.1f}s")
            await runtime.page.screenshot(
                path=os.path.join(PROJECT_ROOT, "screenshots", "route_stealth_login.png"),
                animations="disabled",
            )
            await runtime.close()
            return True
        else:
            logger.error("[Stealth] ❌ 导航失败: 未找到 #username")
            await _save_debug_info(runtime, "stealth")
            await runtime.close()
            return False

    except Exception as e:
        logger.error(f"[Stealth] ❌ 测试异常: {e}", exc_info=True)
        return False


async def _save_debug_info(runtime, route_name: str):
    """保存调试信息: 截图 + 页面 HTML"""
    try:
        # 截图
        screenshot_path = os.path.join(PROJECT_ROOT, "screenshots", f"route_{route_name}_debug.png")
        await runtime.page.screenshot(path=screenshot_path, animations="disabled")
        logger.info(f"调试截图已保存: {screenshot_path}")

        # 页面 HTML
        html = await runtime.page.content()
        html_path = os.path.join(PROJECT_ROOT, "screenshots", f"route_{route_name}_debug.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"调试 HTML 已保存: {html_path}")

        # 当前 URL 和标题
        logger.info(f"当前 URL: {runtime.page.url}")
        logger.info(f"当前标题: {await runtime.page.title()}")

        # 检查所有登录选择器
        logger.info("--- 检查登录选择器匹配情况 ---")
        for sel in runtime.SIGN_IN_SELECTORS:
            try:
                count = await runtime.page.locator(sel).count()
                if count > 0:
                    logger.info(f"  ✅ '{sel}' 匹配到 {count} 个元素")
                else:
                    logger.info(f"  ❌ '{sel}' 未匹配")
            except Exception:
                logger.info(f"  ⚠️ '{sel}' 查询异常")

    except Exception as e:
        logger.warning(f"保存调试信息失败: {e}")


async def main():
    """主函数: 根据命令行参数选择测试路径"""
    # 解析命令行参数
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    valid_routes = {"native", "base", "stealth"}
    selected = [a.lower() for a in args if a.lower() in valid_routes]

    if not selected:
        selected = ["native", "base", "stealth"]  # 默认全部测试

    logger.info("=" * 60)
    logger.info(f"  三条路径导航测试 (测试: {', '.join(selected)})")
    logger.info(f"  目标: {os.getenv('TARGET_URL', 'career5.successfactors.eu')}")
    logger.info("=" * 60)

    results = {}

    if "native" in selected:
        results["native"] = await test_native_route()

    if "base" in selected:
        results["base"] = await test_base_route()

    if "stealth" in selected:
        results["stealth"] = await test_stealth_route()

    # 汇总结果
    logger.info("")
    logger.info("=" * 60)
    logger.info("  测试结果汇总")
    logger.info("=" * 60)
    for route, success in results.items():
        status = "✅ 通过" if success else "❌ 失败"
        logger.info(f"  {route:10s} : {status}")
    logger.info("=" * 60)

    all_pass = all(results.values())
    return all_pass


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n  已中断")
        sys.exit(0)
