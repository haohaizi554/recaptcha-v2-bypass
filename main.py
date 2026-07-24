"""
reCAPTCHA v2 自动化绕过工具 - 统一入口
======================================
支持多种启动模式:
  python main.py              → 默认启动 GUI
  python main.py --gui        → 启动 GUI
  python main.py --cli        → 启动 CLI 交互菜单
  python main.py -m <key>     → 直接启动指定方案 (跳过菜单)
  python main.py --check      → 环境依赖检查
  python main.py --list       → 列出所有方案
  python main.py --version    → 显示版本信息

方案 key: audio / api / image / cookie / extension / native
"""

import argparse
import asyncio
import logging
import os
import platform
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from solutions import (
    SOLUTION_COUNT,
    SOLUTIONS,
    check_solution_deps,
    get_solution,
)

# ============================================================
# 版本信息
# ============================================================
__version__ = "2.0.0"
__app_name__ = "reCAPTCHA v2 自动化绕过工具"

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Main")


# ============================================================
# CLI 渲染
# ============================================================
def _print_banner():
    """打印程序横幅"""
    print()
    print("=" * 62)
    print(f"  {__app_name__} v{__version__}")
    print(f"  ApplyKitty 面试题 · {SOLUTION_COUNT} 种绕过方案")
    print("=" * 62)
    print()


def _print_solutions():
    """打印方案列表"""
    print("  可选方案:")
    print()
    for i, sol in enumerate(SOLUTIONS, 1):
        cost_tag = sol["cost"]
        status = sol["status"]
        print(f"  [{i}] {sol['cli_icon']} {sol['name']}")
        print(f"      {sol['short_desc']}")
        print(f"      费用: {cost_tag} | 状态: {status}")
        print()
    print("  [0] 退出")
    print()


def _print_separator():
    """打印分隔线"""
    print("-" * 62)


def _print_env_info():
    """打印环境信息"""
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  平台:     {platform.system()} {platform.release()}")
    print(f"  工作目录: {os.path.dirname(os.path.abspath(__file__))}")
    print()


# ============================================================
# 环境检查
# ============================================================
def run_check():
    """环境依赖检查"""
    _print_banner()
    print("  环境依赖检查")
    _print_separator()
    print()
    _print_env_info()

    all_ok = True
    for sol in SOLUTIONS:
        key = sol["key"]
        ok, missing = check_solution_deps(key)
        status_icon = "✓" if ok else "✗"
        print(f"  {status_icon} {sol['name']} ({key})")
        if ok:
            print(f"      依赖完整: {', '.join(sol.get('deps', []))}")
        else:
            print(f"      缺少: {', '.join(missing)}")
            print(f"      安装: pip install {' '.join(missing)}")
            all_ok = False
        print()

    _print_separator()
    if all_ok:
        print("  ✓ 所有方案依赖完整, 可以使用全部功能")
    else:
        print("  ✗ 部分方案缺少依赖, 请按提示安装")
    print()
    return all_ok


def run_list():
    """列出所有方案"""
    _print_banner()
    print("  方案列表:")
    print()
    for _i, sol in enumerate(SOLUTIONS, 1):
        print(f"  {sol['cli_icon']} {sol['key']:12s} {sol['name']}")
        print(f"                {sol['short_desc']}")
        print(f"                费用: {sol['cost']} | 状态: {sol['status']}")
        print()
    return 0


# ============================================================
# 方案启动器
# ============================================================
def _launch_audio():
    from runtimes.runtime_audio import AudioRuntime

    return AudioRuntime()


def _launch_api():
    print()
    print("  --- API 代解服务选择 ---")
    print()
    print("  [1] 2captcha  (~$2.99/1000次)")
    print("  [2] CapSolver (~$0.80/1000次)")
    print("  [0] 返回主菜单")
    print()

    choice = input("  请选择 (0-2): ").strip()
    if choice == "0":
        return None

    provider_map = {"1": "2captcha", "2": "capsolver"}
    provider = provider_map.get(choice)
    if not provider:
        print("  无效选择")
        return None

    from runtimes.runtime_api import APIRuntime

    return APIRuntime(provider=provider)


def _launch_image():
    from runtimes.runtime_image import ImageRuntime

    return ImageRuntime()


def _launch_cookie():
    print()
    print("  --- 无障碍 Cookie 配置 ---")
    print()
    print("  获取方式: 访问 https://www.google.com/recaptcha/admin/accessibility")
    print("  注册后在浏览器 Cookie 中找到 'recaptcha-accessibility-cookie' 的值")
    print()

    default_cookie = config.RECAPTCHA_ACCESSIBILITY_COOKIE
    if default_cookie and "YOUR_" not in default_cookie:
        use_default = input("  使用 config.py 中的 cookie? (Y/n): ").strip().lower()
        if use_default != "n":
            from runtimes.runtime_cookie import CookieRuntime

            return CookieRuntime()

    cookie_value = input("  请输入 cookie 值 (或按回车返回): ").strip()
    if not cookie_value:
        print("  未输入 cookie, 返回主菜单")
        return None

    from runtimes.runtime_cookie import CookieRuntime

    return CookieRuntime(cookie_value=cookie_value)


def _launch_extension():
    ext_path = config.NOPECHA_EXTENSION_PATH
    print()
    print("  --- NopeCHA 扩展配置 ---")
    print()

    if not os.path.isdir(ext_path):
        print(f"  扩展目录不存在: {ext_path}")
        print()
        print("  请下载 NopeCHA 扩展并解压:")
        print("  Chrome Web Store ID: dknliebolcfipdbfhohdchdbmldibjco")
        print(f"  解压到: {ext_path}")
        print()
        custom_path = input("  输入自定义扩展路径 (或按回车返回): ").strip()
        if not custom_path or not os.path.isdir(custom_path):
            print("  无效路径, 返回主菜单")
            return None
        ext_path = custom_path

    from runtimes.runtime_extension import ExtensionRuntime

    return ExtensionRuntime(extension_path=ext_path)


def _launch_native():
    from runtimes.runtime_native import NativeRuntime

    return NativeRuntime()


# key → 启动函数映射
LAUNCHERS = {
    "audio": _launch_audio,
    "api": _launch_api,
    "image": _launch_image,
    "cookie": _launch_cookie,
    "extension": _launch_extension,
    "native": _launch_native,
}


def _run_runtime(runtime):
    """运行 runtime 并返回结果"""
    _print_separator()
    print(f"  启动方案: {runtime.method_desc}")
    _print_separator()
    print()

    result = asyncio.run(runtime.run())

    if result:
        print()
        print("  [OK] reCAPTCHA 绕过成功!")
    else:
        print()
        print("  [FAIL] reCAPTCHA 绕过失败")
    return result


# ============================================================
# CLI 交互模式
# ============================================================
def run_cli():
    """CLI 交互菜单模式"""
    while True:
        _print_banner()
        _print_solutions()

        choice = input(f"  请输入选择 (0-{SOLUTION_COUNT}): ").strip()

        if choice == "0":
            print()
            print("  再见!")
            print()
            break

        # 数字 → key
        try:
            idx = int(choice) - 1
            if 0 <= idx < SOLUTION_COUNT:
                key = SOLUTIONS[idx]["key"]
            else:
                raise ValueError
        except ValueError:
            # 也支持直接输入 key
            key = choice if choice in LAUNCHERS else None

        if key is None or key not in LAUNCHERS:
            print()
            print("  无效选择, 请重新输入")
            input("  按回车继续...")
            continue

        _print_separator()
        print()

        # 依赖检查
        ok, missing = check_solution_deps(key)
        if not ok:
            print(f"  [警告] 方案缺少依赖: {', '.join(missing)}")
            print(f"  请运行: pip install {' '.join(missing)}")
            print()
            cont = input("  仍要继续? (y/N): ").strip().lower()
            if cont != "y":
                continue

        try:
            runtime = LAUNCHERS[key]()
            if runtime is None:
                continue
            _run_runtime(runtime)
        except KeyboardInterrupt:
            print()
            print("  用户中断")
        except Exception as e:
            print()
            print(f"  [ERROR] {e}")
            logger.error(f"运行异常: {e}", exc_info=True)

        print()
        input("  按回车返回主菜单...")
        print()

    return 0


def run_method(key: str):
    """直接启动指定方案 (跳过菜单)"""
    sol = get_solution(key)
    if sol is None:
        print(f"  [错误] 未知方案: {key}")
        print(f"  可用方案: {', '.join(s['key'] for s in SOLUTIONS)}")
        return 1

    _print_banner()
    print(f"  直接启动: {sol['name']} ({key})")
    print(f"  {sol['short_desc']}")
    print()

    # 依赖检查
    ok, missing = check_solution_deps(key)
    if not ok:
        print(f"  [警告] 缺少依赖: {', '.join(missing)}")
        print(f"  请运行: pip install {' '.join(missing)}")
        print()
        cont = input("  仍要继续? (y/N): ").strip().lower()
        if cont != "y":
            return 1

    try:
        runtime = LAUNCHERS[key]()
        if runtime is None:
            return 1
        _run_runtime(runtime)
    except KeyboardInterrupt:
        print()
        print("  用户中断")
    except Exception as e:
        print()
        print(f"  [ERROR] {e}")
        logger.error(f"运行异常: {e}", exc_info=True)
        return 1

    return 0


# ============================================================
# GUI 模式
# ============================================================
def run_gui():
    """启动 GUI 模式"""
    try:
        from PyQt6.QtGui import QFont, QIcon
        from PyQt6.QtWidgets import QApplication

        import gui as gui_module
    except ImportError as e:
        print(f"  [错误] GUI 依赖缺失: {e}")
        print("  请安装: pip install PyQt6")
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("reCAPTCHA Bypass Tool")
    app.setApplicationVersion(__version__)

    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    app.setWindowIcon(QIcon(gui_module._icon_pixmap("shield", 64)))

    window = gui_module.MainWindow()
    window.show()
    return app.exec()


# ============================================================
# 参数解析
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="recaptcha-bypass",
        description=f"{__app_name__} v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "启动模式:\n"
            "  python main.py              默认启动 GUI\n"
            "  python main.py --cli        CLI 交互菜单\n"
            f"  python main.py -m <key>     直接启动方案 (key: {', '.join(s['key'] for s in SOLUTIONS)})\n"
            "  python main.py --check      环境依赖检查\n"
            "  python main.py --list       列出所有方案\n"
            "  python main.py --version    显示版本\n"
        ),
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="启动 CLI 交互菜单",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动 GUI (默认行为)",
    )
    parser.add_argument(
        "-m",
        "--method",
        metavar="KEY",
        choices=[s["key"] for s in SOLUTIONS],
        help=f"直接启动指定方案 (key: {', '.join(s['key'] for s in SOLUTIONS)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="环境依赖检查",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有方案",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{__app_name__} v{__version__}",
    )
    return parser


# ============================================================
# 主入口
# ============================================================
def main():
    """统一入口: 解析参数并分发到对应模式"""
    parser = build_parser()
    args = parser.parse_args()

    # --check: 环境检查
    if args.check:
        return 0 if run_check() else 1

    # --list: 列出方案
    if args.list:
        return run_list()

    # -m <key>: 直接启动方案
    if args.method:
        return run_method(args.method)

    # --cli: CLI 交互菜单
    if args.cli:
        return run_cli()

    # 默认 / --gui: GUI 模式
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
