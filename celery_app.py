# celery_app.py

from celery import Celery
from config import settings

celery_app = Celery(
    "urban_morpho_tasks",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "src.tasks.tier1_tasks",
        "src.tasks.tier2_tasks",
        "src.tasks.prep_tasks",
        "src.tasks.proposed_tasks",
        "src.tasks.reporting_tasks",
        "src.tasks.tier4_tasks"
    ]
)