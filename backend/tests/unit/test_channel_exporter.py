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


def test_normalize_channel_name():
    """Test channel name normalization logic."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={})
    exporter = ChannelExporter(ent)

    # Basic normalization
    assert exporter._normalize_channel_name("General") == "general"
    assert exporter._normalize_channel_name("Marketing Team") == "marketing-team"
    assert exporter._normalize_channel_name("Marketing_Team") == "marketing-team"
    assert exporter._normalize_channel_name("marketing__team") == "marketing-team"
    assert exporter._normalize_channel_name("marketing--team") == "marketing-team"
    assert exporter._normalize_channel_name("  Marketing  ") == "marketing"
    assert exporter._normalize_channel_name("") == ""


def test_extract_search_terms():
    """Test search term extraction from channel names."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={})
    exporter = ChannelExporter(ent)

    # Extract tokens >= 3 chars
    assert set(exporter._extract_search_terms("marketing-smm-crm")) == {
        "marketing",
        "smm",
        "crm",
    }
    assert set(exporter._extract_search_terms("test_channel_name")) == {
        "test",
        "channel",
        "name",
    }
    assert set(exporter._extract_search_terms("ab-cd-efg")) == {"efg"}  # only >= 3
    assert exporter._extract_search_terms("") == []


@pytest.mark.asyncio
async def test_search_existing_channel_direct_lookup_success():
    """Test direct lookup success path (step 1)."""
    ent = StubEntity(
        entity_type="channel", slack_id="C123", raw_data={"name": "general"}
    )
    exporter = ChannelExporter(ent)

    # Mock direct lookup success
    lookup_resp = SimpleNamespace(
        status_code=200,
        text='{"id":"chan123"}',
        json=lambda: {"id": "chan123"},
    )

    with patch.object(exporter, "mm_api_get", new=AsyncMock(return_value=lookup_resp)):
        with patch.object(
            exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
        ):
            channel_id, path = await exporter._search_existing_channel_id("general")

    assert channel_id == "chan123"
    assert path == "name-lookup"


@pytest.mark.asyncio
async def test_search_existing_channel_fallback_exact_name():
    """Test search fallback with exact name match (step 3.1)."""
    ent = StubEntity(
        entity_type="channel", slack_id="C123", raw_data={"name": "marketing_team"}
    )
    exporter = ChannelExporter(ent)

    # Direct lookup fails (404)
    lookup_resp = SimpleNamespace(status_code=404, text="Not found")

    # Search returns candidate with matching normalized name
    search_resp = SimpleNamespace(
        status_code=200,
        text="[]",
        json=lambda: [
            {
                "id": "chan456",
                "name": "marketing-team",  # Normalized match
                "display_name": "Marketing Team",
                "type": "O",
            }
        ],
    )

    async def mock_get(path):
        return lookup_resp

    async def mock_post(path, payload):
        return search_resp

    with patch.object(exporter, "mm_api_get", new=AsyncMock(side_effect=mock_get)):
        with patch.object(
            exporter, "mm_api_post", new=AsyncMock(side_effect=mock_post)
        ):
            with patch.object(
                exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
            ):
                channel_id, path = await exporter._search_existing_channel_id(
                    "marketing_team"
                )

    assert channel_id == "chan456"
    assert path == "search-fallback-exact-name"


@pytest.mark.asyncio
async def test_search_existing_channel_fallback_exact_display_name():
    """Test search fallback with exact display_name match (step 3.2)."""
    ent = StubEntity(
        entity_type="channel",
        slack_id="C123",
        raw_data={"name": "old-name", "display_name": "Marketing SMM"},
    )
    exporter = ChannelExporter(ent)

    # Direct lookup fails
    lookup_resp = SimpleNamespace(status_code=404, text="Not found")

    # Search returns candidate with matching display_name
    search_resp = SimpleNamespace(
        status_code=200,
        text="[]",
        json=lambda: [
            {
                "id": "chan789",
                "name": "some-internal-name",
                "display_name": "Marketing_SMM",  # Normalized match with display
                "type": "O",
            }
        ],
    )

    async def mock_get(path):
        return lookup_resp

    async def mock_post(path, payload):
        return search_resp

    with patch.object(exporter, "mm_api_get", new=AsyncMock(side_effect=mock_get)):
        with patch.object(
            exporter, "mm_api_post", new=AsyncMock(side_effect=mock_post)
        ):
            with patch.object(
                exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
            ):
                channel_id, path = await exporter._search_existing_channel_id(
                    "old-name", slack_display_name="Marketing SMM"
                )

    assert channel_id == "chan789"
    assert path == "search-fallback-exact-display-name"


@pytest.mark.asyncio
async def test_search_existing_channel_fallback_single_candidate_with_tokens():
    """Test search fallback with single candidate matching all tokens (step 3.3)."""
    ent = StubEntity(
        entity_type="channel", slack_id="C123", raw_data={"name": "marketing-smm"}
    )
    exporter = ChannelExporter(ent)

    # Direct lookup fails
    lookup_resp = SimpleNamespace(status_code=404, text="Not found")

    # Search returns single candidate with all tokens in display_name
    search_resp = SimpleNamespace(
        status_code=200,
        text="[]",
        json=lambda: [
            {
                "id": "chan999",
                "name": "different-internal-name",
                "display_name": "Our Marketing and SMM Channel",
                "type": "O",
            }
        ],
    )

    async def mock_get(path):
        return lookup_resp

    async def mock_post(path, payload):
        return search_resp

    with patch.object(exporter, "mm_api_get", new=AsyncMock(side_effect=mock_get)):
        with patch.object(
            exporter, "mm_api_post", new=AsyncMock(side_effect=mock_post)
        ):
            with patch.object(
                exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
            ):
                channel_id, path = await exporter._search_existing_channel_id(
                    "marketing-smm"
                )

    assert channel_id == "chan999"
    assert path == "search-fallback-single-candidate"


@pytest.mark.asyncio
async def test_search_existing_channel_no_candidates():
    """Test search fallback with no candidates found."""
    ent = StubEntity(
        entity_type="channel", slack_id="C123", raw_data={"name": "nonexistent"}
    )
    exporter = ChannelExporter(ent)

    # Direct lookup fails
    lookup_resp = SimpleNamespace(status_code=404, text="Not found")

    # Search returns empty list
    search_resp = SimpleNamespace(status_code=200, text="[]", json=lambda: [])

    async def mock_get(path):
        return lookup_resp

    async def mock_post(path, payload):
        return search_resp

    with patch.object(exporter, "mm_api_get", new=AsyncMock(side_effect=mock_get)):
        with patch.object(
            exporter, "mm_api_post", new=AsyncMock(side_effect=mock_post)
        ):
            with patch.object(
                exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
            ):
                channel_id, path = await exporter._search_existing_channel_id(
                    "nonexistent"
                )

    assert channel_id is None
    assert "candidates=0" in path


@pytest.mark.asyncio
async def test_search_existing_channel_multiple_ambiguous_candidates():
    """Test search fallback with multiple ambiguous candidates."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)

    # Direct lookup fails
    lookup_resp = SimpleNamespace(status_code=404, text="Not found")

    # Search returns multiple candidates without clear match
    search_resp = SimpleNamespace(
        status_code=200,
        text="[]",
        json=lambda: [
            {
                "id": "chan1",
                "name": "test-channel-one",
                "display_name": "Test Channel One",
                "type": "O",
            },
            {
                "id": "chan2",
                "name": "test-channel-two",
                "display_name": "Test Channel Two",
                "type": "O",
            },
        ],
    )

    async def mock_get(path):
        return lookup_resp

    async def mock_post(path, payload):
        return search_resp

    with patch.object(exporter, "mm_api_get", new=AsyncMock(side_effect=mock_get)):
        with patch.object(
            exporter, "mm_api_post", new=AsyncMock(side_effect=mock_post)
        ):
            with patch.object(
                exporter, "_get_mm_team_id", new=AsyncMock(return_value="team1")
            ):
                channel_id, path = await exporter._search_existing_channel_id("test")

    assert channel_id is None
    assert "candidates=2" in path


@pytest.mark.asyncio
async def test_dm_with_missing_users_creates_placeholders():
    """Test that DM with missing users creates placeholder entities and exports them."""
    ent = StubEntity(
        entity_type="channel",
        slack_id="D123",
        raw_data={"id": "D123", "members": ["U1", "U2"]},
    )
    exporter = ChannelExporter(ent)

    # Simulate DM creation success
    dm_resp = SimpleNamespace(
        status_code=201,
        text='{"channel_id":"mm_dm_id"}',
        json=lambda: {"channel_id": "mm_dm_id"},
    )

    # After placeholders are created and exported, _resolve_mm_user_ids should return 2 IDs
    async def mock_resolve_mm_user_ids(slack_ids):
        # Simulate that both users now have MM IDs after placeholder creation
        return [f"mm_{sid.lower()}" for sid in slack_ids]

    with patch.object(
        exporter,
        "_resolve_mm_user_ids",
        new=AsyncMock(side_effect=mock_resolve_mm_user_ids),
    ):
        with patch.object(exporter, "mm_api_post", new=AsyncMock(return_value=dm_resp)):

            async def fake_set_status(status, error=None):
                ent.status = SimpleNamespace(name=status)
                ent.error_message = error

            exporter.set_status = fake_set_status  # type: ignore
            await exporter.export_entity()

    # Should have successfully created the DM
    assert ent.mattermost_id == "mm_dm_id"
    assert ent.status.name == "success"


@pytest.mark.asyncio
async def test_resolve_mm_user_ids_creates_placeholder():
    """Test _resolve_mm_user_ids creates placeholder for missing users."""
    from unittest.mock import MagicMock

    ent = StubEntity(entity_type="channel", slack_id="D123", raw_data={"id": "D123"})
    exporter = ChannelExporter(ent)

    # Mock SessionLocal where it's actually imported from
    with patch("app.models.base.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session

        # Simulate: user not found in DB (always return None)

        async def mock_execute(query):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            return mock_result

        mock_session.execute.side_effect = mock_execute
        mock_session.refresh = AsyncMock()

        # Mock User.save_to_db
        async def mock_user_save(self):
            fake_entity = StubEntity(
                slack_id=self.slack_id,
                entity_type="user",
                mattermost_id=None,
                raw_data=self.raw_data,
            )
            return fake_entity

        # Mock UserExporter.export_entity
        async def mock_user_export(self):
            self.entity.mattermost_id = f"mm_{self.entity.slack_id.lower()}"

        with patch(
            "app.services.entities.user.User.save_to_db",
            new=mock_user_save,
        ):
            with patch(
                "app.services.export.user_exporter.UserExporter.export_entity",
                new=mock_user_export,
            ):
                # Test _resolve_mm_user_ids with a missing user
                result = await exporter._resolve_mm_user_ids(["U_MISSING"])

                # Should have created placeholder and returned MM ID
                assert len(result) == 1
                assert result[0] == "mm_u_missing"
