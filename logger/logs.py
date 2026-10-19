"""Application logging that leaves the root logger configuration untouched."""

import logging

LOGGER_NAME = "transmision_vivo"
LOG_FORMAT = "%(asctime)s  %(levelname)s\t%(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(handler)

    return logger


log = _configure_logger()
