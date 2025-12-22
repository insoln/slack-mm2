import os
from app.logging_config import backend_logger
from app.services.backup.meta_utils import merge_job_meta
from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin


class ChannelExporter(ExporterBase, LoggingMixin, MMApiMixin):
    def __init__(self, entity):
        super().__init__(entity)
        self._cached_team_id = None

    # Resilience / semantics notes:
    # 1. Duplicate public/private channel names: treat "already exists" (or HTTP 409) as success by
    #    looking up existing id and marking entity success (prevents cascade failures for messages).
    # 2. DM requires exactly 2 mapped members. Otherwise mark skipped (dataset structural issue).
    # 3. GDM (mpim) rules:
    #    - <3 members -> skipped (invalid dataset)
    #    - 3..8 members -> create via /gdm plugin endpoint
    #    - >8 members -> downgrade to private channel creation path (acts like normal channel)
    #    - Fallback: if only 2 members try adding USLACKBOT to reach threshold when possible.
    # 4. Participant count structural errors from server mapped to skipped not failed.
    # 5. Archived slack channel => archive in Mattermost after creation.

    def _get_channel_name(self, raw_data):
        """Получить название канала из raw_data"""
        return raw_data.get("name") if raw_data else None

    def _get_channel_display_name(self, raw_data):
        """Получить отображаемое название канала"""
        name = self._get_channel_name(raw_data)
        if not name:
            return None

        # Для DM каналов (начинаются с D) используем специальную логику
        if name.startswith("D"):
            return f"DM-{name}"

        # Для обычных каналов используем оригинальное название
        return name

    def _sanitize_display_name(
        self, display_name: str | None, fallback_name: str
    ) -> str:
        """Ограничить DisplayName до допустимых значений MM (<=64 символа, без переводов строк).
        Если не задано — используем fallback_name. Триммим пробелы, \n/\r заменяем на пробел.
        """
        val = display_name or fallback_name or "channel"
        if not isinstance(val, str):
            val = str(val)
        # Уберем переводы строк и контрольные символы (минимально)
        val = val.replace("\r", " ").replace("\n", " ").strip()
        # Порог 64 символа — обрежем по символам (unicode)
        if len(val) > 64:
            val = val[:64]
        # MM требует непустой DisplayName
        if not val:
            val = (fallback_name or "channel")[:64]
        return val

    def _get_channel_purpose(self, raw_data):
        """Получить описание канала"""
        purpose = raw_data.get("purpose", {}) if raw_data else {}
        return purpose.get("value", "") if purpose else ""

    def _get_channel_header(self, raw_data):
        """Получить заголовок канала"""
        topic = raw_data.get("topic", {}) if raw_data else {}
        return topic.get("value", "") if topic else ""

    def _is_dm_channel(self, raw_data):
        """Проверить, является ли канал DM"""
        return raw_data.get("id", "").startswith("D") if raw_data else False

    def _is_group_dm_channel(self, raw_data):
        """Проверить, является ли канал групповым DM (mpim в Slack)."""
        if not raw_data:
            return False
        # Slack экспорт помечает групповые диалоги флагом is_mpim
        if bool(raw_data.get("is_mpim")):
            return True
        # Фоллбек: иногда mpim приходит как канал с именем, начинающимся на 'mpdm-'
        nm = (raw_data or {}).get("name") or ""
        return nm.startswith("mpdm-")

    def _is_private_channel(self, raw_data):
        """Проверить, является ли канал приватным"""
        # В Slack приватные каналы имеют ID начинающийся с G
        return raw_data.get("id", "").startswith("G") if raw_data else False

    async def export_entity(self):
        self.log_export(f"Экспорт канала/диалога {self.entity.slack_id}")
        try:
            is_dm = self._is_dm_channel(self.entity.raw_data)
            is_gdm = self._is_group_dm_channel(self.entity.raw_data)
            is_private = self._is_private_channel(self.entity.raw_data)

            # DM path
            if is_dm:
                members = (self.entity.raw_data or {}).get("members") or []
                mm_user_ids = await self._resolve_mm_user_ids(members)
                if len(mm_user_ids) == 2:
                    dm_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/dm", {"user_ids": mm_user_ids}
                    )
                    if getattr(dm_resp, "status_code", None) in (200, 201):
                        try:
                            dm_data = dm_resp.json()  # type: ignore[attr-defined]
                        except Exception:
                            backend_logger.error(
                                f"Плагин вернул не-JSON для DM: status={getattr(dm_resp,'status_code',None)} body={getattr(dm_resp,'text','')[:200]}"
                            )
                            await self.set_status(
                                "failed",
                                error=f"Plugin invalid JSON for DM: {getattr(dm_resp,'status_code',None)}",
                            )
                            return
                        self.entity.mattermost_id = dm_data.get("channel_id")
                        await self.set_status("success")
                        backend_logger.debug(
                            f"DM канал создан/получен, ID: {self.entity.mattermost_id}"
                        )
                        return
                    backend_logger.error(
                        f"Ошибка создания DM через плагин: {getattr(dm_resp,'status_code',None)} {getattr(dm_resp,'text','')}"
                    )
                    await self.set_status("failed", error=getattr(dm_resp, "text", ""))
                    return
                backend_logger.warning(
                    f"Ожидалось 2 участника DM, найдено {len(mm_user_ids)}; пропускаю"
                )
                await self.set_status("skipped", error="Invalid DM members count")
                return

            # Group DM (mpim) path
            if is_gdm:
                members = (self.entity.raw_data or {}).get("members") or []
                mm_user_ids = await self._resolve_mm_user_ids(members)
                if len(mm_user_ids) == 2:  # attempt to pad with USLACKBOT
                    try:
                        slackbot_mm = await self._resolve_mm_user_ids(["USLACKBOT"])
                        if slackbot_mm:
                            backend_logger.debug(
                                "Добавляем USLACKBOT в 2-участниковый MPIM для соответствия порогу"
                            )
                            mm_user_ids.append(slackbot_mm[0])
                    except Exception:
                        pass
                if len(mm_user_ids) > 8:
                    backend_logger.info(
                        f"MPIM с {len(mm_user_ids)} участниками преобразован в приватный канал"
                    )
                    is_gdm = False  # fall through to normal channel creation
                elif len(mm_user_ids) >= 3:
                    gdm_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/gdm", {"user_ids": mm_user_ids}
                    )
                    if getattr(gdm_resp, "status_code", None) in (200, 201):
                        try:
                            gdm_data = gdm_resp.json()  # type: ignore[attr-defined]
                        except Exception:
                            backend_logger.error(
                                f"Плагин вернул не-JSON для GDM: status={getattr(gdm_resp,'status_code',None)} body={getattr(gdm_resp,'text','')[:200]}"
                            )
                            await self.set_status(
                                "failed",
                                error=f"Plugin invalid JSON for GDM: {getattr(gdm_resp,'status_code',None)}",
                            )
                            return
                        self.entity.mattermost_id = gdm_data.get("channel_id")
                        await self.set_status("success")
                        backend_logger.debug(
                            f"GDM канал создан/получен, ID: {self.entity.mattermost_id}"
                        )
                        return
                    backend_logger.error(
                        f"Ошибка создания GDM через плагин: {getattr(gdm_resp,'status_code',None)} {getattr(gdm_resp,'text','')}"
                    )
                    await self.set_status("failed", error=getattr(gdm_resp, "text", ""))
                    return
                else:
                    backend_logger.warning(
                        f"Слишком мало участников для GDM: {len(mm_user_ids)}; пропускаю (need >=3)"
                    )
                    await self.set_status(
                        "skipped", error="Insufficient GDM members (<3)"
                    )
                    return

            # Normal (public/private) channel path
            channel_name = self._get_channel_name(self.entity.raw_data)
            if not channel_name:
                backend_logger.error(f"Нет названия для канала {self.entity.slack_id}")
                await self.set_status(
                    "failed",
                    error="No channel name found in raw_data for non-DM channel",
                )
                return

            self.log_export(f"Экспорт канала {channel_name}")
            team_id = await self._get_mm_team_id()
            safe_display = self._sanitize_display_name(
                self._get_channel_display_name(self.entity.raw_data),
                channel_name.replace("-", " "),
            )
            if not is_gdm and self._is_group_dm_channel(self.entity.raw_data):
                safe_display = f"MPIM {self.entity.slack_id}"[:64]
                is_private = True
            payload = {
                "team_id": team_id,
                "name": channel_name,
                "display_name": safe_display,
                "type": "P" if is_private else "O",
            }
            purpose = self._get_channel_purpose(self.entity.raw_data)
            if purpose:
                payload["purpose"] = purpose
            header = self._get_channel_header(self.entity.raw_data)
            if header:
                payload["header"] = header

            response = await self.mm_api_post(
                "/plugins/mm-importer/api/v1/channel", payload
            )
            if getattr(response, "status_code", None) in (200, 201):
                try:
                    channel_data = response.json()  # type: ignore[attr-defined]
                except Exception:
                    backend_logger.error(
                        f"Плагин вернул не-JSON для channel: status={getattr(response,'status_code',None)} body={getattr(response,'text','')[:200]}"
                    )
                    await self.set_status(
                        "failed",
                        error=f"Plugin invalid JSON for channel: {getattr(response,'status_code',None)}",
                    )
                    return
                self.entity.mattermost_id = channel_data.get(
                    "channel_id"
                ) or channel_data.get("id")
                members = (self.entity.raw_data or {}).get("members") or []
                if members:
                    mm_user_ids = await self._resolve_mm_user_ids(members)
                    if mm_user_ids:
                        await self._ensure_team_membership(mm_user_ids)
                        add_resp = await self.mm_api_post(
                            "/plugins/mm-importer/api/v1/channel/members",
                            {
                                "channel_id": self.entity.mattermost_id,
                                "user_ids": mm_user_ids,
                            },
                        )
                        if getattr(add_resp, "status_code", None) not in (200, 201):
                            backend_logger.error(
                                f"Не удалось добавить участников: {getattr(add_resp,'status_code',None)} {getattr(add_resp,'text','')}"
                            )
                if (self.entity.raw_data or {}).get("is_archived"):
                    arch_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/channel/archive",
                        {"channel_id": self.entity.mattermost_id},
                    )
                    if getattr(arch_resp, "status_code", None) not in (200, 201):
                        backend_logger.error(
                            f"Не удалось архивировать канал: {getattr(arch_resp,'status_code',None)} {getattr(arch_resp,'text','')}"
                        )
                await self.set_status("success")
                backend_logger.debug(
                    f"Канал {channel_name} экспортирован в Mattermost, ID: {self.entity.mattermost_id}"
                )
                return

            # Error handling
            try:
                data = response.json()
            except Exception:
                data = {"raw": getattr(response, "text", "")[:300]}
            err_text = (
                data.get("error") or data.get("message") or data.get("id") or str(data)
            )
            duplicate_hint = False
            if err_text and "already exists" in err_text.lower():
                duplicate_hint = True
            if getattr(response, "status_code", None) == 409:
                duplicate_hint = True
            if duplicate_hint:
                backend_logger.info(
                    f"Канал '{channel_name}' уже существует, попытаемся получить его id и продолжить"
                )
                existing_id = await self._lookup_existing_channel_id(channel_name)
                if existing_id:
                    self.entity.mattermost_id = existing_id
                    await self.set_status("success")
                    backend_logger.info(
                        f"Переиспользован существующий канал '{channel_name}' -> {existing_id}"
                    )
                    return
                backend_logger.warning(
                    f"Не удалось найти existing channel '{channel_name}' после дубликат-ошибки"
                )
            if (
                err_text
                and "participant" in err_text.lower()
                and "count" in err_text.lower()
            ):
                await self.set_status("skipped", error=err_text)
                backend_logger.warning(
                    f"Пропускаем канал '{channel_name}' из-за ошибки количества участников: {err_text}"
                )
                return
            backend_logger.error(
                f"Ошибка создания канала через плагин: {getattr(response,'status_code',None)}, {data}"
            )
            await self.set_status("failed", error=err_text)
        except Exception as e:
            backend_logger.error(f"Ошибка при создании канала: {e}")
            await self.set_status("failed", error=str(e))

    async def _resolve_mm_user_ids(self, slack_user_ids):
        """
        Получить Mattermost ID для списка Slack user ids из таблицы Entity.

        Обрабатываются три случая для каждого пользователя:
        1. Если сущность пользователя существует и уже содержит Mattermost ID — возвращается существующий ID.
        2. Если сущность пользователя существует, но Mattermost ID отсутствует — пользователь экспортируется в Mattermost, возвращается новый ID.
        3. Если пользователь отсутствует в таблице entities — создается placeholder-запись с минимальными данными (username=slack_id, email=slack_id@placeholder.local), пользователь экспортируется в Mattermost, возвращается новый ID.
        """
        from app.models.base import SessionLocal
        from sqlalchemy import select
        from app.models.entity import Entity
        from app.services.entities.user import User
        from app.services.export.user_exporter import UserExporter

        mm_ids = []
        async with SessionLocal() as session:
            for sid in slack_user_ids:
                q = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "user") & (Entity.slack_id == sid)
                    )
                )
                ent = q.scalar_one_or_none()
                if ent is not None:
                    mm_id = getattr(ent, "mattermost_id", None)
                    if mm_id:
                        mm_ids.append(mm_id)
                    else:
                        # Сущность есть, но MM ID отсутствует - экспортируем
                        backend_logger.info(
                            f"Entity exists for {sid} but MM ID missing, attempting export"
                        )
                        exporter = UserExporter(ent)
                        try:
                            await exporter.export_entity()
                            # Перезагружаем entity для получения обновленного mattermost_id
                            ent = await session.get(Entity, ent.id)
                            mm_id = getattr(ent, "mattermost_id", None)
                            if mm_id:
                                mm_ids.append(mm_id)
                            else:
                                backend_logger.warning(
                                    f"Export completed but MM ID still missing for {sid}"
                                )
                        except Exception as e:
                            backend_logger.error(f"Error exporting user {sid}: {e}")
                else:
                    backend_logger.warning(
                        f"User entity not found for Slack user {sid}, creating placeholder"
                    )
                    # Создаем placeholder user entity
                    placeholder_raw_data = {
                        "id": sid,
                        "name": sid,
                        "profile": {
                            "email": f"{sid}@placeholder.local",
                            "first_name": "Placeholder",
                            "last_name": "User",
                        },
                        "is_placeholder": True,
                    }
                    placeholder_user = User(
                        slack_id=sid,
                        mattermost_id=None,
                        raw_data=placeholder_raw_data,
                        status="pending",
                        auto_save=False,
                        job_id=getattr(self.entity, "job_id", None),
                    )
                    try:
                        # Сохраняем placeholder в БД
                        saved_entity = await placeholder_user.save_to_db()
                        if saved_entity:
                            backend_logger.info(
                                f"Created placeholder user entity for Slack user {sid}"
                            )
                            # Немедленно экспортируем placeholder пользователя в MM
                            exporter = UserExporter(saved_entity)
                            await exporter.export_entity()
                            # Перезагружаем entity для получения mattermost_id
                            reloaded_entity = await session.get(Entity, saved_entity.id)
                            mm_id = getattr(reloaded_entity, "mattermost_id", None)
                            if mm_id:
                                mm_ids.append(mm_id)
                                backend_logger.info(
                                    f"Successfully exported placeholder user {sid} to MM with ID {mm_id}"
                                )
                                await self._record_placeholder_user_creation()
                            else:
                                backend_logger.error(
                                    f"Placeholder export completed but MM ID missing for {sid}"
                                )
                        else:
                            backend_logger.error(
                                f"Failed to create placeholder user entity for {sid}"
                            )
                    except Exception as e:
                        backend_logger.error(
                            f"Error creating/exporting placeholder user for {sid}: {e}"
                        )
        return mm_ids

    async def _record_placeholder_user_creation(self) -> None:
        job_id = getattr(self.entity, "job_id", None)
        if not job_id:
            return
        try:
            await merge_job_meta(
                job_id,
                incr={
                    "users_discovered": 1,
                    "users_created": 1,
                    "users_processed": 1,
                },
            )
        except Exception as exc:  # pragma: no cover
            backend_logger.warning(
                "Failed to bump placeholder user counters for job %s: %s",
                job_id,
                exc,
            )

    async def _get_mm_team_id(self):
        """Определить ID команды Mattermost:
        - если задан MM_TEAM_ID — используем его
        - иначе получаем по имени MM_TEAM через API /api/v4/teams/name/{name}
        Кэшируем результат на время жизни экспортера.
        """
        if self._cached_team_id:
            return self._cached_team_id
        env_team_id = os.environ.get("MM_TEAM_ID")
        if env_team_id:
            self._cached_team_id = env_team_id
            return env_team_id
        team_name = os.environ.get("MM_TEAM", "test")
        try:
            resp = await self.mm_api_get(f"/api/v4/teams/name/{team_name}")
            if getattr(resp, "status_code", None) == 200:
                data = resp.json()  # type: ignore[attr-defined]
                tid = data.get("id")
                if tid:
                    self._cached_team_id = tid
                    return tid
            backend_logger.error(
                f"Не удалось получить team id по имени '{team_name}': {getattr(resp,'status_code',None)} {getattr(resp,'text','')}"
            )
        except Exception as e:
            backend_logger.error(f"Ошибка при получении team id: {e}")
        # Fallback на ранее используемый тестовый ID
        return "b7u9rycm43nip86mdiuqsxdcbe"

    async def _ensure_team_membership(self, mm_user_ids):
        """Ensure all provided Mattermost users are members of the team before adding to channels."""
        team_id = await self._get_mm_team_id()
        for uid in mm_user_ids:
            try:
                resp = await self.mm_api_post(
                    f"/api/v4/teams/{team_id}/members",
                    {"team_id": team_id, "user_id": uid},
                )
                if getattr(resp, "status_code", None) not in (200, 201):
                    # Server may respond with an error if already a member; log for trace and continue
                    try:
                        data = resp.json()  # type: ignore[attr-defined]
                        backend_logger.debug(
                            f"ensure team member resp for user {uid}: {getattr(resp,'status_code',None)} {data}"
                        )
                    except Exception:
                        backend_logger.debug(
                            f"ensure team member resp for user {uid}: {getattr(resp,'status_code',None)} {getattr(resp,'text','')}"
                        )
            except Exception as e:
                backend_logger.error(
                    f"Ошибка добавления пользователя {uid} в команду {team_id}: {e}"
                )

    def _normalize_channel_name(self, name: str) -> str:
        """Нормализовать название канала по тем же правилам, что и плагин.
        
        Применяет:
        1. Транслитерацию кириллицы (маркетинг → marketing)
        2. Unicode NFD нормализацию (café → cafe)
        3. Приведение к нижнему регистру
        4. Замену разделителей на дефисы
        5. Схлопывание множественных дефисов
        6. Обрезку до 64 символов
        
        Это должно совпадать с логикой normalizeChannelName в infra/plugin/server/api.go
        """
        if not name:
            return ""
        
        # Шаг 1: Транслитерация кириллицы
        cyrillic_map = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
            'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
            'А': 'a', 'Б': 'b', 'В': 'v', 'Г': 'g', 'Д': 'd', 'Е': 'e', 'Ё': 'yo',
            'Ж': 'zh', 'З': 'z', 'И': 'i', 'Й': 'y', 'К': 'k', 'Л': 'l', 'М': 'm',
            'Н': 'n', 'О': 'o', 'П': 'p', 'Р': 'r', 'С': 's', 'Т': 't', 'У': 'u',
            'Ф': 'f', 'Х': 'h', 'Ц': 'ts', 'Ч': 'ch', 'Ш': 'sh', 'Щ': 'sch', 'Ъ': '',
            'Ы': 'y', 'Ь': '', 'Э': 'e', 'Ю': 'yu', 'Я': 'ya',
        }
        
        cyrillic_translated = ''.join(cyrillic_map.get(c, c) for c in name)
        
        # Шаг 2: Unicode NFD нормализация (удаление диакритики)
        import unicodedata
        nfd = unicodedata.normalize('NFD', cyrillic_translated)
        # Удаляем combining characters (диакритика)
        ascii_base = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        # Обратно в NFC форму
        normalized = unicodedata.normalize('NFC', ascii_base)
        
        # Шаг 3: Фильтрация символов (оставляем только a-z, 0-9, дефисы)
        out = ""
        for c in normalized:
            if 'a' <= c <= 'z':
                out += c
            elif '0' <= c <= '9':
                out += c
            elif 'A' <= c <= 'Z':
                out += c.lower()
            elif c in ('-', '_', ' ', '.'):
                out += '-'
            # остальные символы пропускаются
        
        # Шаг 4: Схлопываем множественные дефисы
        import re
        cleaned = re.sub(r'-+', '-', out)
        
        # Шаг 5: Убираем дефисы в начале и конце
        cleaned = cleaned.strip('-')
        
        # Шаг 6: Обеспечиваем минимальную длину (2 символа)
        # (в плагине используется model.NewId(), здесь можем просто вернуть как есть,
        # так как этот метод используется только для lookup, не для создания)
        if len(cleaned) == 0:
            return ""
        
        # Шаг 7: Обрезаем до 64 символов
        if len(cleaned) > 64:
            cleaned = cleaned[:64]
        
        return cleaned

    def _extract_search_terms(self, name: str) -> list[str]:
        """Извлечь поисковые токены из названия канала (длина >= 3 символов)."""
        if not name:
            return []
        # Разделяем по дефисам, подчеркиваниям и пробелам
        import re

        tokens = re.split(r"[-_\s]+", name.lower())
        # Фильтруем токены длиной >= 3 символов
        return [t for t in tokens if len(t) >= 3]

    async def _search_existing_channel_id(
        self,
        slack_name: str,
        slack_display_name: str | None = None,
        is_private: bool = False,
    ) -> tuple[str | None, str]:
        """Многошаговый поиск существующего канала с fallback логикой.

        Возвращает: (channel_id, lookup_path)
        - channel_id: ID найденного канала или None
        - lookup_path: строка описывающая, как был найден канал (для логирования)
        """
        team_id = await self._get_mm_team_id()

        # Шаг 1: Попытка прямого lookup по имени (текущее поведение)
        backend_logger.debug(f"Шаг 1: Прямой lookup канала '{slack_name}' по имени")
        try:
            resp = await self.mm_api_get(
                f"/api/v4/teams/{team_id}/channels/name/{slack_name}"
            )
            if hasattr(resp, "status_code") and getattr(resp, "status_code") == 200:
                try:
                    data = resp.json()  # type: ignore[attr-defined]
                    cid = data.get("id")
                    if cid:
                        backend_logger.info(
                            f"Канал '{slack_name}' найден прямым lookup: {cid}"
                        )
                        return (cid, "name-lookup")
                except Exception:
                    backend_logger.error(
                        f"Не-JSON ответ при lookup канала '{slack_name}': {getattr(resp,'text','')[:200]}"
                    )
        except Exception as e:
            backend_logger.debug(f"Ошибка прямого lookup канала '{slack_name}': {e}")

        # Шаг 2: Fallback search через /api/v4/teams/{team}/channels/search
        backend_logger.debug(
            f"Шаг 2: Fallback search для канала '{slack_name}' через API поиска"
        )

        # Формируем список поисковых терминов
        search_terms = set()
        search_terms.add(slack_name)  # исходное имя
        search_terms.add(slack_name.lower())  # lowercase вариант

        # Нормализованные варианты
        normalized = self._normalize_channel_name(slack_name)
        if normalized:
            search_terms.add(normalized)
            # Варианты с заменой _ ↔ -
            search_terms.add(normalized.replace("-", "_"))
            search_terms.add(normalized.replace("_", "-"))

        # Добавляем display_name если есть
        if slack_display_name:
            search_terms.add(slack_display_name)
            search_terms.add(slack_display_name.lower())

        # Извлекаем токены для поиска (длина >= 3)
        tokens = self._extract_search_terms(slack_name)
        search_terms.update(tokens)

        backend_logger.debug(
            f"Поисковые термины для '{slack_name}': {list(search_terms)[:10]}"
        )

        # Собираем кандидатов из поиска
        candidates = {}  # {channel_id: channel_data}
        for term in search_terms:
            if not term or len(term) < 2:  # Skip too short terms
                continue
            try:
                search_resp = await self.mm_api_post(
                    f"/api/v4/teams/{team_id}/channels/search",
                    {"term": term},
                )
                if (
                    hasattr(search_resp, "status_code")
                    and getattr(search_resp, "status_code") == 200
                ):
                    try:
                        search_data = search_resp.json()  # type: ignore[attr-defined]
                        for chan in search_data:
                            if isinstance(chan, dict) and "id" in chan:
                                candidates[chan["id"]] = chan
                    except Exception:
                        backend_logger.debug(
                            f"Не-JSON ответ при поиске по термину '{term}'"
                        )
            except Exception as e:
                backend_logger.debug(f"Ошибка поиска по термину '{term}': {e}")

        if not candidates:
            backend_logger.warning(
                f"Не найдено кандидатов для канала '{slack_name}' через search fallback"
            )
            return (
                None,
                f"already exists in MM, but lookup failed; candidates=0",
            )

        backend_logger.debug(f"Найдено {len(candidates)} кандидатов для '{slack_name}'")

        # Шаг 3: Фильтруем кандидатов
        # 3.1 Точное совпадение по name
        normalized_slack_name = self._normalize_channel_name(slack_name)
        for cid, chan in candidates.items():
            chan_name = chan.get("name", "")
            if normalized_slack_name and chan_name:
                normalized_chan_name = self._normalize_channel_name(chan_name)
                if normalized_chan_name == normalized_slack_name:
                    # Проверяем тип канала если можем определить
                    chan_type = chan.get("type", "")
                    if is_private and chan_type != "P":
                        backend_logger.debug(
                            f"Кандидат {cid} отклонён: несовпадение типа (private vs {chan_type})"
                        )
                        continue
                    backend_logger.info(
                        f"Канал '{slack_name}' найден через search с точным совпадением name: {cid}"
                    )
                    return (cid, "search-fallback-exact-name")

        # 3.2 Точное совпадение по display_name
        if slack_display_name:
            normalized_slack_display = self._normalize_channel_name(slack_display_name)
            for cid, chan in candidates.items():
                chan_display = chan.get("display_name", "")
                if normalized_slack_display and chan_display:
                    normalized_chan_display = self._normalize_channel_name(chan_display)
                    if normalized_chan_display == normalized_slack_display:
                        chan_type = chan.get("type", "")
                        if is_private and chan_type != "P":
                            backend_logger.debug(
                                f"Кандидат {cid} отклонён: несовпадение типа по display_name"
                            )
                            continue
                        backend_logger.info(
                            f"Канал '{slack_name}' найден через search с точным совпадением display_name: {cid}"
                        )
                        return (cid, "search-fallback-exact-display-name")

        # 3.3 Единственный кандидат с совпадением всех токенов
        if len(candidates) == 1:
            cid, chan = list(candidates.items())[0]
            chan_display = (chan.get("display_name") or "").lower()
            # Проверяем что все ключевые токены из slack_name присутствуют в display_name
            if tokens and all(t in chan_display for t in tokens):
                chan_type = chan.get("type", "")
                if is_private and chan_type != "P":
                    backend_logger.debug(
                        f"Единственный кандидат {cid} отклонён: несовпадение типа"
                    )
                else:
                    backend_logger.info(
                        f"Канал '{slack_name}' найден как единственный кандидат с совпадением токенов: {cid}"
                    )
                    return (cid, "search-fallback-single-candidate")

        # Шаг 4: Не удалось однозначно определить канал
        candidate_info = []
        for cid, chan in list(candidates.items())[:5]:  # ограничим до 5 для лога
            candidate_info.append(
                f"{cid}({chan.get('name','?')}/{chan.get('display_name','?')})"
            )

        err_msg = (
            f"already exists in MM, but lookup failed; "
            f"candidates={len(candidates)}, "
            f"top5={','.join(candidate_info)}"
        )
        backend_logger.warning(
            f"Не удалось однозначно определить канал '{slack_name}': {err_msg}"
        )
        return (None, err_msg)

    async def _lookup_existing_channel_id(self, name: str) -> str | None:
        """Попытаться найти существующий канал по имени.
        
        Применяет ту же нормализацию, что и плагин, для поиска.
        Использует fallback к вызову плагина для idempotent get.
        
        Возвращает:
        - channel_id если канал найден
        - None если не найден
        """
        team_id = await self._get_mm_team_id()
        
        # Нормализуем имя по тем же правилам, что и плагин
        normalized_name = self._normalize_channel_name(name)
        
        if normalized_name != name:
            backend_logger.debug(
                f"Нормализовано имя канала для lookup: '{name}' → '{normalized_name}'"
            )
        
        # Шаг 1: Прямой lookup по нормализованному имени
        try:
            resp = await self.mm_api_get(
                f"/api/v4/teams/{team_id}/channels/name/{normalized_name}"
            )
            if hasattr(resp, "status_code") and getattr(resp, "status_code") == 200:
                try:
                    data = resp.json()  # type: ignore[attr-defined]
                    cid = data.get("id")
                    if cid:
                        backend_logger.info(
                            f"Канал '{name}' (normalized: '{normalized_name}') найден прямым lookup: {cid}"
                        )
                        return cid
                except Exception:
                    backend_logger.error(
                        f"Не-JSON ответ при lookup канала '{normalized_name}': {getattr(resp,'text','')[:200]}"
                    )
        except Exception as e:
            backend_logger.debug(f"Ошибка прямого lookup канала '{normalized_name}': {e}")
        
        # Шаг 2: Fallback - вызываем плагин с идемпотентным CreateOrGetChannel
        # Плагин найдет канал даже если он архивирован (includeDeleted=true)
        backend_logger.debug(
            f"Прямой lookup не нашёл канал '{normalized_name}', пробуем через плагин"
        )
        
        # Определяем тип канала (приватный или публичный)
        is_private = self._is_private_channel(self.entity.raw_data)
        channel_type = "P" if is_private else "O"
        
        # Используем display_name из raw_data или fallback на name
        display_name = self._sanitize_display_name(
            self._get_channel_display_name(self.entity.raw_data),
            name.replace("-", " "),
        )
        
        try:
            # Вызываем плагин для idempotent get/create
            plugin_resp = await self.mm_api_post(
                "/plugins/mm-importer/api/v1/channel",
                {
                    "team_id": team_id,
                    "name": name,  # Передаём оригинальное имя, плагин сам нормализует
                    "display_name": display_name,
                    "type": channel_type,
                }
            )
            
            if getattr(plugin_resp, "status_code", None) in (200, 201):
                try:
                    plugin_data = plugin_resp.json()  # type: ignore[attr-defined]
                    cid = plugin_data.get("channel_id")
                    if cid:
                        backend_logger.info(
                            f"Канал '{name}' найден через плагин (возможно архивный): {cid}"
                        )
                        return cid
                except Exception:
                    backend_logger.error(
                        f"Плагин вернул не-JSON при lookup '{name}': {getattr(plugin_resp,'text','')[:200]}"
                    )
        except Exception as e:
            backend_logger.debug(f"Ошибка вызова плагина для канала '{name}': {e}")
        
        # Шаг 3: Расширенный search fallback (если плагин тоже не помог)
        backend_logger.debug(
            f"Плагин не нашёл канал '{name}', используем расширенный search"
        )
        
        channel_id, lookup_path = await self._search_existing_channel_id(
            name, 
            self._get_channel_display_name(self.entity.raw_data),
            is_private
        )
        
        if channel_id:
            backend_logger.info(
                f"Канал '{name}' найден через расширенный search ({lookup_path}): {channel_id}"
            )
            return channel_id
        
        backend_logger.warning(
            f"Не удалось найти канал '{name}' ни одним из методов (direct lookup, plugin, search)"
        )
        return None
