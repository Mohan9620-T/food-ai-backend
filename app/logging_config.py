import logging.config


def configure_logging() -> None:
    from app.services.observability import RequestLogFilter

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
                }
            },
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
            "root": {"handlers": ["console"], "level": "INFO"},
        }
    )

    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestLogFilter())
