import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib import messages as django_messages
from django.core.exceptions import ValidationError
from django.db import models
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import providers
from .md import render_markdown
from .models import (
    AppSettings,
    Assistant,
    Conversation,
    DeepResearchRun,
    Export,
    LLMModel,
    MemoryFact,
    Message,
    Project,
    Provider,
    ScheduledRun,
    ScheduledTask,
    TaskTemplate,
    UsageRecord,
)

logger = logging.getLogger(__name__)


def _history(conv: Conversation) -> list[dict]:
    from .memory import memory_context, project_context
    from .scores import score_extra_message

    msgs: list[dict] = []
    system = "\n\n".join(
        part for part in (
            conv.system_prompt, project_context(conv), memory_context(),
            _attachments_context(conv),
        ) if part
    )
    if system:
        msgs.append({"role": "system", "content": system})
    # Colonnes utiles seules : le LLM ne consomme que role/content.
    # .only() sûr ici : on ne touche jamais aux autres champs de m.
    for m in conv.messages.only("role", "content").all():
        if m.role in ("user", "assistant") and m.content:
            msgs.append({"role": m.role, "content": m.content})
    # Partition intelligente : la dernière question parle-t-elle musique ?
    # Message stocké inchangé, consigne ABC ajoutée à l'appel LLM seul.
    for m in reversed(msgs):
        if m["role"] == "user":
            if (extra := score_extra_message(m["content"])) is not None:
                msgs.append(extra)
            break
    return msgs


def _conv_options(conv: Conversation) -> dict:
    effort = conv.reasoning_effort if conv.reasoning_effort in providers.EFFORT_LEVELS else ""
    return {"effort": effort, "web": bool(conv.web_search), "thinking": bool(conv.thinking_enabled)}


def index(request: HttpRequest) -> HttpResponse:
    convs = Conversation.objects.all()
    conv = convs.first()
    if conv is None:
        conv = Conversation.objects.create(title="Nouvelle conversation")
    return redirect("chat:detail", pk=conv.pk)


def _last_usage_ctx(conv: Conversation) -> dict | None:
    """Stats de la dernière réponse pour le pied de composer."""
    rec = conv.usage_records.order_by("-created_at").first()
    if rec is None:
        return None
    secs = (rec.duration_ms or 0) / 1000.0
    return {
        "prompt": rec.prompt_tokens,
        "completion": rec.completion_tokens,
        "tps": round(rec.completion_tokens / secs, 1) if secs > 0 else 0,
        "duration": f"{secs:.1f}".replace(".", ",") + " s",
        "model": rec.model_id or "—",
        "success": rec.success,
    }


def _rendered_html(message: Message) -> str:
    """HTML d'un message mémorisé (clé = contenu). Zéro risque de périmé :
    édition/régénération changent le contenu donc la clé ; TTL borne le reste."""
    import hashlib

    from django.core.cache import cache

    content = message.content or ""
    if not content:
        return ""
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    key = f"jejegpt:md:{message.pk}:{digest}"
    html = cache.get(key)
    if html is None:
        html = render_markdown(content)
        cache.set(key, html, 3600)
    return html


def _messages_ctx(conv: Conversation) -> list[dict]:
    items = []
    msgs = list(conv.messages.prefetch_related("exports").order_by("created_at", "id"))
    # Regroupe les versions d'une même question (parent commun) pour le badge V1/V2.
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for m in msgs:
        key = m.parent_id if m.parent_id else f"single-{m.pk}"
        groups[key].append(m)
    for m in msgs:
        exports = list(m.exports.all())
        sibs = groups[m.parent_id] if m.parent_id else [m]
        items.append({
            "obj": m,
            "html": _rendered_html(m),
            "exports": exports,
            "siblings": sibs,
            "is_latest": (sibs[-1].pk == m.pk) if len(sibs) > 1 else True,
            **_newsletter_ctx(exports),
        })
    return items


def _attachments_context(conv: Conversation) -> str:
    """Extraits des pièces jointes, bornés, injectés en system (sans RAG)."""
    try:
        atts = list(conv.attachments.all()[:3])
    except Exception:
        return ""
    parts = []
    for a in atts:
        txt = (a.excerpt or "").strip()
        if txt:
            parts.append(f"[Pièce jointe : {a.file_name}]\n{txt[:6000]}")
    if not parts:
        return ""
    return "Documents joints par l'utilisateur (extraits, à citer si utile) :\n\n" + "\n\n".join(parts)


def _followups_for(content: str) -> list[str]:
    """3 relances heuristiques (sans appel LLM) sous la dernière réponse."""
    text = (content or "").strip()
    if not text:
        return []
    chips = []
    if len(text) > 800:
        chips.append("Résume en 5 points clés")
    if "```" in text or "code" in text.lower():
        chips.append("Explique le code étape par étape")
    if "|" in text:
        chips.append("Convertis le tableau en plan d'action")
    for s in ("Approfondis avec des exemples concrets",
              "Propose un plan d'action",
              "Quelles sont les limites / points de vigilance ?"):
        if len(chips) >= 3:
            break
        if s not in chips:
            chips.append(s)
    return chips[:3]


def _newsletter_ctx(exports: list) -> dict:
    """Boutons par canal : la régénération a lieu au clic (aucune lecture de cache)."""
    return {"newsletter_channels": True}


def _newsletter_source(mid: int):
    """Message source d'une newsletter : (message, conversation,httperror|None)."""
    msg = get_object_or_404(Message, pk=mid)
    conv = msg.conversation
    if msg.role != "assistant" or not (msg.content or "").strip():
        return msg, conv, "La newsletter se crée depuis une réponse."
    if conv.provider is None or not conv.model_id:
        return msg, conv, "Configurez un provider et un modèle pour générer la newsletter."
    return msg, conv, None


@require_POST
def newsletter_channel(request: HttpRequest, mid: int, channel: str) -> HttpResponse:
    """Régénère une synthèse adaptée au canal (mailto|html|eml), à chaque clic."""
    from django.http import JsonResponse

    from .exports.service import create_export
    from .newsletter import (
        URL_CHANNELS,
        build_newsletter_for_channel,
        mailto_href,
        split_newsletter,
    )

    if channel not in URL_CHANNELS:
        return HttpResponse("Canal newsletter inconnu.", status=404)
    msg, conv, guard_error = _newsletter_source(mid)
    wants_json = request.headers.get("HX-Request") == "true" or "application/json" in (
        request.headers.get("Accept") or ""
    )
    if guard_error:
        if wants_json:
            return JsonResponse({"error": guard_error}, status=400)
        django_messages.error(request, guard_error)
        return redirect("chat:detail", pk=conv.pk)
    try:
        fresh = build_newsletter_for_channel(conv, msg.content or "", channel)
    except providers.ProviderError as exc:
        if wants_json:
            return JsonResponse({"error": f"Newsletter impossible : {exc.message}"}, status=400)
        django_messages.error(request, f"Newsletter impossible : {exc.message}")
        return redirect("chat:detail", pk=conv.pk)
    try:
        if channel == "mailto":
            subject, _md, text = split_newsletter(fresh)
            href = mailto_href(subject, text)
            if wants_json:
                return JsonResponse({"mailto": href})
            return redirect(href)
        if channel == "html":
            from .exports.html_gen import build_html

            html_doc = build_html(f"Newsletter — {conv.title}", fresh).decode("utf-8")
            msg.exports.filter(format="html").delete()
            create_export(msg, "html", f"Newsletter — {conv.title}", fresh)
            if wants_json:
                return JsonResponse({"html": html_doc})
            django_messages.success(request, "HTML riche régénéré (export mis à jour).")
            return redirect("chat:detail", pk=conv.pk)
        msg.exports.filter(format__in=("html", "eml")).delete()
        create_export(msg, "html", f"Newsletter — {conv.title}", fresh)
        eml_exp = create_export(msg, "eml", f"Newsletter — {conv.title}", fresh)
        if wants_json:
            return JsonResponse({"download": eml_exp.get_absolute_url()})
        return redirect(eml_exp.get_absolute_url())
    except (ValidationError, ValueError) as exc:
        detail_msg = getattr(exc, "message", None) or str(exc)
        if wants_json:
            return JsonResponse({"error": f"Newsletter impossible : {detail_msg}"}, status=400)
        django_messages.error(request, f"Newsletter impossible : {detail_msg}")
        return redirect("chat:detail", pk=conv.pk)


def _snippets_json(conv: Conversation | None = None) -> list[dict]:
    """Slash-commands + gabarits + starters pour l'autocomplétion du composer."""
    slashes = [
        {"cmd": "/resume", "label": "Résumer", "prompt": "Résume en 5 points clés : "},
        {"cmd": "/mail", "label": "Mail pro", "prompt": "Rédige un mail professionnel : "},
        {"cmd": "/code", "label": "Code", "prompt": "Donne le code + explications : "},
        {"cmd": "/traduire", "label": "Traduire EN", "prompt": "Traduis en anglais : "},
        {"cmd": "/plan", "label": "Plan d'action", "prompt": "Propose un plan d'action concret : "},
    ]
    items = list(slashes)
    for tpl in TaskTemplate.objects.all()[:20]:
        items.append({"cmd": f"/{tpl.name.lower().replace(' ', '-')}",
                      "label": tpl.name, "prompt": tpl.body[:200]})
    if conv is not None and conv.assistant_id:
        for s in conv.assistant.starter_list():
            items.append({"cmd": "▶", "label": s[:60], "prompt": s})
    return items


def detail(request: HttpRequest, pk: int) -> HttpResponse:
    conv = get_object_or_404(Conversation.objects.select_related("assistant"), pk=pk)
    ScheduledRun.objects.filter(task__conversation=conv, seen=False).update(seen=True)
    has_msgs = conv.messages.exists()
    starters: list[str] = []
    if not has_msgs and conv.assistant_id:
        starters = conv.assistant.starter_list()
    thread_ctx = _thread_ctx(conv)
    ctx = {
        **_sidebar_ctx(conv),
        "has_messages": has_msgs,
        "assistant_starters": starters,
        "flash_messages": list(django_messages.get_messages(request)),
        "providers": Provider.objects.filter(is_active=True).defer("api_key").prefetch_related("cached_models"),
        "messages": thread_ctx["messages"],
        "followups": thread_ctx["followups"],
        "pinned_messages": thread_ctx["pinned_messages"],
        "default_temperature": settings.JEJEGPT_DEFAULT_TEMPERATURE,
        "controls": providers.ui_controls(conv.provider, conv.model_id),
        "effort_levels": providers.EFFORT_LEVELS,
        "last_usage": _last_usage_ctx(conv),
        "research_run": DeepResearchRun.objects.filter(
            conversation=conv, status="running"
        ).first(),
        "documents": list(conv.documents.all()[:10]),
        "active_document": conv.documents.first(),
        "attachments": list(conv.attachments.all()[:10]),
        "snippets_json": _snippets_json(conv),
    }
    return render(request, "chat/detail.html", ctx)


def _sidebar_ctx(conversation: Conversation) -> dict:
    cutoff = timezone.now() - timedelta(days=30)
    # Audit templates du 29/09/2026 : sidebar_item.html ne lit que
    # c.pk / c.title / c.project.name (+ project_id pour le test {% if c.project %}).
    # Les TEXT lourds (system_prompt...) sont exclus via .only(). Listes bornées
    # à 50+50 : au-delà, la recherche globale (/recherche/) prend le relais.
    base = (
        Conversation.objects.select_related("project", "assistant")
        .only("id", "title", "updated_at", "project_id", "assistant_id", "project__name")
        .order_by("-updated_at")
    )
    return {
        "conversations": base,
        "recent_convs": base.filter(updated_at__gte=cutoff)[:50],
        "older_convs": base.filter(updated_at__lt=cutoff)[:50],
        "conversation": conversation,
        "active_project_id": getattr(conversation, "project_id", None),
        "scheduled_unseen": ScheduledRun.objects.filter(seen=False).count(),
        "projects": list(
            # Pas de prefetch("conversations") : sidebar.html n'itère jamais les
            # conversations du projet, seul conv_count (annotate) est affiché.
            Project.objects.annotate(
                conv_count=models.Count("conversations")
            ).only("id", "name", "instructions")
        ),
        "assistants": list(
            Assistant.objects.annotate(conv_count=models.Count("conversations")).all()
        ),
    }


def _resolve_default_llm() -> tuple[Provider | None, str]:
    """Provider + modèle par défaut (réglage global, sinon 1er provider actif)."""
    defaults = AppSettings.get_cached()
    if defaults.default_valid():
        return defaults.default_provider, defaults.default_model_id
    first_provider = Provider.objects.filter(is_active=True).first()
    first_model = ""
    if first_provider:
        m = first_provider.cached_models.first()
        first_model = m.model_id if m else ""
    return first_provider, first_model


def _assistant_llm(assistant: Assistant) -> tuple[Provider | None, str]:
    """LLM d'un assistant : figé si encore valide (actif + en cache), sinon défaut."""
    provider = assistant.provider
    if (
        provider is not None and provider.is_active and assistant.model_id
        and provider.cached_models.filter(model_id=assistant.model_id).exists()
    ):
        return provider, assistant.model_id
    return _resolve_default_llm()


def _assistant_llm_ctx() -> dict:
    """Providers actifs + défaut global, pour les selects LLM des formulaires."""
    defaults = AppSettings.get_cached()
    return {
        "llm_providers": list(
            Provider.objects.filter(is_active=True).defer("api_key").prefetch_related("cached_models")
        ),
        "llm_default": (
            f"{defaults.default_provider.name} / {defaults.default_model_id}"
            if defaults.default_valid() else ""
        ),
    }


@require_POST
def conversation_create(request: HttpRequest) -> HttpResponse:
    first_provider, first_model = _resolve_default_llm()
    conv = Conversation.objects.create(
        title="Nouvelle conversation",
        provider=first_provider,
        model_id=first_model,
        temperature=settings.JEJEGPT_DEFAULT_TEMPERATURE,
        system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
    )
    if project_id := (request.POST.get("project_id") or "").strip():
        try:
            conv.project = Project.objects.get(pk=int(project_id))
            conv.save(update_fields=["project", "updated_at"])
        except (Project.DoesNotExist, ValueError):
            pass
    if request.headers.get("HX-Request"):
        return render(
            request, "chat/partials/sidebar_conversations.html",
            _sidebar_ctx(conv),
        )
    return redirect("chat:detail", pk=conv.pk)


@require_POST
def conversation_delete(request: HttpRequest, pk: int) -> HttpResponse:
    conv = get_object_or_404(Conversation, pk=pk)
    conv.delete()
    nxt = Conversation.objects.first()
    if request.headers.get("HX-Request"):
        if nxt is None:
            nxt = Conversation.objects.create(title="Nouvelle conversation")
        resp = render(
            request, "chat/partials/sidebar_conversations.html",
            _sidebar_ctx(nxt),
        )
        resp["HX-Redirect"] = nxt.get_absolute_url()
        return resp
    return redirect("chat:detail", pk=nxt.pk) if nxt else redirect("chat:index")


@require_POST
def conversation_update(request: HttpRequest, pk: int) -> HttpResponse:
    conv = get_object_or_404(Conversation, pk=pk)
    if title := request.POST.get("title"):
        conv.title = title[:200]
    if "system_prompt" in request.POST:
        conv.system_prompt = request.POST.get("system_prompt", "")
    if temp := request.POST.get("temperature"):
        try:
            conv.temperature = max(0.0, min(2.0, float(temp)))
        except ValueError:
            pass
    if "reasoning_effort" in request.POST:
        effort = (request.POST.get("reasoning_effort") or "").strip()
        conv.reasoning_effort = effort if effort in providers.EFFORT_LEVELS else ""
    if "web_search" in request.POST:
        conv.web_search = request.POST.get("web_search") in ("1", "true", "on")
    if "show_images" in request.POST:
        conv.show_images = request.POST.get("show_images") in ("1", "true", "on")
    if "thinking_enabled" in request.POST:
        conv.thinking_enabled = request.POST.get("thinking_enabled") in ("1", "true", "on")
    if pid := request.POST.get("provider_id"):
        try:
            conv.provider = Provider.objects.get(pk=int(pid))
        except (Provider.DoesNotExist, ValueError):
            pass
    if "model_id" in request.POST:
        conv.model_id = request.POST.get("model_id", "")[:300]
    if "project_id" in request.POST:
        raw_project = (request.POST.get("project_id") or "").strip()
        if not raw_project:
            conv.project = None
        else:
            try:
                conv.project = Project.objects.get(pk=int(raw_project))
            except (Project.DoesNotExist, ValueError):
                pass
    conv.save()
    if request.headers.get("HX-Request"):
        # Toggles du composer (postOpt) : renvoie les options à jour,
        # la page ne recharge pas, seul le bloc est remplacé.
        return render(request, "chat/partials/composer_options.html", {
            "conversation": conv,
            "controls": providers.ui_controls(conv.provider, conv.model_id),
            "effort_levels": providers.EFFORT_LEVELS,
        })
    return redirect("chat:detail", pk=conv.pk)


def project_detail(request: HttpRequest, pk: int) -> HttpResponse:
    from django.db.models import Count, Max, Prefetch

    project = get_object_or_404(
        Project.objects.prefetch_related(
            Prefetch(
                "conversations",
                queryset=Conversation.objects.annotate(msg_count=Count("messages")),
            )
        ).annotate(
            conv_count=Count("conversations", distinct=True),
            msg_total=Count("conversations__messages", distinct=True),
            last_activity=Max("conversations__updated_at"),
        ),
        pk=pk,
    )
    conv = Conversation.objects.first()
    if conv is not None:
        ctx = _sidebar_ctx(conv)
    else:
        ctx = {
            "recent_convs": [],
            "older_convs": [],
            "active_project_id": None,
            "projects": list(
                Project.objects.annotate(
                    conv_count=models.Count("conversations")
                ).only("id", "name", "instructions")
            ),
            "assistants": list(
                Assistant.objects.annotate(conv_count=models.Count("conversations"))
            ),
        }
    ctx.update({
        "conversation": conv,
        "project": project,
        "has_instructions": bool((project.instructions or "").strip()),
    })
    return render(request, "chat/project_detail.html", ctx)


@require_POST
def project_create(request: HttpRequest) -> HttpResponse:
    name = (request.POST.get("name") or "").strip()[:160]
    if not name:
        django_messages.error(request, "Nom de projet requis.")
        return redirect("chat:index")
    project, created = Project.objects.get_or_create(name=name)
    if created:
        django_messages.success(request, f"Projet « {name} » créé.")
    return redirect("chat:project_detail", pk=project.pk)


@require_POST
def project_update(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    if name := (request.POST.get("name") or "").strip()[:160]:
        if Project.objects.exclude(pk=pk).filter(name=name).exists():
            django_messages.error(request, f"Un projet « {name} » existe déjà.")
            return redirect("chat:project_detail", pk=pk)
        project.name = name
    if "instructions" in request.POST:
        project.instructions = request.POST.get("instructions", "")
    project.save()
    django_messages.success(request, f"Projet « {project.name} » mis à jour.")
    return redirect("chat:project_detail", pk=pk)


@require_POST
def project_delete(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    name = project.name
    project.delete()
    django_messages.success(request, f"Projet « {name} » supprimé (conversations conservées).")
    return redirect("chat:index")


@require_POST
def project_conversations_delete(request: HttpRequest, pk: int) -> HttpResponse:
    from django.db import transaction

    project = get_object_or_404(Project, pk=pk)
    count = project.conversations.count()
    if count:
        with transaction.atomic():
            project.conversations.all().delete()
        django_messages.success(request, f"{count} conversation(s) du projet « {project.name} » supprimée(s).")
    else:
        django_messages.info(request, f"Aucune conversation à supprimer dans « {project.name} ».")
    return redirect("chat:project_detail", pk=pk)


def _parse_assistant_data(request: HttpRequest) -> tuple[dict | None, str]:
    """Valide un assistant : (data, erreur). Nom requis, température bornée,
    effort connu, 4 starters max, LLM figé valide ou défaut."""
    name = (request.POST.get("name") or "").strip()[:160]
    if not name:
        return None, "Nom d'assistant requis."
    try:
        temperature = max(0.0, min(2.0, float(request.POST.get("temperature", "0.7") or 0.7)))
    except (ValueError, TypeError):
        temperature = 0.7
    effort = (request.POST.get("reasoning_effort") or "").strip()
    if effort not in providers.EFFORT_LEVELS:
        effort = ""
    provider = None
    model_id = ""
    if raw_provider := (request.POST.get("provider_id") or "").strip():
        try:
            provider = Provider.objects.get(pk=int(raw_provider), is_active=True)
        except (Provider.DoesNotExist, ValueError, TypeError):
            return None, "Provider invalide ou inactif."
        model_id = (request.POST.get("model_id") or "").strip()[:300]
        if not model_id or not provider.cached_models.filter(model_id=model_id).exists():
            return None, "Modèle inconnu pour ce provider (Actualiser d'abord)."
    return {
        "name": name,
        "description": (request.POST.get("description") or "").strip()[:300],
        "instructions": (request.POST.get("instructions") or "").strip(),
        "provider": provider,
        "model_id": model_id,
        "temperature": temperature,
        "reasoning_effort": effort,
        "web_search": request.POST.get("web_search") in ("1", "true", "on"),
        "thinking_enabled": request.POST.get("thinking_enabled", "on") in ("1", "true", "on"),
        "starters": Assistant.normalize_starters(request.POST.get("starters") or ""),
    }, ""


def assistant_list(request: HttpRequest) -> HttpResponse:
    conv = Conversation.objects.first()
    ctx = _sidebar_ctx(conv) if conv is not None else {
        "recent_convs": [], "older_convs": [], "active_project_id": None,
        "projects": list(Project.objects.annotate(conv_count=models.Count("conversations"))),
        "assistants": list(Assistant.objects.annotate(conv_count=models.Count("conversations"))),
    }
    ctx.update({
        "conversation": conv,
        "assistants_page": list(
            Assistant.objects.annotate(conv_count=models.Count("conversations")).all()
        ),
        "sidebar_tab": "assistants",
        "effort_levels": providers.EFFORT_LEVELS,
        **_assistant_llm_ctx(),
    })
    return render(request, "chat/assistant_list.html", ctx)


def assistant_detail(request: HttpRequest, pk: int) -> HttpResponse:
    from django.db.models import Count

    assistant = get_object_or_404(
        Assistant.objects.annotate(conv_count=Count("conversations")), pk=pk
    )
    conv = Conversation.objects.first()
    ctx = _sidebar_ctx(conv) if conv is not None else {
        "recent_convs": [], "older_convs": [], "active_project_id": None,
        "projects": list(Project.objects.annotate(conv_count=models.Count("conversations"))),
        "assistants": list(Assistant.objects.annotate(conv_count=models.Count("conversations"))),
    }
    ctx.update({
        "conversation": conv,
        "assistant": assistant,
        "sidebar_tab": "assistants",
        "assistant_conversations": list(assistant.conversations.all()[:20]),
        "effort_levels": providers.EFFORT_LEVELS,
        **_assistant_llm_ctx(),
    })
    return render(request, "chat/assistant_detail.html", ctx)


@require_POST
def assistant_create(request: HttpRequest) -> HttpResponse:
    data, error = _parse_assistant_data(request)
    if data is None:
        django_messages.error(request, error)
        return redirect("chat:assistant_list")
    if Assistant.objects.filter(name=data["name"]).exists():
        django_messages.error(request, f"Un assistant « {data['name']} » existe déjà.")
        return redirect("chat:assistant_list")
    assistant = Assistant.objects.create(**data)
    django_messages.success(request, f"Assistant « {assistant.name} » créé.")
    return redirect("chat:assistant_detail", pk=assistant.pk)


@require_POST
def assistant_update(request: HttpRequest, pk: int) -> HttpResponse:
    assistant = get_object_or_404(Assistant, pk=pk)
    data, error = _parse_assistant_data(request)
    if data is None:
        django_messages.error(request, error)
        return redirect("chat:assistant_detail", pk=pk)
    if Assistant.objects.exclude(pk=pk).filter(name=data["name"]).exists():
        django_messages.error(request, f"Un assistant « {data['name']} » existe déjà.")
        return redirect("chat:assistant_detail", pk=pk)
    for field, value in data.items():
        setattr(assistant, field, value)
    assistant.save()
    django_messages.success(request, f"Assistant « {assistant.name} » mis à jour.")
    return redirect("chat:assistant_detail", pk=pk)


@require_POST
def assistant_delete(request: HttpRequest, pk: int) -> HttpResponse:
    assistant = get_object_or_404(Assistant, pk=pk)
    name = assistant.name
    assistant.delete()
    django_messages.success(request, f"Assistant « {name} » supprimé (conversations conservées).")
    return redirect("chat:assistant_list")


@require_POST
def assistant_duplicate(request: HttpRequest, pk: int) -> HttpResponse:
    assistant = get_object_or_404(Assistant, pk=pk)
    base = f"{assistant.name} (copie)"[:160]
    name = base
    suffix = 2
    while Assistant.objects.filter(name=name).exists():
        name = f"{base} {suffix}"[:160]
        suffix += 1
    copy = Assistant.objects.create(
        name=name, description=assistant.description, instructions=assistant.instructions,
        provider=assistant.provider, model_id=assistant.model_id,
        temperature=assistant.temperature, reasoning_effort=assistant.reasoning_effort,
        web_search=assistant.web_search, thinking_enabled=assistant.thinking_enabled,
        starters=assistant.starters,
    )
    django_messages.success(request, f"Assistant « {assistant.name} » dupliqué.")
    return redirect("chat:assistant_detail", pk=copy.pk)


@require_POST
def assistant_start_conversation(request: HttpRequest, pk: int) -> HttpResponse:
    """Démarre une conversation : LLM figé de l'assistant, sinon modèle par défaut."""
    assistant = get_object_or_404(Assistant, pk=pk)
    provider, model_id = _assistant_llm(assistant)
    conv = Conversation.objects.create(
        title=assistant.name[:200],
        provider=provider,
        model_id=model_id,
        temperature=assistant.temperature,
        system_prompt=assistant.instructions or settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        reasoning_effort=assistant.reasoning_effort,
        web_search=assistant.web_search,
        thinking_enabled=assistant.thinking_enabled,
        assistant=assistant,
    )
    return redirect("chat:detail", pk=conv.pk)


def score_demo(request: HttpRequest) -> HttpResponse:
    """Vitrine partition intelligente : 3 exemples rendus par le viewer JS."""
    from .scores import SCORE_DEMOS

    conv = Conversation.objects.first()
    if conv is not None:
        ctx = _sidebar_ctx(conv)
    else:
        ctx = {
            "recent_convs": [], "older_convs": [], "active_project_id": None,
            "projects": list(Project.objects.annotate(conv_count=models.Count("conversations"))),
            "assistants": list(Assistant.objects.annotate(conv_count=models.Count("conversations"))),
        }
    ctx.update({"conversation": conv, "demos": SCORE_DEMOS})
    return render(request, "chat/score_demo.html", ctx)


@require_POST
def score_events(request: HttpRequest) -> JsonResponse:
    """Événements jouables d'un bloc ABC (audio Tone.js + mode débutant)."""
    from .scores import abc_to_events

    try:
        return JsonResponse(abc_to_events(request.POST.get("abc") or ""))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def score_musicxml(request: HttpRequest) -> HttpResponse:
    """Téléchargement MusicXML généré depuis un bloc ABC."""
    import io
    import unicodedata

    from .scores import abc_to_musicxml

    try:
        xml = abc_to_musicxml(request.POST.get("abc") or "", request.POST.get("title") or "")
    except ValueError as exc:
        return HttpResponse(str(exc), status=400)
    ascii_ = "".join(
        c for c in unicodedata.normalize("NFD", request.POST.get("title") or "partition")
        if unicodedata.category(c) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-") or "partition"
    return FileResponse(
        io.BytesIO(xml.encode("utf-8")), as_attachment=True,
        filename=f"{slug}.musicxml",
        content_type="application/vnd.recordare.musicxml+xml",
    )


@require_POST
def memory_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    fact = get_object_or_404(MemoryFact, pk=pk)
    fact.active = not fact.active
    fact.save(update_fields=["active"])
    return redirect("chat:settings_tab", tab="personnalisation")


@require_POST
def memory_delete(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(MemoryFact, pk=pk).delete()
    return redirect("chat:settings_tab", tab="personnalisation")


@require_POST
def provider_create(request: HttpRequest) -> HttpResponse:
    name = (request.POST.get("name") or "").strip()[:100]
    base_url = (request.POST.get("base_url") or "").strip().rstrip("/")
    api_key = (request.POST.get("api_key") or "").strip()
    if name and base_url:
        Provider.objects.update_or_create(
            name=name, defaults={"base_url": base_url, "api_key": api_key, "is_active": True}
        )
    if request.POST.get("next") == "providers":
        return redirect("chat:settings_tab", tab="providers")
    conv = Conversation.objects.first()
    return redirect("chat:detail", pk=conv.pk) if conv else redirect("chat:index")


@require_POST
def provider_refresh(request: HttpRequest, pk: int) -> HttpResponse:
    provider = get_object_or_404(Provider, pk=pk)
    count, error = providers.sync_models(provider, timeout=settings.JEJEGPT_HTTP_TIMEOUT)
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/model_options.html", {
            "providers": Provider.objects.filter(is_active=True).defer("api_key").prefetch_related("cached_models"),
            "conversation": Conversation.objects.first(),
            "refresh_info": f"{count} modèle(s)" if not error else f"Erreur : {error}",
        })
    if request.POST.get("next") == "providers":
        return redirect("chat:settings_tab", tab="providers")
    conv = Conversation.objects.first()
    return redirect("chat:detail", pk=conv.pk) if conv else redirect("chat:index")


@require_POST
def provider_update(request: HttpRequest, pk: int) -> HttpResponse:
    """Modifie base_url / is_active ; api_key vide = inchangée (jamais réaffichée)."""
    provider = get_object_or_404(Provider, pk=pk)
    if base_url := (request.POST.get("base_url") or "").strip().rstrip("/"):
        provider.base_url = base_url
    if api_key := (request.POST.get("api_key") or "").strip():
        provider.api_key = api_key
    if "is_active" in request.POST:
        provider.is_active = request.POST.get("is_active") in ("1", "true", "on")
    provider.save()
    return redirect("chat:settings_tab", tab="providers")


@require_POST
def provider_delete(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(Provider, pk=pk).delete()
    return redirect("chat:settings_tab", tab="providers")


@require_POST
def model_caps(request: HttpRequest, pk: int) -> HttpResponse:
    """Règle manuelle des capacités d'un modèle (prime sur la table statique)."""
    provider = get_object_or_404(Provider, pk=pk)
    model = get_object_or_404(LLMModel, provider=provider, model_id=(request.POST.get("model_id") or "").strip()[:300])
    model.supports_effort = request.POST.get("supports_effort") in ("1", "true", "on")
    model.supports_web = request.POST.get("supports_web") in ("1", "true", "on")
    model.supports_thinking = request.POST.get("supports_thinking") in ("1", "true", "on")
    model.save(update_fields=["supports_effort", "supports_web", "supports_thinking"])
    return redirect("chat:settings_tab", tab="providers")


@require_POST
def model_web_test(request: HttpRequest, pk: int) -> HttpResponse:
    """Teste empiriquement la navigation web d'un modèle (bouton « Tester »).

    Sonde datée avec web forcé + vérification serveur des liens cités.
    Ne modifie JAMAIS supports_web : seul le verdict (statut + date) est
    persisté, avec une suggestion si les cases contredisent le résultat.
    """
    from .webtest import run_web_test

    provider = get_object_or_404(Provider, pk=pk)
    model = get_object_or_404(LLMModel, provider=provider, model_id=(request.POST.get("model_id") or "").strip()[:300])
    status, detail = run_web_test(provider, model.model_id)
    model.web_test_status = status
    model.web_test_detail = detail[:300]
    model.web_test_at = timezone.now()
    model.save(update_fields=["web_test_status", "web_test_detail", "web_test_at"])
    if status == "ok" and not model.supports_web:
        django_messages.info(request, f"« {model.model_id} » navigue : pensez à cocher « web ».")
    elif status == "ko" and model.supports_web:
        django_messages.info(request, f"« {model.model_id} » ne navigue pas : pensez à décocher « web ».")
    else:
        label = {"ok": "navigue", "ko": "ne navigue pas"}.get(status, "verdict incertain")
        django_messages.info(request, f"Test web « {model.model_id} » : {label} — {detail}")
    return redirect("chat:settings_tab", tab="providers")


@require_POST
def model_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Ajoute un model_id au cache (secours si GET /models incompatible)."""
    provider = get_object_or_404(Provider, pk=pk)
    if mid := (request.POST.get("model_id") or "").strip()[:300]:
        LLMModel.objects.update_or_create(
            provider=provider,
            model_id=mid,
            defaults={
                "display_name": mid,
                "route": providers.lookup_route(str(provider.base_url), mid),
            },
        )
    return redirect("chat:settings_tab", tab="providers")


SETTINGS_TABS = [
    "general", "notifications", "personnalisation", "plugins", "voix",
    "facturation", "utilisation", "statistiques", "providers", "donnees",
    "navigateur", "stockage", "protection", "securite", "parental",
]
FUNCTIONAL_TABS = {"general", "personnalisation", "providers", "donnees", "utilisation"}


def settings_page(request: HttpRequest, tab: str = "general") -> HttpResponse:
    if tab not in SETTINGS_TABS:
        return redirect("chat:settings_tab", tab="general")
    conv = Conversation.objects.first()
    if conv is None:
        ctx: dict = {
            "recent_convs": [],
            "older_convs": [],
            "active_project_id": None,
            "projects": list(
                Project.objects.annotate(
                    conv_count=models.Count("conversations")
                ).only("id", "name", "instructions")
            ),
            "assistants": list(
                Assistant.objects.annotate(conv_count=models.Count("conversations"))
            ),
        }
    else:
        ctx = _sidebar_ctx(conv)
    ctx.update({
        "conversation": conv,
        "active_tab": tab,
        "is_functional": tab in FUNCTIONAL_TABS,
        "providers": Provider.objects.prefetch_related("cached_models"),
        "current_conv": conv,
        "app_settings": AppSettings.get_cached(),
        "default_preset": settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        "memory_facts": list(MemoryFact.objects.all()[:100]),
        "memory_max_facts": settings.JEJEGPT_MEMORY_MAX_FACTS,
        "text_scale_choices": (
            (87, "Compact"), (100, "Normal"), (112, "Large"), (125, "XL"),
        ),
    })
    if tab == "donnees":
        from django.db.models import Sum

        db_path = settings.BASE_DIR / "db.sqlite3"
        ctx["stats"] = {
            "conversations": Conversation.objects.count(),
            "messages": Message.objects.count(),
            "providers": Provider.objects.count(),
            "db_size": db_path.stat().st_size if db_path.exists() else 0,
            "exports": Export.objects.count(),
            "exports_size": Export.objects.aggregate(total=Sum("size"))["total"] or 0,
        }
        ctx["recent_exports"] = list(Export.objects.select_related("message")[:20])
    if tab == "utilisation":
        ctx.update(_usage_ctx(request))
    return render(request, "chat/settings.html", ctx)


USAGE_PERIODS = (7, 30, 90)
USAGE_WEEKS = 22


def _usage_ctx(request: HttpRequest) -> dict:
    from django.db.models import Avg, Count, Sum
    from django.db.models.functions import ExtractHour, TruncDate

    try:
        period = int(request.GET.get("period", "30"))
    except (ValueError, TypeError):
        period = 30
    if period not in USAGE_PERIODS:
        period = 30
    cutoff = timezone.now() - timedelta(days=period)
    qs = UsageRecord.objects.filter(created_at__gte=cutoff)

    totals = qs.aggregate(
        calls=Count("id"),
        prompt=Sum("prompt_tokens"),
        completion=Sum("completion_tokens"),
        total=Sum("total_tokens"),
        avg_ms=Avg("duration_ms"),
        ms_total=Sum("duration_ms"),
        errors=Count("id", filter=models.Q(success=False)),
    )
    totals = {k: (v or 0) for k, v in totals.items()}
    totals["avg_ms"] = round(totals["avg_ms"])
    total_sec = (totals["ms_total"] or 0) / 1000.0
    totals["tps"] = round((totals["completion"] or 0) / total_sec, 1) if total_sec > 0 else 0
    by_model = list(
        qs.values("model_id", "provider__name", "route").annotate(
            calls=Count("id"),
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
            total=Sum("total_tokens"),
            avg_ms=Avg("duration_ms"),
            errors=Count("id", filter=models.Q(success=False)),
        ).order_by("-total")
    )
    for row in by_model:
        secs = ((row["avg_ms"] or 0) / 1000.0) or 0
        row["tps"] = round((row["completion"] or 0) / secs, 1) if secs > 0 else 0
        row["avg_ms"] = round(row["avg_ms"] or 0)

    days = {
        str(r["day"]): r["calls"]
        for r in qs.annotate(day=TruncDate("created_at")).values("day").annotate(calls=Count("id"))
    }
    today = timezone.localdate()
    start = today - timedelta(days=USAGE_WEEKS * 7 - 1)
    heatmap: list[list[dict]] = []
    day = start
    for _ in range(USAGE_WEEKS):
        week = []
        for _ in range(7):
            calls = days.get(str(day), 0)
            week.append({"date": day, "calls": calls, "level": min(4, (calls + 1) // 2) if calls else 0})
            day += timedelta(days=1)
        heatmap.append(week)

    recent = list(qs.select_related("conversation").order_by("-created_at")[:20])
    for rec in recent:
        secs = (rec.duration_ms or 0) / 1000.0
        rec.tps = round(rec.completion_tokens / secs, 1) if secs > 0 else 0

    per_day = [
        {"date": day, "calls": days.get(str(day), 0)}
        for day in (today - timedelta(days=i) for i in range(period - 1, -1, -1))
    ]
    peak_hours = list(
        qs.annotate(hour=ExtractHour("created_at")).values("hour").annotate(
            calls=Count("id")
        ).order_by("-calls")[:3]
    )
    top_convs = list(
        qs.values("conversation_id", "conversation__title").annotate(
            total=Sum("total_tokens"), calls=Count("id")
        ).order_by("-total")[:5]
    )
    feedback = Message.objects.filter(
        created_at__gte=cutoff, role="assistant"
    ).aggregate(
        total=Count("id"),
        up=Count("id", filter=models.Q(feedback="up")),
        down=Count("id", filter=models.Q(feedback="down")),
    )
    feedback = {k: (v or 0) for k, v in feedback.items()}

    return {
        "usage_period": period,
        "usage_periods": USAGE_PERIODS,
        "usage_totals": totals,
        "usage_heatmap": heatmap,
        "usage_by_model": by_model,
        "usage_recent": recent,
        "usage_per_day": per_day,
        "usage_peak_hours": peak_hours,
        "usage_top_convs": top_convs,
        "usage_feedback": feedback,
    }


@require_POST
def settings_save(request: HttpRequest) -> HttpResponse:
    """Enregistre le LLM par défaut et la taille du texte (onglet Général).

    Validation stricte, valeur absente/malformée/invalide -> défaut conservé.
    """
    defaults = AppSettings.get_solo()
    choice = (request.POST.get("default_choice") or "").strip()
    pk_raw, _, model_id = choice.partition(":")
    model_id = model_id.strip()[:300]
    try:
        provider = Provider.objects.get(pk=int(pk_raw), is_active=True)
    except (Provider.DoesNotExist, ValueError, TypeError):
        provider = None
    if provider is not None and model_id and provider.cached_models.filter(model_id=model_id).exists():
        defaults.default_provider = provider
        defaults.default_model_id = model_id
        defaults.save()
    if "memory_enabled" in request.POST:
        defaults.memory_enabled = request.POST.get("memory_enabled") in ("1", "true", "on")
        defaults.save(update_fields=["memory_enabled"])
    if "text_scale" in request.POST:
        try:
            scale = int(request.POST.get("text_scale") or 0)
        except (ValueError, TypeError):
            scale = 0
        if scale in AppSettings.TEXT_SCALES:
            defaults.text_scale = scale
            defaults.save(update_fields=["text_scale"])
    return redirect("chat:settings_tab", tab="general")


def data_export(request: HttpRequest) -> HttpResponse:
    import json

    from django.db.models import Prefetch

    # 2 requêtes au lieu de 1+N : conversations + messages préchargés,
    # colonnes utiles seules. HttpResponse conservé (pas de streaming) pour
    # ne pas casser le contrat existant (tests + Content-Disposition).
    convs = list(
        Conversation.objects.only("id", "title", "model_id", "temperature")
        .order_by("id")
        .prefetch_related(
            Prefetch(
                "messages",
                queryset=Message.objects.only(
                    "id", "conversation_id", "role", "content", "created_at"
                ).order_by("id"),
            )
        )
    )
    payload = [
        {
            "title": c.title,
            "model_id": c.model_id,
            "temperature": c.temperature,
            "messages": [
                {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                for m in c.messages.all()
            ],
        }
        for c in convs
    ]
    resp = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2), content_type="application/json")
    resp["Content-Disposition"] = 'attachment; filename="jejegpt-export.json"'
    return resp


@require_POST
def data_clear(request: HttpRequest) -> HttpResponse:
    """Efface TOUTES les conversations (cascade). Garde serveur obligatoire.

    Le confirm JS du template se contourne par curl : on exige confirm=SUPPRIMER
    côté serveur, sinon redirection sans suppression. Suppression en transaction
    atomique (réversible par ROLLBACK en cas d'erreur à mi-cascade).
    """
    from django.db import transaction

    if request.POST.get("confirm") != "SUPPRIMER":
        django_messages.error(request, "Confirmation requise : cochez la case avant d'effacer.")
        return redirect("chat:settings_tab", tab="donnees")
    with transaction.atomic():
        Conversation.objects.all().delete()
    django_messages.success(request, "Toutes les conversations ont été supprimées.")
    return redirect("chat:settings_tab", tab="donnees")


def export_download(request: HttpRequest, token) -> FileResponse:
    export = get_object_or_404(Export, download_token=token)
    return FileResponse(
        export.file.open("rb"),
        as_attachment=True,
        filename=export.file_name,
    )


@require_POST
def research_start(request: HttpRequest, pk: int) -> HttpResponse:
    """Lance une recherche approfondie depuis le contenu du composer (fetch JSON)."""
    import json as _json

    from .research import active_run_for, start_research

    conv = get_object_or_404(Conversation, pk=pk)
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )
    content = (request.POST.get("content") or "").strip()
    if not content:
        if wants_json:
            return JsonResponse({"error": "Sujet vide."}, status=400)
        django_messages.error(request, "Sujet vide.")
        return redirect("chat:detail", pk=conv.pk)
    existing = active_run_for(conv)
    if existing is not None and existing.conversation_id:
        target = existing.conversation.get_absolute_url()
        if wants_json:
            return JsonResponse({"redirect": target, "running": True})
        return redirect(target)
    run, error = start_research(conv, content)
    if run is None:
        if wants_json:
            return JsonResponse({"error": error}, status=400)
        django_messages.error(request, error)
        return redirect("chat:detail", pk=conv.pk)
    target = run.conversation.get_absolute_url()
    if wants_json:
        return JsonResponse({"redirect": target})
    return redirect(target)


def research_status(request: HttpRequest, pk: int) -> JsonResponse:
    """Statut d'un run de recherche pour le polling auto."""
    run = get_object_or_404(DeepResearchRun, pk=pk)
    return JsonResponse({
        "status": run.status,
        "step": run.step,
        "cycles_done": run.cycles_done,
        "max_cycles": run.max_cycles,
    })


@require_POST
def message_feedback(request: HttpRequest, mid: int) -> HttpResponse:
    """Note une réponse (pouce haut/bas), re-cliquer annule. Réservé aux réponses."""
    msg = get_object_or_404(Message, pk=mid)
    if msg.role != "assistant":
        return HttpResponse("Seule une réponse peut être notée.", status=400)
    value = (request.POST.get("value") or "").strip()
    if value not in ("up", "down", ""):
        return HttpResponse("Valeur invalide.", status=400)
    msg.feedback = "" if msg.feedback == value else value
    msg.save(update_fields=["feedback"])
    conv = msg.conversation
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/thread.html", _thread_ctx(conv))
    return redirect("chat:detail", pk=conv.pk)


def _templates_ctx() -> list:
    """Gabarits avec leurs variables, pour la section Gabarits."""
    templates = list(TaskTemplate.objects.all())
    for tpl in templates:
        tpl.var_list = tpl.variables()  # type: ignore[attr-defined]
    return templates


def _templates_json() -> list[dict]:
    """Gabarits sérialisés pour l'assistant de remplissage (json_script)."""
    return [
        {
            "id": tpl.pk,
            "name": tpl.name,
            "body": tpl.body,
            "variables": tpl.variables(),
            "suggest_web": bool(tpl.suggest_web),
        }
        for tpl in TaskTemplate.objects.all()
    ]


def _parse_template_data(request: HttpRequest) -> dict | None:
    """Valide un gabarit : nom requis, body avec au moins une {{variable}}."""
    from .models import template_variables

    name = (request.POST.get("name") or "").strip()[:160]
    description = (request.POST.get("description") or "").strip()[:300]
    body = (request.POST.get("body") or "").strip()
    if not name or not body:
        return None
    if not template_variables(body):
        return None
    return {
        "name": name,
        "description": description,
        "body": body,
        "suggest_web": bool(request.POST.get("suggest_web")),
    }


@require_POST
def template_create(request: HttpRequest) -> HttpResponse:
    data = _parse_template_data(request)
    if data is None:
        django_messages.error(request, "Gabarit invalide : nom requis et consigne avec au moins une variable {{…}}.")
        return redirect("chat:scheduled_list")
    if TaskTemplate.objects.filter(name=data["name"]).exists():
        django_messages.error(request, f"Un gabarit « {data['name']} » existe déjà.")
        return redirect("chat:scheduled_list")
    TaskTemplate.objects.create(**data)
    django_messages.success(request, f"Gabarit « {data['name']} » créé.")
    return redirect("chat:scheduled_list")


@require_POST
def template_update(request: HttpRequest, pk: int) -> HttpResponse:
    tpl = get_object_or_404(TaskTemplate, pk=pk)
    data = _parse_template_data(request)
    if data is None:
        django_messages.error(request, "Gabarit invalide : nom requis et consigne avec au moins une variable {{…}}.")
        return redirect("chat:scheduled_list")
    if TaskTemplate.objects.exclude(pk=pk).filter(name=data["name"]).exists():
        django_messages.error(request, f"Un gabarit « {data['name']} » existe déjà.")
        return redirect("chat:scheduled_list")
    for field, value in data.items():
        setattr(tpl, field, value)
    tpl.save()
    django_messages.success(request, f"Gabarit « {tpl.name} » mis à jour.")
    return redirect("chat:scheduled_list")


@require_POST
def template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    tpl = get_object_or_404(TaskTemplate, pk=pk)
    name = tpl.name
    tpl.delete()
    django_messages.success(request, f"Gabarit « {name} » supprimé.")
    return redirect("chat:scheduled_list")


def scheduled_list(request: HttpRequest) -> HttpResponse:
    from .scheduler.parse import describe_schedule

    conv = Conversation.objects.first()
    if conv is not None:
        ctx = _sidebar_ctx(conv)
    else:
        ctx = {
            "recent_convs": [],
            "older_convs": [],
            "active_project_id": None,
            "projects": list(
                Project.objects.annotate(
                    conv_count=models.Count("conversations")
                ).only("id", "name", "instructions")
            ),
            "assistants": list(
                Assistant.objects.annotate(conv_count=models.Count("conversations"))
            ),
        }
    from django.db.models import Prefetch

    tasks = list(
        ScheduledTask.objects.select_related("conversation").prefetch_related(
            # Pas de slice dans le Prefetch : [:5] s'appliquerait au total global,
            # pas à 5 par tâche. On précharge ordonné (colonnes utiles seules)
            # puis on tronque à 5 PAR tâche en Python, sans requête supplémentaire.
            Prefetch(
                "runs",
                queryset=ScheduledRun.objects.order_by("-created_at").only(
                    "id", "task_id", "success", "error", "cause",
                    "has_changes", "created_at", "message_id",
                ),
            )
        )
    )
    # Filet : si l'échec le plus récent n'est pas dans les 5 derniers runs,
    # 1 seule requête groupée au lieu de N (une par tâche en échec).
    tasks_with_failures = [t.pk for t in tasks if t.failures]
    fallback_failures: dict[int, ScheduledRun] = {}
    if tasks_with_failures:
        for run in (
            ScheduledRun.objects.filter(task_id__in=tasks_with_failures, success=False)
            .order_by("task_id", "-created_at")
            .only("id", "task_id", "success", "error", "created_at", "message_id")
        ):
            fallback_failures.setdefault(run.task_id, run)
    for task in tasks:
        days = [int(d) for d in (task.days or "").split(",") if d.strip().isdigit()]
        time_str = task.time.strftime("%H:%M") if task.time else None
        task.schedule_summary = describe_schedule(  # type: ignore[attr-defined]
            task.frequency, days, time_str, task.once_at, month_day=task.month_day
        )
        # Lecture du cache prefetch : aucune requête ici (runs déjà chargés).
        recent = list(task.runs.all()[:5])
        task.recent_runs = recent  # type: ignore[attr-defined]
        task.last_run = recent[0] if recent else None  # type: ignore[attr-defined]
        if task.failures:
            task.last_failure = next((r for r in recent if not r.success), None)  # type: ignore[attr-defined]
            if task.last_failure is None:  # type: ignore[attr-defined]
                task.last_failure = fallback_failures.get(task.pk)  # type: ignore[attr-defined]
        else:
            task.last_failure = None  # type: ignore[attr-defined]
        task.days_list = days  # type: ignore[attr-defined]
        task.time_str = time_str or ""  # type: ignore[attr-defined]
        if task.once_at is not None:
            task.once_at_str = timezone.localtime(task.once_at).strftime("%Y-%m-%dT%H:%M")  # type: ignore[attr-defined]
        else:
            task.once_at_str = ""  # type: ignore[attr-defined]
    active_providers = list(Provider.objects.filter(is_active=True).defer("api_key").prefetch_related("cached_models"))
    ctx.update({
        "conversation": conv,
        "tasks": tasks,
        "providers": active_providers,
        "models_by_provider": {
            str(provider.pk): [
                {"id": model.model_id, "label": model.display_name or model.model_id}
                for model in provider.cached_models.all()
            ]
            for provider in active_providers
        },
        "max_tasks": settings.JEJEGPT_SCHEDULER_MAX_TASKS,
        "max_failures": settings.JEJEGPT_SCHEDULER_MAX_FAILURES,
        "export_formats": ("pdf", "docx", "xlsx", "pptx"),
        "templates": _templates_ctx(),
        "templates_json": _templates_json(),
    })
    return render(request, "chat/scheduled_list.html", ctx)


@require_POST
def scheduled_create(request: HttpRequest) -> HttpResponse:
    from .models import TEMPLATE_VAR_RE
    from .scheduler.parse import validate_schedule_data

    if ScheduledTask.objects.count() >= settings.JEJEGPT_SCHEDULER_MAX_TASKS:
        django_messages.error(request, "Limite de tâches planifiées atteinte.")
        return redirect("chat:scheduled_list")
    try:
        provider = Provider.objects.get(pk=int(request.POST.get("provider") or 0), is_active=True)
    except (Provider.DoesNotExist, ValueError, TypeError):
        django_messages.error(request, "Choisissez un provider actif.")
        return redirect("chat:scheduled_list")
    model_id = (request.POST.get("model_id") or "").strip()[:300]
    if not model_id:
        django_messages.error(request, "Indiquez un modèle.")
        return redirect("chat:scheduled_list")
    data = {
        "name": (request.POST.get("name") or "").strip(),
        "prompt": (request.POST.get("prompt") or "").strip(),
        "frequency": (request.POST.get("frequency") or "daily").strip(),
        "days": [int(d) for d in request.POST.getlist("days") if d.isdigit()],
        "month_day": _parse_month_day(request),
        "time": (request.POST.get("time") or "").strip() or None,
        "once_at": (request.POST.get("once_at") or "").strip().replace("T", " ")[:16] or None,
    }
    parsed = validate_schedule_data(data)
    if parsed is None:
        django_messages.error(request, "Planification invalide : vérifiez fréquence, jours, heure et jour du mois.")
        return redirect("chat:scheduled_list")
    if TEMPLATE_VAR_RE.search(parsed["prompt"]):
        django_messages.error(request, "Il reste des variables {{…}} non renseignées dans la consigne.")
        return redirect("chat:scheduled_list")
    trigger, trigger_error = _parse_trigger(request)
    if trigger is None:
        django_messages.error(request, trigger_error)
        return redirect("chat:scheduled_list")
    sites, sites_error = _parse_sites(request)
    if sites is None:
        django_messages.error(request, sites_error)
        return redirect("chat:scheduled_list")
    task_conv = Conversation.objects.create(
        title=parsed["name"][:200], provider=provider, model_id=model_id,
        system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        temperature=settings.JEJEGPT_DEFAULT_TEMPERATURE,
        web_search=bool(request.POST.get("web_search")),
    )
    _create_scheduled_task(
        name=parsed["name"], prompt=parsed["prompt"], frequency=parsed["frequency"],
        days=parsed["days"], month_day=parsed["month_day"],
        time_str=parsed["time"], once_at=parsed["once_at"],
        provider=provider, model_id=model_id, temperature=settings.JEJEGPT_DEFAULT_TEMPERATURE,
        web_search=bool(request.POST.get("web_search")), conversation=task_conv,
        enabled=bool(request.POST.get("enabled")),
        auto_export=_parse_auto_export(request),
        trigger_url=trigger["trigger_url"], trigger_mode=trigger["trigger_mode"],
        trigger_keyword=trigger["trigger_keyword"], sites=sites,
    )
    django_messages.success(request, f"Tâche « {parsed['name']} » créée.")
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_update(request: HttpRequest, pk: int) -> HttpResponse:
    from .models import TEMPLATE_VAR_RE
    from .scheduler.engine import compute_next_run, dtime_from_str
    from .scheduler.parse import validate_schedule_data

    task = get_object_or_404(ScheduledTask, pk=pk)
    try:
        provider = Provider.objects.get(pk=int(request.POST.get("provider") or 0), is_active=True)
    except (Provider.DoesNotExist, ValueError, TypeError):
        django_messages.error(request, "Choisissez un provider actif.")
        return redirect("chat:scheduled_list")
    model_id = (request.POST.get("model_id") or "").strip()[:300]
    if not model_id:
        django_messages.error(request, "Indiquez un modèle.")
        return redirect("chat:scheduled_list")
    data = {
        "name": (request.POST.get("name") or "").strip(),
        "prompt": (request.POST.get("prompt") or "").strip(),
        "frequency": (request.POST.get("frequency") or "daily").strip(),
        "days": [int(d) for d in request.POST.getlist("days") if d.isdigit()],
        "month_day": _parse_month_day(request),
        "time": (request.POST.get("time") or "").strip() or None,
        "once_at": (request.POST.get("once_at") or "").strip().replace("T", " ")[:16] or None,
    }
    parsed = validate_schedule_data(data)
    if parsed is None:
        django_messages.error(request, "Planification invalide : vérifiez fréquence, jours, heure et jour du mois.")
        return redirect("chat:scheduled_list")
    if TEMPLATE_VAR_RE.search(parsed["prompt"]):
        django_messages.error(request, "Il reste des variables {{…}} non renseignées dans la consigne.")
        return redirect("chat:scheduled_list")
    trigger, trigger_error = _parse_trigger(request)
    if trigger is None:
        django_messages.error(request, trigger_error)
        return redirect("chat:scheduled_list")
    sites, sites_error = _parse_sites(request)
    if sites is None:
        django_messages.error(request, sites_error)
        return redirect("chat:scheduled_list")
    web_search = bool(request.POST.get("web_search"))
    enabled = bool(request.POST.get("enabled"))
    task.name = parsed["name"][:160]
    task.prompt = parsed["prompt"]
    task.frequency = parsed["frequency"]
    task.days = ",".join(str(d) for d in (parsed["days"] or []))
    task.month_day = parsed["month_day"]
    task.time = dtime_from_str(parsed["time"]) if parsed["time"] else None
    task.once_at = parsed["once_at"]
    task.provider = provider
    task.model_id = model_id
    task.web_search = web_search
    task.enabled = enabled
    task.auto_export = _parse_auto_export(request)
    task.trigger_url = trigger["trigger_url"]
    task.trigger_mode = trigger["trigger_mode"]
    task.trigger_keyword = trigger["trigger_keyword"]
    task.sites = sites
    task.next_run = compute_next_run(
        parsed["frequency"], parsed["days"], parsed["time"], parsed["once_at"],
        timezone.now(), month_day=parsed["month_day"],
    )
    if task.next_run is None:
        task.enabled = False
    task.failures = 0
    task.save(update_fields=[
        "name", "prompt", "frequency", "days", "month_day", "time", "once_at",
        "provider", "model_id", "web_search", "enabled", "next_run", "failures",
        "auto_export", "trigger_url", "trigger_mode", "trigger_keyword", "sites",
    ])
    if task.conversation_id:
        conv = task.conversation
        conv.title = parsed["name"][:200]
        conv.provider = provider
        conv.model_id = model_id
        conv.web_search = web_search
        conv.save(update_fields=["title", "provider", "model_id", "web_search", "updated_at"])
    django_messages.success(request, f"Tâche « {parsed['name']} » mise à jour.")
    return redirect("chat:scheduled_list")


def _parse_auto_export(request: HttpRequest) -> str:
    """Format d'export auto demandé, '' si aucun. Jamais invalide."""
    from .exports.service import FORMATS

    fmt = (request.POST.get("auto_export") or "").strip()
    return fmt if fmt in FORMATS else ""


def _parse_month_day(request: HttpRequest) -> int | None:
    """Jour du mois 1-28 (mensuel), None sinon. La validation stricte est côté parse."""
    try:
        value = int((request.POST.get("month_day") or "").strip())
    except (ValueError, TypeError):
        return None
    return value if 1 <= value <= 28 else None


def _parse_trigger(request: HttpRequest) -> tuple[dict | None, str]:
    """Surveillance d'URL : (data, erreur). Data vide = désactivée."""
    from .scheduler.engine import trigger_url_allowed

    url = (request.POST.get("trigger_url") or "").strip()[:500]
    if not url:
        return {"trigger_url": "", "trigger_mode": "change", "trigger_keyword": ""}, ""
    if not trigger_url_allowed(url):
        return None, "URL de surveillance refusée (http(s) public uniquement, pas de réseau local)."
    mode = (request.POST.get("trigger_mode") or "change").strip()
    if mode not in ("change", "keyword"):
        mode = "change"
    keyword = (request.POST.get("trigger_keyword") or "").strip()[:200]
    if mode == "keyword" and not keyword:
        return None, "Mot-clé requis pour une surveillance par mot-clé."
    return {"trigger_url": url, "trigger_mode": mode, "trigger_keyword": keyword}, ""


def _parse_sites(request: HttpRequest) -> tuple[str | None, str]:
    """Domaines restreints (un par ligne) : (texte propre, erreur)."""
    from .scheduler.engine import parse_sites

    domains, error = parse_sites(request.POST.get("sites") or "")
    if error:
        return None, error
    return "\n".join(domains), ""


def scheduled_badge(request: HttpRequest) -> JsonResponse:
    """Compteur de runs non lus pour l'actualisation auto de la pastille."""
    count = ScheduledRun.objects.filter(seen=False).count()
    return JsonResponse({"unseen": count})


@require_POST
def scheduled_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    from .scheduler.engine import compute_next_run

    task = get_object_or_404(ScheduledTask, pk=pk)
    task.enabled = not task.enabled
    if task.enabled:
        task.next_run = compute_next_run(
            task.frequency, task.days, task.time, task.once_at, timezone.now(),
            month_day=task.month_day,
        )
        task.failures = 0
        task.save(update_fields=["enabled", "next_run", "failures"])
    else:
        task.save(update_fields=["enabled"])
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_run_now(request: HttpRequest, pk: int) -> HttpResponse:
    from .scheduler.engine import run_task_now

    if run_task_now(pk):
        django_messages.success(request, "Exécution lancée, résultat dans la conversation dédiée.")
    else:
        django_messages.error(request, "Tâche introuvable ou déjà en cours.")
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_delete(request: HttpRequest, pk: int) -> HttpResponse:
    task = get_object_or_404(ScheduledTask, pk=pk)
    name = task.name
    task.delete()
    django_messages.success(request, f"Tâche « {name} » supprimée (conversation dédiée incluse).")
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_reset_failures(request: HttpRequest, pk: int) -> HttpResponse:
    """Remet le compteur d'échecs à zéro sans changer l'état activé/pause."""
    task = get_object_or_404(ScheduledTask, pk=pk)
    task.failures = 0
    task.save(update_fields=["failures"])
    django_messages.success(request, f"Compteur d'échecs de « {task.name} » réinitialisé.")
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_duplicate(request: HttpRequest, pk: int) -> HttpResponse:
    """Duplique une tâche (nouvelle conversation dédiée, en pause)."""
    from .scheduler.engine import compute_next_run

    if ScheduledTask.objects.count() >= settings.JEJEGPT_SCHEDULER_MAX_TASKS:
        django_messages.error(request, "Limite de tâches planifiées atteinte.")
        return redirect("chat:scheduled_list")
    task = get_object_or_404(ScheduledTask, pk=pk)
    name = f"{task.name} (copie)"[:160]
    task_conv = Conversation.objects.create(
        title=name[:200], provider=task.provider, model_id=task.model_id,
        system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        temperature=task.temperature,
        web_search=task.web_search,
    )
    days = [int(d) for d in (task.days or "").split(",") if d.strip().isdigit()]
    time_str = task.time.strftime("%H:%M") if task.time else None
    copy = ScheduledTask.objects.create(
        name=name, prompt=task.prompt, frequency=task.frequency, days=task.days,
        month_day=task.month_day,
        time=task.time, once_at=task.once_at, provider=task.provider,
        model_id=task.model_id, temperature=task.temperature,
        web_search=task.web_search, auto_export=task.auto_export,
        trigger_url=task.trigger_url, trigger_mode=task.trigger_mode,
        trigger_keyword=task.trigger_keyword, sites=task.sites,
        conversation=task_conv, enabled=False, failures=0,
    )
    copy.next_run = compute_next_run(
        task.frequency, days, time_str,
        task.once_at, timezone.now(), month_day=task.month_day,
    )
    copy.save(update_fields=["next_run"])
    django_messages.success(request, f"Tâche « {task.name} » dupliquée (en pause).")
    return redirect("chat:scheduled_list")


@require_POST
def scheduled_draft(request: HttpRequest, pk: int) -> HttpResponse:
    """Exécute un brouillon : un run unique sans toucher planification ni échecs."""
    from .scheduler.engine import run_task_draft

    if run_task_draft(pk):
        django_messages.success(request, "Brouillon exécuté, résultat dans la conversation dédiée.")
    else:
        django_messages.error(request, "Tâche introuvable.")
    return redirect("chat:scheduled_list")


@require_POST
def export_create(request: HttpRequest, mid: int, fmt: str) -> HttpResponse:
    from .exports.service import FORMATS, create_export

    message = get_object_or_404(Message, pk=mid)
    if fmt not in FORMATS:
        django_messages.error(request, f"Format d'export inconnu : {fmt}.")
        return redirect("chat:detail", pk=message.conversation_id)
    try:
        export = create_export(message, fmt, message.conversation.title, message.content or "")
    except (ValidationError, ValueError) as exc:
        detail_msg = getattr(exc, "message", None) or str(exc)
        django_messages.error(request, f"Export impossible : {detail_msg}")
        return redirect("chat:detail", pk=message.conversation_id)
    return redirect(export.get_absolute_url())


@require_POST
def newsletter_create(request: HttpRequest, mid: int) -> HttpResponse:
    """Génère une synthèse newsletter d'une réponse, sans toucher au fil.

    Renouvelle les exports html + eml liés au message source puis redirige
    vers la conversation (cartes de téléchargement + actions mailto/copie).
    """
    from .exports.service import create_export
    from .newsletter import build_newsletter_text

    message = get_object_or_404(Message, pk=mid)
    conv = message.conversation
    if message.role != "assistant" or not (message.content or "").strip():
        django_messages.error(request, "La newsletter se crée depuis une réponse.")
        return redirect("chat:detail", pk=conv.pk)
    if conv.provider is None or not conv.model_id:
        django_messages.error(
            request, "Configurez un provider et un modèle pour générer la newsletter."
        )
        return redirect("chat:detail", pk=conv.pk)
    try:
        newsletter_md = build_newsletter_text(conv, message.content or "")
    except providers.ProviderError as exc:
        django_messages.error(request, f"Newsletter impossible : {exc.message}")
        return redirect("chat:detail", pk=conv.pk)
    try:
        message.exports.filter(format__in=("html", "eml")).delete()
        create_export(message, "html", f"Newsletter — {conv.title}", newsletter_md)
        create_export(message, "eml", f"Newsletter — {conv.title}", newsletter_md)
    except (ValidationError, ValueError) as exc:
        detail_msg = getattr(exc, "message", None) or str(exc)
        django_messages.error(request, f"Newsletter impossible : {detail_msg}")
        return redirect("chat:detail", pk=conv.pk)
    django_messages.success(request, "Newsletter prête : Outlook (mailto), Copier HTML ou .eml ci-dessous.")
    return redirect("chat:detail", pk=conv.pk)


def _generate_reply(conv: Conversation) -> tuple[bool, str]:
    """Appelle le provider, persiste la réponse (ou l'erreur inline).

    Retourne (succès, message d'erreur). Remplit et retourne aussi l'usage.
    """
    usage = providers._empty_usage()
    try:
        reply = providers.chat_complete(
            conv.provider, conv.model_id, _history(conv),
            temperature=conv.temperature, timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=conv.pk,
            route=providers.resolve_route(conv.provider, conv.model_id),
            options=_conv_options(conv),
            usage_collector=usage,
        )
        Message.objects.create(conversation=conv, role="assistant", content=reply,
                               model_label=f"{conv.provider.name} / {conv.model_id}"
                               if conv.provider else (conv.model_id or ""))
    except providers.ProviderError as exc:
        Message.objects.create(conversation=conv, role="assistant", content="", error=exc.message)
        _record_usage(conv, usage, success=False, error=exc.message)
        conv.save()
        return False, exc.message
    conv.save()
    _record_usage(conv, usage, success=True)
    # .only() sûr : seul content est lu ci-dessous.
    last_user = conv.messages.only("content").filter(role="user").order_by("-created_at", "-id").first()
    _maybe_extract_memory(conv, last_user.content if last_user else "", reply)
    _maybe_auto_images(conv, last_user.content if last_user else "")
    return True, ""


def _maybe_extract_memory(conv: Conversation, user_text: str, reply: str) -> None:
    """Mémorise les faits durables depuis le dernier échange. Jamais bloquant."""
    from .memory import maybe_extract

    try:
        maybe_extract(conv, user_text, reply)
    except Exception:
        logger.exception("memory hook failed")


def _maybe_auto_export(conv: Conversation, user_text: str) -> None:
    """Crée un Export depuis la dernière réponse si l'utilisateur a demandé un fichier.

    Ex. 'Génère moi un pdf qui liste…' → PDF joint sous la réponse. Idempotent par
    format (pas de doublon si la réponse a déjà cet export) et silencieux en échec :
    l'export reste disponible via les boutons manuels.
    """
    from .exports.service import create_export, detect_export_request

    try:
        fmt = detect_export_request(user_text)
        if fmt is None:
            return
        assistant = conv.messages.filter(role="assistant").exclude(content="").last()
        if assistant is None or not assistant.content:
            return
        if assistant.exports.filter(format=fmt).exists():
            return
        create_export(assistant, fmt, conv.title, assistant.content)
    except Exception:
        logger.exception("auto export failed")


def _maybe_auto_images(conv: Conversation, user_text: str) -> None:
    """Enrichit la dernière réponse avec une galerie web si pertinent.

    Déclenché si le toggle Images est ON et que le sujet s'y prête
    (ex. ruines sous-marines No Man's Sky). Silencieux en échec :
    la réponse texte reste toujours livrée.
    """
    from .image_search import images_enabled, images_markdown, needs_images, search_images

    try:
        if not getattr(conv, "show_images", True):
            logger.info("auto images skip: toggle off (conv=%s)", conv.pk)
            return
        if not images_enabled():
            logger.info("auto images skip: recherche désactivée (provider/auto)")
            return
        if not needs_images(user_text):
            logger.info("auto images skip: sujet non-visuel")
            return
        assistant = conv.messages.filter(role="assistant").exclude(content="").last()
        if assistant is None or not assistant.content:
            logger.info("auto images skip: pas de réponse à enrichir")
            return
        if "🖼️ **Images**" in assistant.content:
            logger.info("auto images skip: galerie déjà présente")
            return
        results = search_images(user_text)
        block = images_markdown(results)
        if not block:
            logger.warning("auto images: 0 résultat pour %r", (user_text or "")[:80])
            return
        assistant.content = assistant.content.rstrip() + "\n" + block + "\n"
        assistant.save(update_fields=["content"])
        logger.info("auto images: galerie %d image(s) ajoutée (conv=%s)", len(results), conv.pk)
    except Exception:
        logger.exception("auto images failed")


def _maybe_schedule(conv: Conversation, user_text: str) -> None:
    """Crée une tâche planifiée (en pause) si l'utilisateur en demande une.

    Parsing 100 % LLM, validation stricte : incompréhensible → message de clarification,
    jamais de tâche. La tâche démarre désactivée : l'utilisateur l'active dans Planifiées.
    """
    from .scheduler.parse import (
        describe_schedule,
        detect_schedule_intent,
        needs_web_search,
        parse_schedule_request,
    )

    try:
        if not detect_schedule_intent(user_text):
            return
        if conv.provider is None or not conv.model_id:
            return
        parsed = parse_schedule_request(conv.provider, conv.model_id, user_text)
        if parsed is None:
            Message.objects.create(
                conversation=conv, role="assistant",
                content="Je n'ai pas compris la planification. Précisez la fréquence et l'heure, "
                "par exemple « tous les jours à 10h » ou « chaque lundi à 8h30 ».",
            )
            return
        if ScheduledTask.objects.count() >= settings.JEJEGPT_SCHEDULER_MAX_TASKS:
            Message.objects.create(
                conversation=conv, role="assistant",
                content="Limite de tâches planifiées atteinte "
                f"({settings.JEJEGPT_SCHEDULER_MAX_TASKS}). Supprimez-en une dans Planifiées.",
            )
            return
        try:
            web_supported = bool(providers.effective_caps(conv.provider, conv.model_id).get("web"))
        except Exception:
            web_supported = False
        web = needs_web_search(user_text) and web_supported
        task_conv = Conversation.objects.create(
            title=parsed["name"][:200],
            provider=conv.provider,
            model_id=conv.model_id,
            system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
            temperature=conv.temperature,
            web_search=web,
        )
        _create_scheduled_task(
            name=parsed["name"], prompt=parsed["prompt"], frequency=parsed["frequency"],
            days=parsed["days"], month_day=parsed.get("month_day"), time_str=parsed["time"],
            once_at=parsed["once_at"],
            provider=conv.provider, model_id=conv.model_id, temperature=conv.temperature,
            web_search=web, conversation=task_conv, enabled=False,
        )
        summary = describe_schedule(
            parsed["frequency"], parsed["days"], parsed["time"], parsed["once_at"],
            month_day=parsed.get("month_day"),
        )
        web_note = " (avec recherche web)" if web else ""
        Message.objects.create(
            conversation=conv, role="assistant",
            content=f"✅ Tâche **{parsed['name']}** créée (en pause) : {summary}{web_note}.\n\n"
            "Activez-la dans [Planifiées](/planifiees/) quand vous voulez.",
        )
    except Exception:
        logger.exception("schedule from chat failed")


def _create_scheduled_task(name, prompt, frequency, days, time_str, once_at, provider,
                           model_id, temperature, web_search, conversation, enabled,
                           auto_export="", month_day=None, trigger_url="",
                           trigger_mode="change", trigger_keyword="", sites=""):
    from .scheduler.engine import compute_next_run, dtime_from_str

    time_value = dtime_from_str(time_str) if time_str else None
    days_str = ",".join(str(d) for d in (days or []))
    task = ScheduledTask.objects.create(
        name=name[:160], prompt=prompt, frequency=frequency, days=days_str,
        month_day=month_day,
        time=time_value, once_at=once_at, provider=provider, model_id=model_id,
        temperature=temperature, web_search=web_search, conversation=conversation,
        enabled=enabled, auto_export=auto_export,
        trigger_url=trigger_url, trigger_mode=trigger_mode,
        trigger_keyword=trigger_keyword, sites=sites,
    )
    task.next_run = compute_next_run(
        frequency, days or [], time_str, once_at, timezone.now(), month_day=month_day
    )
    if task.next_run is None:
        task.enabled = False
    task.save(update_fields=["next_run", "enabled"])
    return task


def _record_usage(conv: Conversation, usage: dict, success: bool = True, error: str = "") -> None:
    """Persiste une ligne d'usage sans jamais faire échouer l'envoi."""
    try:
        UsageRecord.objects.create(
            conversation=conv,
            provider=conv.provider,
            model_id=conv.model_id,
            route=providers.resolve_route(conv.provider, conv.model_id),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            duration_ms=int(usage.get("duration_ms") or 0),
            success=success,
            error=(error or "")[:300],
        )
    except Exception:
        logger.exception("usage persist failed")


def _delete_following(conv: Conversation, msg: Message) -> None:
    # DELETE direct, sans charger tous les IDs en Python. Sémantique identique
    # à l'ordre (created_at, id) : tout ce qui suit strictement msg est supprimé.
    # 1 requête au lieu de 2 + pic mémoire sur les longs fils.
    conv.messages.filter(
        models.Q(created_at__gt=msg.created_at)
        | models.Q(created_at=msg.created_at, id__gt=msg.id)
    ).delete()


@require_POST
def message_edit(request: HttpRequest, mid: int) -> HttpResponse:
    """Modifie une question, supprime la suite et régénère la réponse."""
    msg = get_object_or_404(Message, pk=mid)
    if msg.role != "user":
        return HttpResponse("Seule une question peut être modifiée.", status=400)
    content = (request.POST.get("content") or "").strip()
    if not content:
        return HttpResponse("Message vide.", status=400)
    conv = msg.conversation
    msg.content = content
    msg.save(update_fields=["content"])
    _delete_following(conv, msg)
    _generate_reply(conv)
    return render(request, "chat/partials/thread.html", _thread_ctx(conv))


def _regen_anchor(msg: Message) -> Message | None:
    """Question d'ancrage pour régénérer : le message lui-même si c'est une
    question, sinon la question précédente (clic sous une réponse)."""
    if msg.role == "user":
        return msg
    ordered = list(msg.conversation.messages.order_by("created_at", "id"))
    ids = [m.id for m in ordered]
    try:
        idx = ids.index(msg.id)
    except ValueError:
        return None
    for m in reversed(ordered[:idx]):
        if m.role == "user":
            return m
    return None


@require_POST
def message_regenerate_from(request: HttpRequest, mid: int) -> HttpResponse:
    """Régénère la réponse à partir d'une question ou d'une réponse
    (ancre = la question correspondante, suite supprimée)."""
    msg = get_object_or_404(Message, pk=mid)
    anchor = _regen_anchor(msg)
    if anchor is None:
        return HttpResponse("Aucune question à régénérer.", status=400)
    conv = msg.conversation
    _delete_following(conv, anchor)
    _generate_reply(conv)
    return render(request, "chat/partials/thread.html", _thread_ctx(conv))


@require_POST
def message_send(request: HttpRequest, pk: int) -> HttpResponse:
    """Crée le message user, appelle le provider en non-streamé puis rend le thread.

    Le streaming temps réel passe par l'endpoint SSE (message_stream) ; ici on
    persiste immédiatement pour garder une V1 robuste même sans JS SSE.
    """
    conv = get_object_or_404(Conversation, pk=pk)
    content = (request.POST.get("content") or "").strip()
    if not content:
        return HttpResponse("Message vide.", status=400)
    if not conv.provider or not conv.model_id:
        Message.objects.create(conversation=conv, role="user", content=content)
        Message.objects.create(
            conversation=conv, role="assistant", content="",
            error="Aucun provider/modèle sélectionné. Ajoutez un provider puis Actualiser.",
        )
        conv.save()
    else:
        Message.objects.create(conversation=conv, role="user", content=content)
        _generate_reply(conv)
        _maybe_auto_export(conv, content)
        _maybe_schedule(conv, content)
        if conv.title == "Nouvelle conversation":
            conv.title = content[:60]
    conv.save()
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/thread.html", _thread_ctx(conv))
    return redirect("chat:detail", pk=conv.pk)


@require_POST
def message_regenerate(request: HttpRequest, pk: int) -> HttpResponse:
    conv = get_object_or_404(Conversation, pk=pk)
    last_user = conv.messages.filter(role="user").last()
    if last_user is None:
        return HttpResponse("Rien à régénérer.", status=400)
    conv.messages.filter(role="assistant", content="", error="").delete()
    _generate_reply(conv)
    return render(request, "chat/partials/thread.html", _thread_ctx(conv))


def _thread_ctx(conv: Conversation, notice: str = "") -> dict:
    last_a = conv.messages.filter(role="assistant").exclude(content="").order_by(
        "created_at", "id").last()
    return {
        "conversation": conv,
        "messages": _messages_ctx(conv),
        "providers": Provider.objects.filter(is_active=True).defer("api_key").prefetch_related("cached_models"),
        "followups": _followups_for(last_a.content if last_a else ""),
        "pinned_messages": list(conv.messages.filter(pinned=True).order_by("created_at")[:10]),
        "notice": notice,
    }


def thread_partial(request: HttpRequest, pk: int) -> HttpResponse:
    """Rend uniquement le thread (cible du rafraîchissement post-stream)."""
    conv = get_object_or_404(Conversation, pk=pk)
    return render(request, "chat/partials/thread.html", _thread_ctx(conv))


def message_stream(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    """Endpoint SSE : stream les tokens puis persiste le message assistant complet."""
    conv = get_object_or_404(Conversation, pk=pk)
    prompt = (request.GET.get("prompt") or "").strip()
    if not conv.provider or not conv.model_id:
        def err():
            yield 'event: error\ndata: {"message": "Aucun provider/modèle sélectionné."}\n\n'
        return StreamingHttpResponse(err(), content_type="text/event-stream")

    hist = _history(conv)
    if prompt and not any(m["role"] == "user" and m["content"] == prompt for m in hist):
        from .scores import score_extra_message

        Message.objects.create(conversation=conv, role="user", content=prompt)
        hist.append({"role": "user", "content": prompt})
        if (extra := score_extra_message(prompt)) is not None:
            hist.append(extra)

    def gen():
        full: list[str] = []
        reasoning_parts: list[str] = []
        flushed = 0
        usage = providers._empty_usage()

        def _flush_reasoning():
            nonlocal flushed
            while flushed < len(reasoning_parts):
                import json as _j
                yield f"event: reasoning\ndata: {_j.dumps({'token': reasoning_parts[flushed]})}\n\n"
                flushed += 1

        def _on_reasoning(text: str) -> None:
            reasoning_parts.append(text)

        try:
            for tok in providers.chat_stream(
                conv.provider, conv.model_id, hist,
                temperature=conv.temperature, timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
                session_id=conv.pk,
                route=providers.resolve_route(conv.provider, conv.model_id),
                options=_conv_options(conv),
                usage_collector=usage,
                reasoning_callback=_on_reasoning,
            ):
                full.append(tok)
                import json as _j
                yield f"data: {_j.dumps({'token': tok})}\n\n"
                yield from _flush_reasoning()
        except providers.ProviderError as exc:
            import json as _j
            yield f"event: error\ndata: {_j.dumps({'message': exc.message})}\n\n"
            Message.objects.create(
                conversation=conv, role="assistant", content="".join(full),
                reasoning="".join(reasoning_parts), error=exc.message,
            )
            _record_usage(conv, usage, success=False, error=exc.message)
        else:
            yield from _flush_reasoning()
            Message.objects.create(
                conversation=conv, role="assistant", content="".join(full),
                reasoning="".join(reasoning_parts),
                model_label=f"{conv.provider.name} / {conv.model_id}"
                if conv.provider else (conv.model_id or ""),
            )
            _record_usage(conv, usage, success=True)
            last_user = conv.messages.filter(role="user").last()
            user_text = prompt or (last_user.content if last_user else "")
            _maybe_auto_export(conv, user_text)
            _maybe_schedule(conv, user_text)
            _maybe_auto_images(conv, user_text)
            _maybe_extract_memory(conv, user_text, "".join(full))
            if conv.title == "Nouvelle conversation" and prompt:
                conv.title = prompt[:60]
                conv.save(update_fields=["title", "updated_at"])
        yield "event: done\ndata: [DONE]\n\n"

    resp = StreamingHttpResponse(gen(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    return resp


# ================= Enrichissements (Canvas, épingles, fork, comparo, upload, recherche) ============

@require_POST
def message_pin(request: HttpRequest, mid: int) -> HttpResponse:
    """Épingle / désépingle un message (sommaire + recherche)."""
    msg = get_object_or_404(Message, pk=mid)
    msg.pinned = not msg.pinned
    msg.save(update_fields=["pinned"])
    conv = msg.conversation
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/thread.html", _thread_ctx(conv))
    return redirect("chat:detail", pk=conv.pk)


@require_POST
def conversation_fork(request: HttpRequest, mid: int) -> HttpResponse:
    """Fork : nouvelle conversation copiant l'historique jusqu'au message donné."""
    from django.db import transaction

    msg = get_object_or_404(Message.objects.select_related("conversation"), pk=mid)
    src = msg.conversation
    # Colonnes copiées seules (mêmes 4 champs qu'avant : role, content,
    # reasoning, model_label — aucun changement fonctionnel, pas de signaux
    # sur Message donc bulk_create sûr ici).
    ordered = list(
        src.messages.only("role", "content", "reasoning", "model_label")
        .order_by("created_at", "id")
    )
    ids = [m.pk for m in ordered]
    try:
        idx = ids.index(msg.pk)
    except ValueError:
        idx = len(ordered) - 1
    with transaction.atomic():
        fork = Conversation.objects.create(
            title=f"{src.title} (fork)".strip()[:200],
            provider=src.provider, model_id=src.model_id,
            system_prompt=src.system_prompt, temperature=src.temperature,
            reasoning_effort=src.reasoning_effort, web_search=src.web_search,
            thinking_enabled=src.thinking_enabled, show_images=src.show_images,
            project=src.project, assistant=src.assistant,
        )
        Message.objects.bulk_create(
            [
                Message(conversation=fork, role=m.role, content=m.content,
                        reasoning=m.reasoning, model_label=m.model_label)
                for m in ordered[:idx + 1]
            ],
            batch_size=500,
        )
    return redirect("chat:detail", pk=fork.pk)


@require_POST
def message_retry_with(request: HttpRequest, mid: int) -> HttpResponse:
    """Rejoue la question d'ancrage avec un autre provider/modèle (comparo A-B).

    Non destructif : la nouvelle réponse est une version sœur (parent commun),
    l'ancienne reste visible avec badge V1/V2 + label modèle.
    """
    msg = get_object_or_404(Message.objects.select_related("conversation"), pk=mid)
    anchor = _regen_anchor(msg)
    conv = msg.conversation

    def _fail(text: str) -> HttpResponse:
        # HTMX ne swap que le 2xx : on renvoie le thread (200) avec bannière
        # d'erreur visible, sinon le clic semble ne rien faire.
        if request.headers.get("HX-Request"):
            return render(request, "chat/partials/thread.html", _thread_ctx(conv, notice=text))
        django_messages.error(request, text)
        return redirect("chat:detail", pk=conv.pk)

    if anchor is None:
        return _fail("Aucune question à rejouer.")
    try:
        alt_provider = Provider.objects.get(
            pk=int(request.POST.get("provider_id") or 0), is_active=True)
    except (Provider.DoesNotExist, ValueError, TypeError):
        return _fail("Provider invalide ou inactif.")
    alt_model = (request.POST.get("model_id") or "").strip()[:300]
    if not alt_model or not alt_provider.cached_models.filter(model_id=alt_model).exists():
        return _fail("Modèle inconnu pour ce provider (Actualiser d'abord).")
    hist = _history(conv)
    # Tronque l'historique à l'ancre pour rejouer à contexte identique.
    keep: list[dict] = []
    for h in hist:
        keep.append(h)
        if h["role"] == "user" and h["content"] == anchor.content:
            break
    usage = providers._empty_usage()
    try:
        reply = providers.chat_complete(
            alt_provider, alt_model, keep,
            temperature=conv.temperature, timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=conv.pk,
            route=providers.resolve_route(alt_provider, alt_model),
            options=_conv_options(conv),
            usage_collector=usage,
        )
    except providers.ProviderError as exc:
        _record_usage(conv, usage, success=False, error=exc.message)
        return _fail(f"Rejeu impossible : {exc.message}")
    existing = conv.messages.filter(parent=anchor).count()
    Message.objects.create(
        conversation=conv, role="assistant", content=reply,
        parent=anchor, version_no=existing + 2 if existing else 2,
        model_label=f"{alt_provider.name} / {alt_model}",
    )
    _record_usage(conv, usage, success=True)
    conv.save()
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/thread.html", _thread_ctx(conv))
    return redirect("chat:detail", pk=conv.pk)


def global_search(request: HttpRequest) -> HttpResponse:
    """Recherche plein-texte SQLite (titres + contenus), sans index externe."""
    # LIKE '%q%' non indexable par construction : on borne q pour limiter le
    # coût du scan (pas de FTS tant que le volume ne le justifie pas — cf. SQL-009).
    q = (request.GET.get("q") or "").strip()[:80]
    results: list = []
    convs = []
    if len(q) >= 2:
        convs = list(Conversation.objects.filter(title__icontains=q)[:10])
        results = list(
            Message.objects.select_related("conversation")
            .filter(content__icontains=q).order_by("-created_at")[:30]
        )
    conv = Conversation.objects.first()
    ctx = _sidebar_ctx(conv) if conv is not None else {
        "recent_convs": [], "older_convs": [],
        "active_project_id": None, "projects": [], "assistants": [],
    }
    ctx.update({"conversation": conv, "query": q, "found_convs": convs, "results": results})
    return render(request, "chat/search.html", ctx)


def snippets_api(request: HttpRequest) -> JsonResponse:
    """Snippets pour le '/' du composer : slash + gabarits + starters."""
    conv = None
    if pk := (request.GET.get("conversation") or "").strip():
        try:
            conv = Conversation.objects.select_related("assistant").get(pk=int(pk))
        except (Conversation.DoesNotExist, ValueError):
            conv = None
    return JsonResponse({"snippets": _snippets_json(conv)})


# ---- Canvas (Document) ----

@require_POST
def document_open(request: HttpRequest, pk: int) -> HttpResponse:
    """Crée/ouvre le Canvas depuis une réponse (ou vierge)."""
    conv = get_object_or_404(Conversation, pk=pk)
    doc = conv.documents.first()
    if doc is None:
        seed = ""
        if mid := (request.POST.get("message_id") or "").strip():
            try:
                seed = Message.objects.get(pk=int(mid), conversation=conv).content or ""
            except (Message.DoesNotExist, ValueError):
                seed = ""
        from .models import Document
        doc = Document.objects.create(
            conversation=conv, title=(conv.title or "Document")[:200], content=seed)
    if request.headers.get("HX-Request"):
        return render(request, "chat/partials/canvas.html",
                      {"conversation": conv, "document": doc})
    return redirect("chat:detail", pk=conv.pk)


@require_POST
def document_save(request: HttpRequest, pk: int) -> JsonResponse:
    """Sauvegarde manuelle du Canvas (édition type ChatGPT)."""
    from .models import Document
    doc = get_object_or_404(Document.objects.select_related("conversation"), pk=pk)
    doc.content = request.POST.get("content") or ""
    if title := (request.POST.get("title") or "").strip()[:200]:
        doc.title = title
    doc.version += 1
    doc.save(update_fields=["content", "title", "version", "updated_at"])
    return JsonResponse({"ok": True, "version": doc.version})


CANVAS_ACTIONS = {
    "raccourcir": "Raccourcis ce document de moitié, sans perdre les idées clés.",
    "allonger": "Développe ce document avec exemples concrets et détails utiles.",
    "ton-pro": "Réécris dans un ton professionnel direct.",
    "corriger": "Corrige orthographe, grammaire et clarté, sans changer le fond.",
    "traduire-en": "Traduis en anglais, ton professionnel.",
}


@require_POST
def document_action(request: HttpRequest, pk: int) -> JsonResponse:
    """Applique une action IA au Canvas via le LLM de la conversation."""
    from .models import Document
    doc = get_object_or_404(Document.objects.select_related("conversation"), pk=pk)
    conv = doc.conversation
    action = (request.POST.get("action") or "").strip()
    custom = (request.POST.get("custom") or "").strip()
    if action == "custom":
        instruction = custom[:500] if custom else ""
    else:
        instruction = CANVAS_ACTIONS.get(action, "")
    if not instruction:
        return JsonResponse({"error": "Action inconnue."}, status=400)
    if conv.provider is None or not conv.model_id:
        return JsonResponse({"error": "Configurez un provider et un modèle."}, status=400)
    usage = providers._empty_usage()
    try:
        out = providers.chat_complete(
            conv.provider, conv.model_id,
            [{"role": "user",
              "content": f"{instruction}\n\n--- DOCUMENT ---\n{doc.content[:12000]}"}],
            temperature=conv.temperature, timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=conv.pk,
            route=providers.resolve_route(conv.provider, conv.model_id),
            options=_conv_options(conv),
            usage_collector=usage,
        )
    except providers.ProviderError as exc:
        return JsonResponse({"error": exc.message}, status=400)
    doc.content = out
    doc.version += 1
    doc.save(update_fields=["content", "version", "updated_at"])
    _record_usage(conv, usage, success=True)
    return JsonResponse({"ok": True, "content": out, "version": doc.version})


# ---- Pièces jointes ----

ATTACH_MAX_BYTES = 512 * 1024
ATTACH_EXTS = {".txt", ".md", ".csv", ".json", ".html", ".docx"}


def _extract_attachment_text(path, name: str) -> str:
    low = (name or "").lower()
    if low.endswith(".docx"):
        try:
            from docx import Document as DocxDoc
            return "\n".join(p.text for p in DocxDoc(path).paragraphs)[:8000]
        except Exception:
            return ""
    try:
        return open(path, encoding="utf-8", errors="replace").read()[:8000]
    except Exception:
        try:
            return open(path, encoding="latin-1", errors="replace").read()[:8000]
        except Exception:
            return ""


@require_POST
def attachment_upload(request: HttpRequest, pk: int) -> HttpResponse:
    """Upload local borné (texte uniquement, pas de RAG ni d'OCR)."""
    from pathlib import Path

    from .models import Attachment

    conv = get_object_or_404(Conversation, pk=pk)
    up = request.FILES.get("file")
    if up is None:
        django_messages.error(request, "Aucun fichier reçu.")
        return redirect("chat:detail", pk=conv.pk)
    suffix = Path(up.name or "").suffix.lower()
    if suffix not in ATTACH_EXTS:
        django_messages.error(
            request, f"Format refusé ({suffix or 'sans extension'}). "
            "Acceptés : txt, md, csv, json, html, docx.")
        return redirect("chat:detail", pk=conv.pk)
    if up.size > ATTACH_MAX_BYTES:
        django_messages.error(request, "Fichier trop lourd (512 Ko max).")
        return redirect("chat:detail", pk=conv.pk)
    att = Attachment(conversation=conv, file=up, file_name=up.name[:200],
                     mime=up.content_type or "", size=up.size)
    att.save()
    try:
        att.excerpt = _extract_attachment_text(att.file.path, att.file_name)
        att.save(update_fields=["excerpt"])
    except Exception:
        logger.exception("attachment extract failed")
    django_messages.success(request, f"« {att.file_name} » joint (contexte, 6000 car. max).")
    return redirect("chat:detail", pk=conv.pk)


@require_POST
def attachment_delete(request: HttpRequest, pk: int) -> HttpResponse:
    from .models import Attachment
    att = get_object_or_404(Attachment.objects.select_related("conversation"), pk=pk)
    conv_id = att.conversation_id
    try:
        att.file.delete(save=False)
    except Exception:
        pass
    att.delete()
    return redirect("chat:detail", pk=conv_id)
