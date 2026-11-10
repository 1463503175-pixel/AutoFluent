package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestExportReportCopyPreservesExistingFile(t *testing.T) {
	root, target := t.TempDir(), t.TempDir()
	native := filepath.Join(root, "native_reports")
	if err := os.Mkdir(native, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(native, "analysis.html")
	if err := os.WriteFile(source, []byte("report"), 0600); err != nil {
		t.Fatal(err)
	}
	saved, err := exportReportCopy(root, source, target)
	if err != nil || saved != filepath.Join(target, "analysis.html") {
		t.Fatalf("saved %q: %v", saved, err)
	}
	if _, err := exportReportCopy(root, source, target); err == nil {
		t.Fatal("existing report was not protected")
	}
	data, err := os.ReadFile(saved)
	if err != nil || string(data) != "report" {
		t.Fatalf("bad output: %q %v", data, err)
	}
}

func TestApprovedReportPathAllowsNativeAndHistoric(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"native_reports", "libraries", "saved_reports"} {
		dir := filepath.Join(root, name)
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
		html := filepath.Join(dir, "analysis.html")
		if err := os.WriteFile(html, []byte("ok"), 0600); err != nil {
			t.Fatal(err)
		}
		if !isApprovedReportPath(root, html) {
			t.Errorf("legitimate %s report refused", name)
		}
	}
}
func TestApprovedReportPathRejectsTraversalSiblingAndSymlink(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	dir := filepath.Join(root, "native_reports")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	external := filepath.Join(outside, "secret.html")
	if err := os.WriteFile(external, []byte("not ours"), 0600); err != nil {
		t.Fatal(err)
	}
	sibling := filepath.Join(root, "native_reports_bad")
	if err := os.Mkdir(sibling, 0700); err != nil {
		t.Fatal(err)
	}
	sb := filepath.Join(sibling, "forged.html")
	if err := os.WriteFile(sb, []byte("x"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{external, filepath.Join(dir, "..", "native_reports_bad", "forged.html"), sb, "", dir} {
		if isApprovedReportPath(root, path) {
			t.Errorf("untrusted path accepted: %s", path)
		}
	}
	link := filepath.Join(dir, "external.html")
	if err := os.Symlink(external, link); err != nil {
		if runtime.GOOS != "windows" {
			t.Fatal(err)
		}
	} else if isApprovedReportPath(root, link) {
		t.Fatal("symlink leaving reports tree accepted")
	}
}
