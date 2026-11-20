LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        # httpx logs every request's full URL at INFO, and the Poster token
        # travels in the query string (PQ-058): only warnings and errors.
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
}
