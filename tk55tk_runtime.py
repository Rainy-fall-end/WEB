import logging
import os
import platform
import sys
from pathlib import Path


LINUX_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


def setup_logging(name, level="INFO", log_file=None):
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def resolve_headless(headless, logger=None):
    if headless is not None:
        return bool(headless)

    is_linux = platform.system().lower() == "linux"
    has_display = bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
    if is_linux and not has_display:
        if logger:
            logger.info("No Linux display detected; using headless browser mode.")
        return True

    return True


def browser_launch_kwargs(headless, slow_mo=0, logger=None):
    kwargs = {
        "headless": resolve_headless(headless, logger),
        "slow_mo": slow_mo,
    }

    if platform.system().lower() == "linux":
        kwargs["args"] = LINUX_BROWSER_ARGS
        if logger:
            logger.info("Using Linux Chromium launch args: %s", " ".join(LINUX_BROWSER_ARGS))

    executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable_path:
        kwargs["executable_path"] = executable_path
        if logger:
            logger.info("Using custom Chromium executable: %s", executable_path)

    return kwargs


def linux_browser_help():
    return (
        "Chromium failed to start. On Linux, run: "
        "python -m playwright install chromium && "
        "python -m playwright install-deps chromium. "
        "If running in Docker/root, keep headless mode enabled."
    )
