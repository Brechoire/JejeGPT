"""Moteur des tâches planifiées : calcul d'échéances, exécution, ticker in-process."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dtime

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .parse import PARIS

logger = logging.getLogger(__name__)

# Consigne ajoutée au prompt des tâches avec recherche web : sources cliquables.
SOURCES_SUFFIX = (
    "\n\nRéponds en Markdown. Termine par une ligne « Sources : » avec un lien "
    "Markdown cliquable [nom](https://…) par source réellement utilisée "
    "(vraies URL uniquement, aucune URL inventée)."
)

_started = False
_thread: threading.Thread | None = None


def parse_days(raw: str | list[int] | None) -> list[int]:
    if isinstance(raw, list):
        return sorted({d for d in raw if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
    days = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.append(int(part))
    return sorted(set(days))


def _at_day(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def compute_next_run(frequency: str, days, time_value, once_at, now=None, month_day=None):
    """Prochaine occurrence strictement > now (Europe/Paris). None si terminée."""
    if now is None:
        now = timezone.now()
    now = now.astimezone(PARIS)
    if frequency == "once":
        if once_at is None:
            return None
        once = once_at.astimezone(PARIS) if timezone.is_aware(once_at) else once_at.replace(tzinfo=PARIS)
        return once if once > now else None
    if time_value is None:
        return None
    if isinstance(time_value, str):
        hour, minute = (int(part) for part in time_value.split(":"))
    else:
        hour, minute = time_value.hour, time_value.minute
    if frequency == "monthly":
        if isinstance(month_day, bool) or not isinstance(month_day, int):
            return None
        if not 1 <= month_day <= 28:
            return None
        for add in range(0, 3):
            base = now.month - 1 + add
            candidate = now.replace(
                year=now.year + base // 12, month=base % 12 + 1, day=month_day,
                hour=hour, minute=minute, second=0, microsecond=0,
            )
            if candidate > now:
                return candidate
        return None
    wanted = parse_days(days) if frequency == "weekly" else list(range(7))
    if not wanted:
        return None
    for offset in range(8):
        candidate = _at_day(now + timedelta(days=offset), hour, minute)
        if candidate > now and candidate.weekday() in wanted:
            return candidate
    return None


def _normalize_reply(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _reply_hash(text: str) -> str:
    return hashlib.sha256(_normalize_reply(text).encode("utf-8")).hexdigest()


def _task_conversation(task):
    from ..models import Conversation

    if task.conversation_id:
        return task.conversation
    conv = Conversation.objects.create(
        title=task.name[:200],
        provider=task.provider,
        model_id=task.model_id,
        system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        temperature=task.temperature,
        web_search=task.web_search,
    )
    task.conversation = conv
    task.save(update_fields=["conversation"])
    return conv


def _record_run_usage(task, conv, usage: dict, success: bool, error: str = "") -> None:
    from .. import providers
    from ..models import UsageRecord

    try:
        UsageRecord.objects.create(
            conversation=conv,
            provider=task.provider,
            model_id=task.model_id,
            route=providers.resolve_route(task.provider, task.model_id),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            duration_ms=int(usage.get("duration_ms") or 0),
            success=success,
            error=(error or "")[:300],
        )
    except Exception:
        logger.exception("scheduled usage persist failed")


def _scheduled_system(conv) -> str:
    """System d'une conversation dédiée : prompt + projet + mémoire éventuels."""
    from ..memory import memory_context, project_context

    return "\n\n".join(
        part for part in (
            conv.system_prompt or settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
            project_context(conv),
            memory_context(),
        ) if part
    )


def _execute(task, now, cause: str = "due", catchup_from=None) -> None:
    from .. import providers
    from ..models import Message, ScheduledRun

    conv = _task_conversation(task)
    usage = providers._empty_usage()
    effort = conv.reasoning_effort if conv.reasoning_effort in providers.EFFORT_LEVELS else ""
    try:
        if task.provider is None or not task.model_id:
            raise providers.ProviderError("Provider ou modèle manquant (tâche désactivée ?).")
        reply = providers.chat_complete(
            task.provider, task.model_id,
            [
                {"role": "system", "content": _scheduled_system(conv)},
                {"role": "user", "content": task.prompt + (SOURCES_SUFFIX if task.web_search else "")},
            ],
            temperature=task.temperature,
            timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=f"scheduled-{task.pk}",
            route=providers.resolve_route(task.provider, task.model_id),
            options={"effort": effort, "web": bool(task.web_search), "thinking": bool(conv.thinking_enabled)},
            usage_collector=usage,
        )
    except providers.ProviderError as exc:
        task.failures += 1
        if task.failures >= settings.JEJEGPT_SCHEDULER_MAX_FAILURES:
            task.enabled = False
        task.save(update_fields=["failures", "enabled"])
        ScheduledRun.objects.create(task=task, message=None, success=False, error=exc.message, cause=cause)
        _record_run_usage(task, conv, usage, success=False, error=exc.message)
        logger.warning("scheduled task %s failed (%s)", task.pk, exc.message)
        return
    except Exception as exc:
        task.failures += 1
        if task.failures >= settings.JEJEGPT_SCHEDULER_MAX_FAILURES:
            task.enabled = False
        task.save(update_fields=["failures", "enabled"])
        ScheduledRun.objects.create(task=task, message=None, success=False, error=str(exc)[:500], cause=cause)
        logger.exception("scheduled task %s crashed", task.pk)
        return
    digest = _reply_hash(reply)
    changed = not task.last_hash or digest != task.last_hash
    content = reply
    if cause == "catchup" and catchup_from is not None:
        when = catchup_from.astimezone(PARIS).strftime("%d/%m %H:%M")
        content = f"🔁 Rattrapage de l'échéance du {when} :\n\n{reply}"
    message = Message.objects.create(conversation=conv, role="assistant", content=content)
    task.failures = 0
    task.last_hash = digest
    task.last_excerpt = (reply or "").strip()[:500]
    task.save(update_fields=["failures", "last_hash", "last_excerpt"])
    conv.save()
    ScheduledRun.objects.create(task=task, message=message, success=True, cause=cause, has_changes=changed)
    _record_run_usage(task, conv, usage, success=True)
    _maybe_auto_export(task, conv, message, reply)


def _maybe_auto_export(task, conv, message, reply) -> None:
    """Export automatique du résultat si la tâche le demande. Silencieux en échec."""
    from ..exports.service import FORMATS, create_export

    fmt = (task.auto_export or "").strip()
    if fmt not in FORMATS:
        return
    try:
        create_export(message, fmt, conv.title, reply or "")
    except Exception:
        logger.exception("scheduled auto export failed (task %s)", task.pk)


def run_task_draft(pk: int) -> bool:
    """Exécution d'essai (brouillon) : un run unique sans toucher planification.

    Ne modifie jamais enabled, next_run ni failures. Le run est enregistré
    (non lu) pour alimenter l'historique et la pastille. False si introuvable.
    """
    from .. import providers
    from ..models import Message, ScheduledRun, ScheduledTask

    try:
        task = ScheduledTask.objects.get(pk=pk)
    except ScheduledTask.DoesNotExist:
        return False
    conv = _task_conversation(task)
    usage = providers._empty_usage()
    effort = conv.reasoning_effort if conv.reasoning_effort in providers.EFFORT_LEVELS else ""
    try:
        if task.provider is None or not task.model_id:
            raise providers.ProviderError("Provider ou modèle manquant.")
        reply = providers.chat_complete(
            task.provider, task.model_id,
            [
                {"role": "system", "content": _scheduled_system(conv)},
                {"role": "user", "content": task.prompt + (SOURCES_SUFFIX if task.web_search else "")},
            ],
            temperature=task.temperature,
            timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=f"scheduled-draft-{task.pk}",
            route=providers.resolve_route(task.provider, task.model_id),
            options={"effort": effort, "web": bool(task.web_search), "thinking": bool(conv.thinking_enabled)},
            usage_collector=usage,
        )
    except providers.ProviderError as exc:
        Message.objects.create(conversation=conv, role="assistant", content="", error=exc.message)
        conv.save()
        ScheduledRun.objects.create(task=task, message=None, success=False, error=exc.message, cause="draft")
        _record_run_usage(task, conv, usage, success=False, error=exc.message)
        return True
    except Exception as exc:
        logger.exception("scheduled draft %s crashed", task.pk)
        ScheduledRun.objects.create(task=task, message=None, success=False, error=str(exc)[:500], cause="draft")
        return True
    message = Message.objects.create(conversation=conv, role="assistant", content=reply)
    conv.save()
    changed = not task.last_hash or _reply_hash(reply) != task.last_hash
    ScheduledRun.objects.create(task=task, message=message, success=True, cause="draft", has_changes=changed)
    _record_run_usage(task, conv, usage, success=True)
    _maybe_auto_export(task, conv, message, reply)
    return True


def run_task(task, now, force: bool = False, cause: str = "due") -> bool:
    """Claim anti-doublon + exécution d'une tâche. True si exécutée. Ne lève jamais.

    force=True (déclenchement manuel) : passe outre la condition next_run <= now.
    cause : due (ticker), catchup (rattrapage), manual (UI).
    """
    from ..models import ScheduledTask

    claimed_next = compute_next_run(
        task.frequency, task.days, task.time, task.once_at, now,
        month_day=task.month_day,
    )
    catchup_from = task.next_run
    claim = ScheduledTask.objects.filter(pk=task.pk, enabled=True)
    if not force:
        claim = claim.filter(next_run__lte=now)
    with transaction.atomic():
        if task.frequency == "once" and claimed_next is None:
            updated = claim.update(enabled=False, next_run=None)
            if updated:
                logger.info("scheduled task %s done (once, past), disabled", task.pk)
            return False
        updated = claim.update(next_run=claimed_next)
        if not updated:
            return False
    task.refresh_from_db()
    try:
        _execute(task, now, cause=cause, catchup_from=catchup_from if cause == "catchup" else None)
    except Exception:
        logger.exception("scheduled task %s claim executed with error", task.pk)
    return True


def run_task_now(pk: int) -> bool:
    """Exécution immédiate depuis l'UI (active aussi la tâche). False si introuvable."""
    from ..models import ScheduledTask

    try:
        task = ScheduledTask.objects.get(pk=pk)
    except ScheduledTask.DoesNotExist:
        return False
    if not task.enabled:
        task.enabled = True
        task.save(update_fields=["enabled"])
    return run_task(task, timezone.now(), force=True, cause="manual")


def run_due_tasks(now=None, grace_seconds=None) -> int:
    """Exécute les tâches dues (+ un rattrapage unique si très en retard). Ne lève jamais."""
    from ..models import ScheduledTask

    if now is None:
        now = timezone.now()
    if grace_seconds is None:
        grace_seconds = max(60, int(settings.JEJEGPT_SCHEDULER_TICK_SECONDS) * 2)
    executed = 0
    due_ids = list(
        ScheduledTask.objects.filter(enabled=True, next_run__lte=now).values_list("pk", flat=True)
    )
    for pk in due_ids:
        try:
            task = ScheduledTask.objects.get(pk=pk)
        except ScheduledTask.DoesNotExist:
            continue
        late = (
            task.next_run is not None
            and (now - task.next_run).total_seconds() > grace_seconds
        )
        if run_task(task, now, cause="catchup" if late else "due"):
            executed += 1
    return executed


def _ticker_loop() -> None:
    tick = max(5, int(settings.JEJEGPT_SCHEDULER_TICK_SECONDS))
    while True:
        try:
            close_old_connections()
            run_due_tasks()
        except Exception:
            logger.exception("scheduler tick failed")
        finally:
            close_old_connections()
        time.sleep(tick)


def start_scheduler() -> bool:
    """Démarre le ticker (idempotent). Retourne True si démarré ici."""
    global _started, _thread
    if _started and _thread is not None and _thread.is_alive():
        return False
    _thread = threading.Thread(target=_ticker_loop, name="jejegpt-scheduler", daemon=True)
    _thread.start()
    _started = True
    logger.info("scheduler started (tick %ss)", settings.JEJEGPT_SCHEDULER_TICK_SECONDS)
    return True


def scheduler_should_run(argv=None) -> bool:
    """Garde-fous : runserver (reload enfant ou --noreload), jamais sous pytest/autres commandes."""
    import os
    import sys

    args = argv if argv is not None else sys.argv
    if not settings.JEJEGPT_SCHEDULER_ENABLED:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in (args[0] if args else ""):
        return False
    if "runserver" not in args:
        return False
    is_child = os.environ.get("RUN_MAIN") == "true"
    is_noreload = "--noreload" in args
    return is_child or is_noreload


def dtime_from_str(value: str) -> dtime:
    hour, minute = (int(part) for part in value.split(":"))
    return dtime(hour=hour, minute=minute)
