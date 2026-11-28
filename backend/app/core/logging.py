import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.context import request_id_var, run_id_var

LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] [%(run_id)s] %(name)s: %(message)s"
APP_LOGGER = "app"


class ContextFilter(logging.Filter):
    """把当前请求编号和运行编号写进每条日志记录，业务代码只管 logger.info(...)。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.run_id = run_id_var.get()
        return True


def setup_logging(log_dir: Path) -> None:
    """配置 app.* 日志：同时输出到终端和 log_dir/app.log。可重复调用，后一次覆盖前一次。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for old_handler in list(logger.handlers):
        logger.removeHandler(old_handler)
        old_handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    for handler in (logging.StreamHandler(), file_handler):
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        logger.addHandler(handler)
