import logging.config


def configure_logging() -> None:
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "json"}
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    })
