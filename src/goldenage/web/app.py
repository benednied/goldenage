"""FastAPI entrypoint."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goldenage.adapters.demo import (
    HeuristicElizabethanSearchClient,
    HeuristicGiselaClient,
    InMemoryActivityRepository,
    InMemoryArtifactRepository,
    InMemoryAuditRepository,
    InMemoryCaseRepository,
    LocalArtifactStore,
    OutlookMsgExtractor,
    build_demo_state,
)
from goldenage.adapters.outlook_mailbox import OutlookMailboxWorker, WindowsOutlookMailboxSource
from goldenage.adapters.postgres import (
    PostgresActivityRepository,
    PostgresArtifactRepository,
    PostgresAuditRepository,
    PostgresCaseRepository,
)
from goldenage.adapters.sqlite import (
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    SQLiteCaseRepository,
    SQLiteLocalUserRepository,
)
from goldenage.application.use_cases import (
    CaseDetail,
    GoldenAgeService,
    IntakeState,
    NotFoundError,
)
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.config import Settings, load_settings
from goldenage.domain.models import UserContext
from goldenage.domain.rules import ResolutionError, due_label

BASE_DIR = Path(__file__).resolve().parent
UNSUPPORTED_INTAKE_MESSAGE = "not supported in this mvp for now"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@dataclass(frozen=True, slots=True)
class AppContext:
    """State shared by route handlers."""

    settings: Settings
    service: GoldenAgeService
    default_user: UserContext | None
    local_user_repository: SQLiteLocalUserRepository | None = None
    outlook_worker: OutlookMailboxWorker | None = None


def create_app() -> FastAPI:
    """Application factory."""
    settings = load_settings()
    context = _build_context(settings)

    app = FastAPI(title="GoldenAge")
    app.state.context = context
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    if settings.use_local_first_sqlite:
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/profiles", StaticFiles(directory=str(settings.profile_dir)), name="profiles")

    @app.on_event("startup")
    async def startup_event() -> None:
        if context.outlook_worker is not None:
            context.outlook_worker.start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        if context.outlook_worker is not None:
            context.outlook_worker.stop()

    @app.get("/", response_class=HTMLResponse)
    async def home() -> RedirectResponse:
        if settings.use_local_first_sqlite and _current_user(context) is None:
            return RedirectResponse(url="/onboarding", status_code=302)
        return RedirectResponse(url="/worklist", status_code=302)

    @app.get("/onboarding", response_class=HTMLResponse)
    async def onboarding(request: Request) -> HTMLResponse:
        if not settings.use_local_first_sqlite:
            return RedirectResponse(url="/worklist", status_code=302)
        if _current_user(context) is not None:
            return RedirectResponse(url="/worklist", status_code=302)
        return templates.TemplateResponse(
            request=request,
            name="onboarding.html",
            context=_onboarding_context(request, message=None),
        )

    @app.post("/onboarding", response_class=HTMLResponse)
    async def create_onboarding_user(
        request: Request,
        display_name: str = Form(default=""),
        email: str = Form(default=""),
        password: str = Form(default=""),
        profile_picture: UploadFile | None = File(default=None),
    ) -> HTMLResponse:
        if not settings.use_local_first_sqlite:
            return RedirectResponse(url="/worklist", status_code=302)
        if _current_user(context) is not None:
            return RedirectResponse(url="/worklist", status_code=302)

        normalized_name = display_name.strip()
        normalized_email = email.strip().lower()
        if not normalized_name or not normalized_email or not password:
            return templates.TemplateResponse(
                request=request,
                name="onboarding.html",
                context=_onboarding_context(
                    request,
                    message="Display name, email, and password are required.",
                ),
                status_code=400,
            )

        profile_image_path = await _store_profile_picture(
            profile_picture=profile_picture,
            settings=settings,
        )
        if profile_picture is not None and profile_image_path is None:
            return templates.TemplateResponse(
                request=request,
                name="onboarding.html",
                context=_onboarding_context(
                    request,
                    message="Profile picture uploads must be image files.",
                ),
                status_code=400,
            )

        local_user_repository = context.local_user_repository
        if local_user_repository is None:
            raise HTTPException(status_code=500, detail="Local-first onboarding is not configured.")
        local_user_repository.create_user(
            account_id=uuid4(),
            email=normalized_email,
            display_name=normalized_name,
            password_hash=_hash_password(password),
            profile_image_path=profile_image_path,
        )
        return RedirectResponse(url="/worklist", status_code=303)

    @app.get("/worklist", response_class=HTMLResponse)
    async def worklist(request: Request, case_id: str | None = None) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        detail = _load_case_detail(case_id=case_id, context=context, now=_now(context), user=user)
        return templates.TemplateResponse(
            request=request,
            name="page.html",
            context=_page_context(request, context, detail=detail, intake_state=IntakeState(), user=user),
        )

    @app.get("/cases/{case_id}/panel", response_class=HTMLResponse)
    async def case_panel(request: Request, case_id: str) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        detail = _load_case_detail(case_id=case_id, context=context, now=_now(context), user=user)
        return templates.TemplateResponse(
            request=request,
            name="partials/detail_panel.html",
            context=_panel_context(request, context, detail=detail, detail_error=None, user=user),
        )

    @app.get("/cases/{case_id}/artifacts/{artifact_id}/panel", response_class=HTMLResponse)
    async def artifact_panel(request: Request, case_id: str, artifact_id: str) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        try:
            detail = context.service.get_case_detail(
                case_id=_uuid(case_id),
                user=user,
                now=_now(context),
                selected_artifact_id=_uuid(artifact_id),
            )
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return templates.TemplateResponse(
            request=request,
            name="partials/detail_panel.html",
            context=_panel_context(request, context, detail=detail, detail_error=None, user=user),
        )

    @app.post("/activities/{activity_id}/resolve", response_class=HTMLResponse)
    async def resolve_activity(
        request: Request,
        activity_id: str,
        next_step: str = Form(default=""),
        next_due_at: str = Form(default=""),
        close_case: str | None = Form(default=None),
        skip_follow_up: str | None = Form(default=None),
    ) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        try:
            detail = context.service.resolve_activity(
                activity_id=_uuid(activity_id),
                user=user,
                now=_now(context),
                next_step=next_step,
                next_due_at=_parse_form_datetime(next_due_at, context.settings.local_timezone),
                close_case=close_case == "on",
                skip_follow_up=skip_follow_up == "on",
            )
        except ResolutionError as error:
            current = context.service.get_case_detail_for_activity(
                activity_id=_uuid(activity_id),
                user=user,
                now=_now(context),
            )
            response = templates.TemplateResponse(
                request=request,
                name="partials/detail_panel.html",
                context=_panel_context(
                    request,
                    context,
                    detail=current,
                    detail_error=str(error),
                    user=user,
                ),
                status_code=400,
            )
            return response
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return templates.TemplateResponse(
            request=request,
            name="workspace.html",
            context=_page_context(
                request,
                context,
                detail=detail,
                intake_state=IntakeState(message="Activity resolved. The worklist has been updated."),
                user=user,
            ),
        )

    @app.post("/artifacts/upload", response_class=HTMLResponse)
    async def upload_artifact(
        request: Request,
        file: UploadFile = File(...),
    ) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        content = await file.read()
        if not content:
            response = templates.TemplateResponse(
                request=request,
                name="partials/intake_panel.html",
                context=_page_context(
                    request,
                    context,
                    detail=None,
                    intake_state=IntakeState(message="Select a file before uploading."),
                    user=user,
                ),
                status_code=400,
            )
            return response

        if not (file.filename or "").lower().endswith(".msg"):
            return _unsupported_upload_response(request, context, user=user)

        intake_state = context.service.upload_artifact(
            file_name=file.filename or "upload.bin",
            media_type=file.content_type or "application/octet-stream",
            content=content,
            user=user,
            now=_now(context),
        )
        return templates.TemplateResponse(
            request=request,
            name="partials/intake_panel.html",
            context=_page_context(request, context, detail=None, intake_state=intake_state, user=user),
        )

    @app.post("/artifacts/{artifact_id}/suggestion/reject", response_class=HTMLResponse)
    async def reject_suggestion(request: Request, artifact_id: str) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        intake_state = context.service.get_intake_state(
            artifact_id=_uuid(artifact_id),
            user=user,
        )
        intake_state = IntakeState(
            artifact=intake_state.artifact,
            suggestion=intake_state.suggestion,
            search_mode=True,
            message="Suggestion rejected. Describe the case to search for it.",
        )
        return templates.TemplateResponse(
            request=request,
            name="partials/intake_panel.html",
            context=_page_context(request, context, detail=None, intake_state=intake_state, user=user),
        )

    @app.get("/intake/conversations/{conversation_id}", response_class=HTMLResponse)
    async def intake_conversation_panel(request: Request, conversation_id: str) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        intake_state = context.service.get_intake_state_for_conversation(
            conversation_id=_uuid(conversation_id),
            user=user,
        )
        return templates.TemplateResponse(
            request=request,
            name="partials/intake_panel.html",
            context=_page_context(request, context, detail=None, intake_state=intake_state, user=user),
        )

    @app.post("/cases/search", response_class=HTMLResponse)
    async def search_cases(
        request: Request,
        artifact_id: str = Form(...),
        query: str = Form(...),
    ) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        intake_state = context.service.search_cases_for_artifact(
            artifact_id=_uuid(artifact_id),
            query=query,
            user=user,
            now=_now(context),
        )
        return templates.TemplateResponse(
            request=request,
            name="partials/intake_panel.html",
            context=_page_context(request, context, detail=None, intake_state=intake_state, user=user),
        )

    @app.post("/artifacts/{artifact_id}/assign", response_class=HTMLResponse)
    async def assign_artifact(
        request: Request,
        artifact_id: str,
        case_id: str = Form(...),
        next_step: str = Form(...),
        next_due_at: str = Form(...),
    ) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        try:
            detail = context.service.assign_artifact_to_case(
                artifact_id=_uuid(artifact_id),
                case_id=_uuid(case_id),
                next_step=next_step,
                next_due_at=_require_form_datetime(next_due_at, context.settings.local_timezone),
                user=user,
                now=_now(context),
            )
        except ResolutionError as error:
            intake_state = context.service.get_intake_state(
                artifact_id=_uuid(artifact_id),
                user=user,
            )
            intake_state = IntakeState(
                artifact=intake_state.artifact,
                suggestion=intake_state.suggestion,
                search_mode=True,
                message=str(error),
            )
            response = templates.TemplateResponse(
                request=request,
                name="partials/intake_panel.html",
                context=_page_context(
                    request,
                    context,
                    detail=None,
                    intake_state=intake_state,
                    user=user,
                ),
                status_code=400,
            )
            response.headers["HX-Retarget"] = "#intake-panel"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return templates.TemplateResponse(
            request=request,
            name="workspace.html",
            context=_page_context(
                request,
                context,
                detail=detail,
                intake_state=IntakeState(message="Artifact assigned and next step scheduled."),
                user=user,
            ),
        )

    @app.post("/artifacts/{artifact_id}/create-case", response_class=HTMLResponse)
    async def create_case_from_artifact(
        request: Request,
        artifact_id: str,
        title: str = Form(...),
        company: str = Form(default=""),
        primary_contact: str = Form(default=""),
        next_step: str = Form(...),
        next_due_at: str = Form(...),
    ) -> HTMLResponse:
        if (redirect := _redirect_to_onboarding_if_needed(context)) is not None:
            return redirect
        user = _require_current_user(context)
        try:
            detail = context.service.create_case_for_artifact(
                artifact_id=_uuid(artifact_id),
                title=title,
                company=company,
                primary_contact=primary_contact,
                next_step=next_step,
                next_due_at=_require_form_datetime(next_due_at, context.settings.local_timezone),
                user=user,
                now=_now(context),
            )
        except ResolutionError as error:
            intake_state = context.service.get_intake_state(
                artifact_id=_uuid(artifact_id),
                user=user,
            )
            intake_state = IntakeState(
                artifact=intake_state.artifact,
                suggestion=intake_state.suggestion,
                search_mode=True,
                search_query=intake_state.search_query,
                search_results=intake_state.search_results,
                message=str(error),
            )
            response = templates.TemplateResponse(
                request=request,
                name="partials/intake_panel.html",
                context=_page_context(
                    request,
                    context,
                    detail=None,
                    intake_state=intake_state,
                    user=user,
                ),
                status_code=400,
            )
            response.headers["HX-Retarget"] = "#intake-panel"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return templates.TemplateResponse(
            request=request,
            name="workspace.html",
            context=_page_context(
                request,
                context,
                detail=detail,
                intake_state=IntakeState(message="New case created from intake."),
                user=user,
            ),
        )

    return app


def _build_context(settings: Settings) -> AppContext:
    if settings.use_local_first_sqlite:
        if settings.sqlite_path is None:
            raise RuntimeError("GOLDENAGE_SQLITE_PATH must be set for local-first SQLite mode.")
        ensure_sqlite_bootstrapped(settings.sqlite_path)
        case_repository = SQLiteCaseRepository(settings.sqlite_path)
        activity_repository = SQLiteActivityRepository(settings.sqlite_path)
        artifact_repository = SQLiteArtifactRepository(settings.sqlite_path)
        audit_repository = SQLiteAuditRepository(settings.sqlite_path)
        local_user_repository = SQLiteLocalUserRepository(settings.sqlite_path)
        default_user = None
    elif settings.database_url:
        case_repository = PostgresCaseRepository(settings.database_url)
        activity_repository = PostgresActivityRepository(settings.database_url)
        artifact_repository = PostgresArtifactRepository(settings.database_url)
        audit_repository = PostgresAuditRepository(settings.database_url)
        default_user = UserContext(
            id=_uuid("11111111-1111-1111-1111-111111111111"),
            email="alex@example.com",
            display_name="Alex Example",
        )
        local_user_repository = None
    else:
        state, user = build_demo_state()
        case_repository = InMemoryCaseRepository(state)
        activity_repository = InMemoryActivityRepository(state, case_repository)
        artifact_repository = InMemoryArtifactRepository(state, case_repository)
        audit_repository = InMemoryAuditRepository(state)
        default_user = user
        local_user_repository = None

    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=activity_repository,
        artifact_repository=artifact_repository,
        audit_repository=audit_repository,
        artifact_store=LocalArtifactStore(settings.artifact_dir),
        content_extractor=OutlookMsgExtractor(),
        gisela_client=HeuristicGiselaClient(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
    )
    outlook_worker = None
    if settings.outlook_sync_enabled and settings.outlook_account_name:
        source = WindowsOutlookMailboxSource(
            settings,
            on_message=lambda message: None,
        )
        outlook_worker = OutlookMailboxWorker(source)
    return AppContext(
        settings=settings,
        service=service,
        default_user=default_user,
        local_user_repository=local_user_repository,
        outlook_worker=outlook_worker,
    )


def _unsupported_upload_response(
    request: Request,
    context: AppContext,
    *,
    user: UserContext,
) -> HTMLResponse:
    print(UNSUPPORTED_INTAKE_MESSAGE, flush=True)
    return templates.TemplateResponse(
        request=request,
        name="partials/intake_panel.html",
        context=_page_context(
            request,
            context,
            detail=None,
            intake_state=IntakeState(message=UNSUPPORTED_INTAKE_MESSAGE),
            user=user,
        ),
        status_code=400,
    )


def _page_context(
    request: Request,
    context: AppContext,
    *,
    detail: CaseDetail | None,
    intake_state: IntakeState,
    user: UserContext,
) -> dict[str, object]:
    hydrated_intake = _hydrate_intake_state(context, intake_state=intake_state, user=user)
    return {
        "request": request,
        "page_title": "GoldenAge",
        "hero_title": (
            "welcome to the golden age" if context.settings.use_local_first_sqlite else "GoldenAge"
        ),
        "user": user,
        "profile_image_url": _profile_image_url(user),
        "worklist": context.service.get_today_worklist(
            user=user,
            now=_worklist_cutoff(context),
        ),
        "detail": detail,
        "detail_error": None,
        "intake_state": hydrated_intake,
        "format_datetime": _format_datetime,
        "due_label": due_label,
        "local_timezone": context.settings.local_timezone,
    }


def _panel_context(
    request: Request,
    context: AppContext,
    *,
    detail: CaseDetail | None,
    detail_error: str | None,
    user: UserContext,
) -> dict[str, object]:
    payload = _page_context(request, context, detail=detail, intake_state=IntakeState(), user=user)
    payload["detail_error"] = detail_error
    return payload


def _load_case_detail(
    *,
    case_id: str | None,
    context: AppContext,
    now: datetime,
    user: UserContext,
) -> CaseDetail | None:
    if not case_id:
        return None
    try:
        return context.service.get_case_detail(case_id=_uuid(case_id), user=user, now=now)
    except NotFoundError:
        return None


def _hydrate_intake_state(
    context: AppContext,
    *,
    intake_state: IntakeState,
    user: UserContext,
) -> IntakeState:
    if intake_state.recent_conversations:
        return intake_state
    fallback = context.service.get_recent_intake(user=user)
    return IntakeState(
        artifact=intake_state.artifact,
        suggestion=intake_state.suggestion,
        search_mode=intake_state.search_mode,
        search_query=intake_state.search_query,
        search_results=intake_state.search_results,
        message=intake_state.message,
        conversation=intake_state.conversation,
        conversation_artifacts=intake_state.conversation_artifacts,
        recent_conversations=fallback.recent_conversations,
        ingest_status=intake_state.ingest_status,
    )


def _onboarding_context(request: Request, message: str | None) -> dict[str, object]:
    return {
        "request": request,
        "page_title": "Welcome to the Golden Age",
        "message": message,
    }


def _current_user(context: AppContext) -> UserContext | None:
    if context.local_user_repository is not None:
        account = context.local_user_repository.get_first_user()
        return account.to_user_context() if account is not None else None
    return context.default_user


def _require_current_user(context: AppContext) -> UserContext:
    user = _current_user(context)
    if user is None:
        raise HTTPException(status_code=503, detail="No user is configured.")
    return user


def _redirect_to_onboarding_if_needed(context: AppContext) -> RedirectResponse | None:
    if context.settings.use_local_first_sqlite and _current_user(context) is None:
        return RedirectResponse(url="/onboarding", status_code=302)
    return None


async def _store_profile_picture(
    *,
    profile_picture: UploadFile | None,
    settings: Settings,
) -> str | None:
    if profile_picture is None:
        return None
    if not (profile_picture.content_type or "").startswith("image/"):
        return None
    content = await profile_picture.read()
    if not content:
        return None
    suffix = Path(profile_picture.filename or "profile.bin").suffix or ".bin"
    file_name = f"{uuid4()}{suffix.lower()}"
    target = settings.profile_dir / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return file_name


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        600_000,
    ).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def _profile_image_url(user: UserContext) -> str | None:
    if user.profile_image_path is None:
        return None
    return f"/profiles/{user.profile_image_path}"


def _now(context: AppContext) -> datetime:
    return datetime.now(ZoneInfo(context.settings.local_timezone)).astimezone(UTC)


def _worklist_cutoff(context: AppContext) -> datetime:
    local_zone = ZoneInfo(context.settings.local_timezone)
    local_now = datetime.now(local_zone)
    local_end_of_day = datetime.combine(local_now.date(), time.max, tzinfo=local_zone)
    return local_end_of_day.astimezone(UTC)


def _format_datetime(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "n/a"
    local_value = value.astimezone(ZoneInfo(timezone_name))
    return local_value.strftime("%d.%m.%Y %H:%M")


def _parse_form_datetime(value: str, timezone_name: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(UTC)


def _require_form_datetime(value: str, timezone_name: str) -> datetime:
    parsed = _parse_form_datetime(value, timezone_name)
    if parsed is None:
        raise ResolutionError("A due date is required.")
    return parsed


def _uuid(raw_value: str):
    from uuid import UUID

    return UUID(raw_value)
