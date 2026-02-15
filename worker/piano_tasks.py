# piano_tasks.py

import asyncio
import traceback
from os import getenv
from pathlib import Path
from typing import Optional, Dict, Any

# HEIC support (optionnel)
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass

# Celery
from celery import Celery

# Jinja templates
from jinja2 import (
    Environment,
    FileSystemLoader,
    select_autoescape,
    TemplateNotFound,
)

# Email (envoi direct, sans Celery, via smtplib)
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

# DB / Models
from tortoise import Tortoise
from pytune_configuration.sync_config_singleton import config, SimpleConfig
from pytune_data.db import init as init_db
from pytune_data.models import User

# Core beautify service
from pytune_piano_i2i.beautify_service import run_piano_beautify_tasks

# Logging

from simple_logger.logger import get_logger, SimpleLogger


logger: SimpleLogger = get_logger("pytune_pianobeautify_worker")


# =====================================================================
#  JINJA ─ LOADING LOCAL EMAIL TEMPLATES
# =====================================================================

TEMPLATES_DIR = Path(__file__).resolve().parent / "email_templates"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_email(template_name: str, ctx: dict) -> str:
    """Render an email Jinja template."""
    return jinja_env.get_template(template_name).render(**ctx)


# =====================================================================
#  SIMPLE EMAIL SENDER (direct SMTP, sans Celery)
# =====================================================================

if config is None:
    config = SimpleConfig()


async def send_email_direct(
    to_email: str,
    subject: str,
    body: str,
    *,
    is_html: bool = True,
    from_email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Envoi direct via SMTP standard (smtplib), exécuté dans un thread
    via asyncio.to_thread pour ne pas bloquer l'event loop.
    """
    if from_email is None:
        from_email = config.FROM_EMAIL

    if not from_email:
        logger.error("SMTP 'from_email' not configured - cannot send email")
        raise ValueError("SMTP 'from_email' not configured")

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Pytune Support", from_email))
    msg["To"] = to_email
    msg["Subject"] = subject

    subtype = "html" if is_html else "plain"
    msg.attach(MIMEText(body, subtype))

    # ⚠️ Fonction STRICTEMENT synchrone pour to_thread
    def _send_sync() -> None:
        context = ssl.create_default_context()
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_SERVER_PORT) as server:
            server.starttls(context=context)
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
            logger.info(f"[BeautifyEmail] Email sent to {to_email} ({subject})")

    try:
        # Exécute le bloc SMTP bloquant dans un thread
        await asyncio.to_thread(_send_sync)
        return {"message": "Email sent successfully"}
    except Exception as e:
        logger.error(f"[BeautifyEmail] Failed to send email to {to_email}: {e}")
        raise


# =====================================================================
#  CELERY APP
# =====================================================================

BROKER_URL = getenv("RABBIT_BROKER_URL", "pyamqp://admin:MyStr0ngP@ss2024!@localhost//")
BACKEND_URL = getenv(
    "RABBIT_BACKEND",
    getenv("CELERY_RESULT_BACKEND", "redis://:UltraSecurePass2024!@pytune-redis:6379/0"),
)

app = Celery("piano_tasks", broker=BROKER_URL, backend=BACKEND_URL)

app.conf.update(
    worker_pool=config.RABBIT_WORKER_POOL,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_transport_options={
        "visibility_timeout": config.RABBIT_VISIBILITY_TIMEOUT,
    },
    timezone="UTC",
    enable_utc=True,
)


# =====================================================================
#  HEALTH CHECK TASK
# =====================================================================

@app.task(queue="i2i_tasks_queue", name="piano_tasks.health_check")
def health_check() -> Dict[str, str]:
    return {"status": "OK", "message": "piano_tasks alive"}


# =====================================================================
#  MAIN TASK — BEAUTIFY PIANO
# =====================================================================

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
    recipient_email: Optional[str] = None,
    recipient_name: Optional[str] = None,
    ) -> Dict[str, Any]:
    """
    Celery entrypoint → runs async workflow run_piano_beautify_tasks()
    """

    async def work() -> Dict[str, Any]:
        logger.info(
            f"[worker] Starting beautify_piano for session_id={session_id}, user_id={user_id}, style={style}"
        )

        # Init DB inside worker context
        await init_db()

        try:
            user: Optional[User] = await User.get_or_none(id=user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")
            resolved_recipient_email = recipient_email or getattr(user, "email", None)
            resolved_recipient_name  = (
                recipient_name
                or getattr(user, "first_name", None)
                or getattr(user, "name", None)
                or "there"  
            )

            # Injected renderers for beautify service
            def _prompt_renderer(_agent: str, _ctx: dict) -> str:
                # prompt_text est déjà construit côté backend orchestrateur
                return prompt_text

            def _email_renderer(ctx: dict) -> str:
                ctx = dict(ctx or {})
                ctx.setdefault("recipient_email", resolved_recipient_email)
                ctx.setdefault("recipient_name", resolved_recipient_name)
                return render_email(email_template_name, ctx)

            # Callback d’envoi async pour run_piano_beautify_tasks
            async def _send_email(
                to_email: str,
                subject: str,
                body: str,
                is_html: bool,
            ) -> Dict[str, Any]:
                return await send_email_direct(
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    is_html=is_html,
                    from_email=config.FROM_EMAIL,
                )
            email_enabled = bool(resolved_recipient_email)
            if not email_enabled:
                logger.warning("[worker] No recipient_email resolved — email disabled.")
            try:
                logger.info(
                    f"[worker] Email resolved: enabled={email_enabled} to={resolved_recipient_email} "
                    f"template={email_template_name}"
                )
                result = await run_piano_beautify_tasks(
                    session_id=session_id,
                    user=user,
                    style=style,
                    prompt_renderer=_prompt_renderer,
                    email_html_renderer=_email_renderer if email_enabled else None,
                    send_email=_send_email if email_enabled else None,
                    reporter=None,
                    recipient_email=resolved_recipient_email,
                )
            except TemplateNotFound:
                logger.warning(
                    f"[worker] Template '{email_template_name}' not found — email disabled."
                )
                result = await run_piano_beautify_tasks(
                    session_id=session_id,
                    user=user,
                    style=style,
                    prompt_renderer=_prompt_renderer,
                    email_html_renderer=None,
                    send_email=None,
                    reporter=None,
                    recipient_email=None,
                )

            logger.info(
                f"[worker] Beautify completed for session_id={session_id}, user_id={user_id}"
            )
            return result

        finally:
            # Always close DB connections
            try:
                await Tortoise.close_connections()
            except Exception:
                pass

    try:
        # Execute async function inside sync Celery task
        result = asyncio.run(work())
        return {"ok": True, **result}

    except Exception as e:
        logger.error(
            f"[worker] beautify_piano failed for session_id={session_id}, user_id={user_id}: {e}"
        )
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(limit=5),
            "session_id": session_id,
            "user_id": user_id,
        }