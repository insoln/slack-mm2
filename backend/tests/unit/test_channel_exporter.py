import pytest
from unittest.mock import AsyncMock, patch
from app.services.export.channel_exporter import ChannelExporter
from types import SimpleNamespace


class StubEntity(SimpleNamespace):
    """Lightweight stand-in for ORM Entity with only needed attributes.
    We avoid hitting the DB or SQLAlchemy instrumentation; ChannelExporter only
    touches these attributes directly.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # default fields
        self.status = SimpleNamespace(name="pending")
        self.error_message = None
        self.mattermost_id = None


@pytest.mark.asyncio
async def test_duplicate_reuse(monkeypatch):
    ent = StubEntity(
        entity_type="channel", slack_id="C123", raw_data={"name": "general"}
    )

    exporter = ChannelExporter(ent)

    # Simulate duplicate error response
    dup_resp = SimpleNamespace(
        status_code=409,
        text='{"message":"Channel already exists"}',
        json=lambda: {"message": "Channel already exists"},
    )
    ok_lookup = SimpleNamespace(
        status_code=200, text='{"id":"mm_chan_id"}', json=lambda: {"id": "mm_chan_id"}
    )

    with patch.object(exporter, "mm_api_post", new=AsyncMock(return_value=dup_resp)):
        with patch.object(
            exporter,
            "_lookup_existing_channel_id",
            new=AsyncMock(return_value="mm_chan_id"),
        ):

            async def fake_set_status(status, error=None):
                ent.status = SimpleNamespace(name=status)
                ent.error_message = error

            exporter.set_status = fake_set_status  # type: ignore
            await exporter.export_entity()
    assert ent.mattermost_id == "mm_chan_id"
    assert ent.status.name == "success"


@pytest.mark.asyncio
async def test_mpim_autopad_slackbot(monkeypatch):
    ent = StubEntity(
        entity_type="channel",
        slack_id="GDM1",
        raw_data={"name": "mpdm-u1--u2-1", "is_mpim": True, "members": ["U1", "U2"]},
    )
    exporter = ChannelExporter(ent)

    # After autopad, we simulate successful gdm creation
    gdm_resp = SimpleNamespace(
        status_code=201,
        text='{"channel_id":"mm_gdm"}',
        json=lambda: {"channel_id": "mm_gdm"},
    )

    async def resolve(ids):
        # Return fake MM ids for any provided Slack ids
        return [f"mm_{i.lower()}" for i in ids]

    with patch.object(exporter, "mm_api_post", new=AsyncMock(return_value=gdm_resp)):
        with patch.object(
            exporter, "_resolve_mm_user_ids", new=AsyncMock(side_effect=resolve)
        ):

            async def fake_set_status(status, error=None):
                ent.status = SimpleNamespace(name=status)
                ent.error_message = error

            exporter.set_status = fake_set_status  # type: ignore
            await exporter.export_entity()
    assert ent.mattermost_id == "mm_gdm"
    assert ent.status.name == "success"


@pytest.mark.asyncio
async def test_mpim_overflow_converted(monkeypatch):
    members = [f"U{i}" for i in range(1, 12)]  # 11 members
    ent = StubEntity(
        entity_type="channel",
        slack_id="GDM2",
        raw_data={"name": "mpdm-big", "is_mpim": True, "members": members},
    )
    exporter = ChannelExporter(ent)

    # Expect normal channel creation path (private) after conversion
    chan_resp = SimpleNamespace(
        status_code=201,
        text='{"channel_id":"mm_priv"}',
        json=lambda: {"channel_id": "mm_priv"},
    )

    async def resolve(ids):
        return [f"mm_{i.lower()}" for i in ids]

    with patch.object(exporter, "mm_api_post", new=AsyncMock(return_value=chan_resp)):
        with patch.object(
            exporter, "_resolve_mm_user_ids", new=AsyncMock(side_effect=resolve)
        ):

            async def fake_set_status(status, error=None):
                ent.status = SimpleNamespace(name=status)
                ent.error_message = error

            exporter.set_status = fake_set_status  # type: ignore
            await exporter.export_entity()
    # Should mark success with converted channel id
    assert ent.mattermost_id == "mm_priv"
    assert ent.status.name == "success"


@pytest.mark.asyncio
async def test_mpim_insufficient_members_skipped(monkeypatch):
    ent = StubEntity(
        entity_type="channel",
        slack_id="GDM3",
        raw_data={"name": "mpdm-one", "is_mpim": True, "members": ["U1"]},
    )
    exporter = ChannelExporter(ent)

    async def resolve(ids):
        return [f"mm_{i.lower()}" for i in ids]

    with patch.object(
        exporter, "_resolve_mm_user_ids", new=AsyncMock(side_effect=resolve)
    ):
        # Force skip path (will not call API)
        async def fake_set_status(status, error=None):
            ent.status = SimpleNamespace(name=status)
            ent.error_message = error

        exporter.set_status = fake_set_status  # type: ignore
        await exporter.export_entity()
    assert ent.status.name == "skipped"
    assert "Insufficient" in (ent.error_message or "")
