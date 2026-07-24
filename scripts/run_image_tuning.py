import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import config
from runtimes.runtime_image import ImageRuntime


def ensure_test_targets() -> None:
    """Prevent the tuning runner from driving public websites."""
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    configured_urls = {
        "SOURCE_URL": config.SOURCE_URL,
        "TARGET_URL": config.TARGET_URL,
        "PAGE_URL": config.PAGE_URL,
    }
    public_targets = []

    for name, url in configured_urls.items():
        host = (urlparse(url).hostname or "").lower()
        is_test_host = host in allowed_hosts or host.endswith(".localhost") or host.endswith(".test")
        if not is_test_host:
            public_targets.append(f"{name}={url}")

    if public_targets:
        joined_targets = "; ".join(public_targets)
        raise RuntimeError(
            "Image tuning is limited to localhost or a .test environment. "
            f"Update the configured URLs before running this script: {joined_targets}"
        )


def setup_logging() -> Path:
    log_path = Path(config.SCREENSHOT_DIR) / "image_tuning_run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stdio_path = log_path.with_suffix(".stdio.log")
    stdio = open(stdio_path, mode="w", encoding="utf-8", buffering=1)
    sys.stdout = stdio
    sys.stderr = stdio

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    return log_path


async def main() -> None:
    log_path = setup_logging()
    logging.getLogger(__name__).info("image tuning log: %s", log_path.resolve())
    ensure_test_targets()

    runtime = ImageRuntime()
    await runtime.run()


if __name__ == "__main__":
    asyncio.run(main())
