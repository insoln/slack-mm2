package main

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestNormalizeChannelName_Cyrillic(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "cyrillic marketing-smm",
			input:    "маркетинг-smm",
			expected: "marketing-smm",
		},
		{
			name:     "cyrillic onboarding_crm",
			input:    "онбординг_crm",
			expected: "onbording-crm",
		},
		{
			name:     "cyrillic crm-alerts",
			input:    "crm-алерты",
			expected: "crm-alerty",
		},
		{
			name:     "cyrillic hiring_crm",
			input:    "найм_crm",
			expected: "naym-crm",
		},
		{
			name:     "cyrillic cases_careerum",
			input:    "кейсы_careerum",
			expected: "keysy-careerum",
		},
		{
			name:     "cyrillic course-seo",
			input:    "курс-сео-карьеры-июл2025",
			expected: "kurs-seo-karery-iyul2025",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := normalizeChannelName(tt.input)
			assert.Equal(t, tt.expected, result, "Failed to normalize: %s", tt.input)
		})
	}
}

func TestNormalizeChannelName_Unicode(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "french with accents",
			input:    "café-résumé",
			expected: "cafe-resume",
		},
		{
			name:     "german with umlauts",
			input:    "über-schön",
			expected: "uber-schon",
		},
		{
			name:     "spanish with tilde",
			input:    "niño-mañana",
			expected: "nino-manana",
		},
		{
			name:     "portuguese",
			input:    "ação-solução",
			expected: "acao-solucao",
		},
		{
			name:     "scandinavian",
			input:    "Ørsted-Ålesund",
			expected: "rsted-alesund",
		},
		{
			name:     "mixed cyrillic and latin",
			input:    "Test-тест-123",
			expected: "test-test-123",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := normalizeChannelName(tt.input)
			assert.Equal(t, tt.expected, result, "Failed to normalize: %s", tt.input)
		})
	}
}

func TestNormalizeChannelName_EdgeCases(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		minLen   int
		maxLen   int
	}{
		{
			name:   "empty string gets random suffix",
			input:  "",
			minLen: 2,
			maxLen: 64,
		},
		{
			name:   "only non-latin characters gets random suffix",
			input:  "日本語",
			minLen: 2,
			maxLen: 64,
		},
		{
			name:   "single character gets extended",
			input:  "a",
			minLen: 2,
			maxLen: 64,
		},
		{
			name:   "very long name gets truncated",
			input:  "very-long-channel-name-that-exceeds-the-maximum-allowed-length-of-64-characters-for-mattermost-channels",
			minLen: 2,
			maxLen: 64,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := normalizeChannelName(tt.input)
			assert.GreaterOrEqual(t, len(result), tt.minLen, "Result too short: %s", result)
			assert.LessOrEqual(t, len(result), tt.maxLen, "Result too long: %s", result)
		})
	}
}

func TestNormalizeChannelName_NoCollisions(t *testing.T) {
	// Test that previously colliding names now produce different normalized names
	tests := []struct {
		name    string
		input1  string
		input2  string
		wantDifferent bool
	}{
		{
			name:          "onboarding_crm vs crm-alerts should differ",
			input1:        "онбординг_crm",
			input2:        "crm-алерты",
			wantDifferent: true,
		},
		{
			name:          "marketing-smm vs smm should differ",
			input1:        "маркетинг-smm",
			input2:        "smm",
			wantDifferent: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result1 := normalizeChannelName(tt.input1)
			result2 := normalizeChannelName(tt.input2)
			
			if tt.wantDifferent {
				assert.NotEqual(t, result1, result2, 
					"Expected different normalized names for %s and %s, but both became: %s",
					tt.input1, tt.input2, result1)
			} else {
				assert.Equal(t, result1, result2,
					"Expected same normalized names for %s and %s",
					tt.input1, tt.input2)
			}
		})
	}
}

func TestNormalizeChannelName_SpecialCharacters(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "underscores to dashes",
			input:    "test_channel_name",
			expected: "test-channel-name",
		},
		{
			name:     "spaces to dashes",
			input:    "test channel name",
			expected: "test-channel-name",
		},
		{
			name:     "dots to dashes",
			input:    "test.channel.name",
			expected: "test-channel-name",
		},
		{
			name:     "mixed separators normalized",
			input:    "test_channel.name space",
			expected: "test-channel-name-space",
		},
		{
			name:     "multiple dashes collapsed",
			input:    "test---channel",
			expected: "test-channel",
		},
		{
			name:     "leading and trailing dashes removed",
			input:    "-test-channel-",
			expected: "test-channel",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := normalizeChannelName(tt.input)
			assert.Equal(t, tt.expected, result, "Failed to normalize: %s", tt.input)
		})
	}
}
