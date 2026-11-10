package main

import (
	"errors"
	"strings"
	"testing"
)

func TestWindowsEditCRLFNormalization(t *testing.T) {
	got := normalizeWindowsEditText("a\nb\r\nc\rd")
	if got != "a\r\nb\r\nc\r\nd" {
		t.Fatalf("%q", got)
	}
	if strings.Contains(got, "\r\r") {
		t.Fatal("duplicate CR breaks")
	}
}
func TestLogUIBoundedAndReadable(t *testing.T) {
	in := "\n重复\n重复\r\n" + strings.Repeat("abc", 900)
	got := compactUILogMessage(in, 140)
	if !strings.Contains(got, "详细信息请打开") || strings.Count(got, "重复") != 1 || len([]rune(got)) > 180 {
		t.Fatalf("%q", got)
	}
}
func TestWorkbenchFailureIsNotMisreportedAsInitialHandshakeFailure(t *testing.T) {
	wb := WorkbenchAttach{Status: "workbench_recheck_required", Port: 9935, Detail: "Fluent 系统检查失败"}
	txt := wbResultUIMessage(wb, errors.New("Fluent report failed"))
	if !strings.Contains(txt, "上次握手成功") || !strings.Contains(txt, "Fluent") {
		t.Fatal(txt)
	}
}
func TestWorkbenchZeroFluentShowsOriginalSystems(t *testing.T) {
	wb := WorkbenchAttach{Status: "no_fluent_system", Port: 9935, AllSystems: []WorkbenchSystem{{Name: "A", Label: "Fluid Flow"}}}
	txt := wbResultUIMessage(wb, nil)
	if !strings.Contains(txt, "A: Fluid Flow") {
		t.Fatal(txt)
	}
}
