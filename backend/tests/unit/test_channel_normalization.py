"""Test channel name normalization to match plugin behavior."""
import pytest
from app.services.export.channel_exporter import ChannelExporter
from types import SimpleNamespace


class StubEntity(SimpleNamespace):
    """Lightweight stand-in for ORM Entity."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status = SimpleNamespace(name="pending")
        self.error_message = None
        self.mattermost_id = None


def test_normalize_channel_name_cyrillic():
    """Test Cyrillic transliteration matches Go plugin."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)
    
    test_cases = [
        ("маркетинг-smm", "marketing-smm"),
        ("онбординг_crm", "onbording-crm"),
        ("crm-алерты", "crm-alerty"),
        ("найм_crm", "naym-crm"),
        ("кейсы_careerum", "keysy-careerum"),
        ("курс-сео-карьеры-июл2025", "kurs-seo-karery-iyul2025"),
    ]
    
    for input_name, expected in test_cases:
        result = exporter._normalize_channel_name(input_name)
        assert result == expected, f"Failed: {input_name} → {result} (expected {expected})"


def test_normalize_channel_name_unicode():
    """Test Unicode diacritic removal matches Go plugin."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)
    
    test_cases = [
        ("café-résumé", "cafe-resume"),
        ("über-schön", "uber-schon"),
        ("niño-mañana", "nino-manana"),
        ("ação-solução", "acao-solucao"),
        ("Test-тест-123", "test-test-123"),
    ]
    
    for input_name, expected in test_cases:
        result = exporter._normalize_channel_name(input_name)
        assert result == expected, f"Failed: {input_name} → {result} (expected {expected})"


def test_normalize_channel_name_special_chars():
    """Test special character handling matches Go plugin."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)
    
    test_cases = [
        ("test_channel_name", "test-channel-name"),
        ("test channel name", "test-channel-name"),
        ("test.channel.name", "test-channel-name"),
        ("test_channel.name space", "test-channel-name-space"),
        ("test---channel", "test-channel"),
        ("-test-channel-", "test-channel"),
    ]
    
    for input_name, expected in test_cases:
        result = exporter._normalize_channel_name(input_name)
        assert result == expected, f"Failed: {input_name} → {result} (expected {expected})"


def test_normalize_channel_name_edge_cases():
    """Test edge cases match Go plugin behavior."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)
    
    # Empty string
    assert exporter._normalize_channel_name("") == ""
    
    # Only non-latin characters → empty (plugin would add random suffix, but we don't need that for lookup)
    result = exporter._normalize_channel_name("日本語")
    assert result == "", f"Expected empty for pure CJK, got: {result}"
    
    # Very long name gets truncated to 64 chars
    long_name = "a" * 100
    result = exporter._normalize_channel_name(long_name)
    assert len(result) <= 64, f"Result too long: {len(result)} chars"
    assert result == "a" * 64


def test_normalize_no_collision():
    """Test that previously colliding names now produce different results."""
    ent = StubEntity(entity_type="channel", slack_id="C123", raw_data={"name": "test"})
    exporter = ChannelExporter(ent)
    
    # These should now be different due to transliteration
    result1 = exporter._normalize_channel_name("онбординг_crm")
    result2 = exporter._normalize_channel_name("crm-алерты")
    
    assert result1 != result2, (
        f"Expected different names but both became: {result1} "
        f"(from 'онбординг_crm' and 'crm-алерты')"
    )
    
    assert result1 == "onbording-crm"
    assert result2 == "crm-alerty"
    
    # Another collision case
    result3 = exporter._normalize_channel_name("маркетинг-smm")
    result4 = exporter._normalize_channel_name("smm")
    
    assert result3 != result4, (
        f"Expected different names but both became: {result3} "
        f"(from 'маркетинг-smm' and 'smm')"
    )
    
    assert result3 == "marketing-smm"
    assert result4 == "smm"
