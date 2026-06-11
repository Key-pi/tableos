"""Project core package."""

from core.celery import app as celery_app

__all__ = ("celery_app",)
