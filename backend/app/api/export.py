from fastapi import APIRouter, BackgroundTasks, Query
from app.services.export.orchestrator import orchestrate_mm_export
from app.services.export.user_exporter import UserExporter
from app.logging_config import backend_logger
from app.utils.env import env_is_truthy

router = APIRouter()


@router.post("/export")
async def start_export(
    background_tasks: BackgroundTasks,
    job_id: int | None = Query(
        default=None,
        description="Anchor job id: ограничить FIFO продвижение экспортом только для джоб, созданных не позже указанной",
    ),
):
    if job_id is not None:
        backend_logger.info(f"Запуск экспорта с якорем job_id={job_id}")
        background_tasks.add_task(orchestrate_mm_export, job_id=job_id)
        return {
            "status": "export_started",
            "message": f"Экспорт (anchor job_id={job_id}) запущен в фоне",
        }
    backend_logger.info("Запуск экспорта всех доступных задач (без anchor)")
    background_tasks.add_task(orchestrate_mm_export)
    return {
        "status": "export_started",
        "message": "Экспорт (global) запущен в фоновом режиме",
    }


@router.get("/export/config")
async def get_export_config():
    skip_attachments = env_is_truthy("SKIP_ATTACHMENT_EXPORT")
    payload = {
        "skip_attachment_export": skip_attachments,
    }
    if skip_attachments:
        payload["attachment_skip_reason"] = (
            "Attachment export disabled via SKIP_ATTACHMENT_EXPORT"
        )

    # Check bot creation configuration
    try:
        # Create temporary UserExporter instance to check config
        # We don't need a real entity, just access to the config check method
        class DummyEntity:
            def __init__(self):
                self.slack_id = "config_check"
                self.raw_data = {}
                self.mattermost_id = None
                self.entity_type = "user"

        dummy_entity = DummyEntity()
        exporter = UserExporter(dummy_entity)
        bot_creation_enabled = await exporter._is_bot_creation_enabled()

        payload["bot_creation_enabled"] = bot_creation_enabled
        if bot_creation_enabled:
            payload["bot_creation_mode"] = "bot_accounts"
            payload["bot_creation_message"] = (
                "Slack-боты будут экспортироваться как Bot Accounts в Mattermost"
            )
        else:
            payload["bot_creation_mode"] = "regular_users"
            payload["bot_creation_message"] = (
                "Slack-боты будут экспортироваться как обычные пользователи (EnableBotAccountCreation отключён в Mattermost)"
            )
    except Exception as e:
        backend_logger.warning(f"Failed to check bot creation config: {e}")
        # Default to enabled if check fails
        payload["bot_creation_enabled"] = True
        payload["bot_creation_mode"] = "bot_accounts"
        payload["bot_creation_message"] = (
            "Slack-боты будут экспортироваться как Bot Accounts в Mattermost"
        )

    return payload
