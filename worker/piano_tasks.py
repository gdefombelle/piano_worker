# piano_tasks.py
import os
import asyncio
import traceback
from os import getenv
from pathlib import Path
from typing import Optional, Dict, Any

# --- (débogage) : décommente ces 3 lignes si tu veux forcer l'arrêt à l'entrée de la tâche
# import debugpy  # type: ignore
# debugpy.listen(("127.0.0.1", 5678))
# debugpy.breakpoint()

# --- Support HEIC/HEIF si pillow-heif est dispo ---
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass

from celery import Celery
from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound
from tortoise import Tortoise

from pytune_configuration.sync_config_singleton import config, SimpleConfig
from pytune_data.db import init as init_db
from pytune_data.models import User
from pytune_piano_i2i.beautify_service import run_piano_beautify_tasks
from simple_logger.logger import SimpleLogger, get_logger

logger: SimpleLogger = get_logger("pytune_pianobeautify_worker")

# === Jinja (templates locaux du worker) ===================
TEMPLATES_DIR = Path(__file__).resolve().parent / "email_templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)
def render_email(name: str, ctx: dict) -> str:
    return jinja_env.get_template(name).render(**ctx)

# === Celery app ===========================================
if config is None:
    config = SimpleConfig()

BROKER_URL = getenv("RABBIT_BROKER_URL", "pyamqp://admin:MyStr0ngP@ss2024!@localhost//")
BACKEND_URL = getenv("RABBIT_BACKEND") or getenv(
    "CELERY_RESULT_BACKEND", "redis://:UltraSecurePass2024!@pytune-redis:6379/0"
)

app = Celery("piano_tasks", broker=BROKER_URL, backend=BACKEND_URL)
app.conf.update(
    worker_pool=getattr(config, "RABBIT_WORKER_POOL", "solo"),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_transport_options={"visibility_timeout": getattr(config, "RABBIT_VISIBILITY_TIMEOUT", 3600)},
    timezone="UTC",
    enable_utc=True,
)

# === Tâches ===============================================
@app.task(queue="i2i_tasks_queue", name="piano_tasks.health_check")
def health_check():
    return {"status": "OK", "message": "piano_tasks alive"}

@app.task(
    queue="i2i_tasks_queue",
    name="piano_tasks.beautify_piano",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def beautify_piano(
    session_id: str,
    user_id: int,
    style: str = "nocturne romantique",
    *,
    prompt_text: str,
    email_template_name: str = "piano_beautify_email.html",
) -> Dict[str, Any]:

    async def work():
        await init_db()
        try:
            user: Optional[User] = await User.get_or_none(id=user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            def _prompt_renderer(_agent: str, _ctx: dict) -> str:
                return prompt_text

            def _email_renderer(ctx: dict) -> str:
                return render_email(email_template_name, ctx)

            from pytune_helpers.email_helper import EmailService
            email_service = EmailService()

            def _send_email(to_email: str, subject: str, body: str, is_html: bool):
                return email_service.send_email(
                    to_email=to_email, subject=subject, body=body, is_html=is_html, send_background=False
                )

            try:
                result = await run_piano_beautify_tasks(
                    session_id=session_id,
                    user=user,
                    style=style,
                    prompt_renderer=_prompt_renderer,
                    email_html_renderer=_email_renderer,
                    send_email=_send_email,
                    reporter=None,
                )
            except TemplateNotFound:
                logger.warning(
                    f"[worker] Template '{email_template_name}' introuvable — envoi d'email désactivé."
                )
                result = await run_piano_beautify_tasks(
                    session_id=session_id,
                    user=user,
                    style=style,
                    prompt_renderer=_prompt_renderer,
                    email_html_renderer=None,
                    send_email=None,
                    reporter=None,
                )

            return result
        finally:
            try:
                await Tortoise.close_connections()
            except Exception:
                pass

    try:
        res = asyncio.run(work())
        return {"ok": True, **res}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(limit=5),
            "session_id": session_id,
            "user_id": user_id,
        }
