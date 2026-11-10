package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPreviewIdentityChangesWithServerAndTemplate(t *testing.T) {
	dir := t.TempDir()
	server := filepath.Join(dir, "server.txt")
	if err := os.WriteFile(server, []byte("127.0.0.1:12345\nsecret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	a := &App{base: dir, dataDir: dir, python: "python", serverInfo: server, template: map[string]any{"schema": "autofluent.conditions.v1", "fields": []any{}, "zones": []any{}}}
	first := a.previewIdentity()
	if len(first) != 64 {
		t.Fatalf("unexpected signature %q", first)
	}
	a.template["new"] = "value"
	second := a.previewIdentity()
	if first == second {
		t.Fatal("changing template must invalidate preview")
	}
	if err := os.WriteFile(server, []byte("127.0.0.1:12345\nnewsecret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if a.previewIdentity() == second {
		t.Fatal("changing server-info must invalidate preview")
	}
}
func TestArchiveScanStoresReadOnlySnapshot(t *testing.T) {
	dir := t.TempDir()
	a := &App{base: dir, dataDir: dir}
	if _, err := a.archiveScan(map[string]any{"schema": "wrong"}); err == nil {
		t.Fatal("invalid schema accepted")
	}
	path, err := a.archiveScan(map[string]any{"schema": "autofluent.snapshot.v1", "read_at": "2026-11-09"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(filepath.ToSlash(path), "snapshots/scan_") {
		t.Fatal(path)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
}
