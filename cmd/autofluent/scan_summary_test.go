package main

import (
	"strings"
	"testing"
)

func TestSummaryClearlyLabelsDraftAndBlockedPreview(t *testing.T) {
	snap := map[string]any{"scan_readiness": "incomplete", "fields": []any{map[string]any{"state": "read"}, map[string]any{"state": "unavailable"}},
		"boundaries": []any{map[string]any{}}, "fluid_zones": []any{},
		"draft_template": map[string]any{"verified_with_real_case": false},
		"auto_preview":   map[string]any{"ok": false, "blocker_count": 2, "change_count": 1}}
	s := summarizeScan(snap)
	for _, text := range []string{"incomplete", "1/2", "未审核", "阻断 2 项", "暂不允许发布"} {
		if !strings.Contains(s, text) {
			t.Fatalf("missing %q in %q", text, s)
		}
	}
}
