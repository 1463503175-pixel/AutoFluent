package main

import (
	"fmt"
	"strings"
)

// Win32 multiline EDIT controls require CRLF; Python warnings and native
// solver logs can mix LF, CRLF, and CR-only text in one response.
func normalizeWindowsEditText(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\r", "\n")
	return strings.ReplaceAll(s, "\n", "\r\n")
}

// The on-screen log is a concise progress summary; verbose diagnostics are
// persisted separately and must not swamp the tiny log viewport.
func compactUILogMessage(s string, maxRunes int) string {
	if maxRunes < 80 {
		maxRunes = 80
	}
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\r", "\n")
	s = strings.ReplaceAll(s, "\t", "  ")
	lines := strings.Split(s, "\n")
	kept := make([]string, 0, 7)
	for _, raw := range lines {
		line := strings.TrimSpace(raw)
		if line == "" {
			continue
		}
		if len(kept) > 0 && kept[len(kept)-1] == line {
			continue
		}
		kept = append(kept, line)
		if len(kept) == 7 {
			break
		}
	}
	short := strings.Join(kept, "\n")
	runes := []rune(short)
	if len(runes) > maxRunes {
		return string(runes[:maxRunes]) + "\n详细信息请打开本机诊断文件"
	}
	if len(lines) > len(kept)+2 {
		short += "\n详细信息请打开本机诊断文件"
	}
	return short
}

func wbResultUIMessage(wb WorkbenchAttach, err error) string {
	msg := fmt.Sprintf("WB 端口 %d：%s", wb.Port, wb.Status)
	switch wb.Status {
	case "workbench_connected":
		msg = fmt.Sprintf("WB %d 握手成功；正在检测 Fluent 系统", wb.Port)
	case "fluent_connected":
		msg = fmt.Sprintf("WB %d 已连接；Fluent TUI 已完成只读验证", wb.Port)
	case "no_fluent_system":
		msg = fmt.Sprintf("WB %d 已连接；当前工程尚未识别 Fluent（原始系统 %d 个）", wb.Port, len(wb.AllSystems))
	case "fluent_probe_failed":
		msg = fmt.Sprintf("WB %d 已连接；Fluent TUI 探测未通过", wb.Port)
	case "workbench_recheck_required":
		msg = fmt.Sprintf("WB %d 上次握手成功；Fluent 检查异常，需重新验证是否仍可用", wb.Port)
	case "selection_required":
		msg = fmt.Sprintf("WB %d 已连接；有 %d 个 Fluent 候选，需要选择一个", wb.Port, len(wb.Systems))
	}
	if wb.ProjectFile != "" {
		msg += "\n工程：" + wb.ProjectFile
	}
	if wb.Status == "no_fluent_system" && len(wb.AllSystems) > 0 {
		for i, s := range wb.AllSystems {
			if i >= 5 {
				break
			}
			msg += fmt.Sprintf("\n  %s: %s", s.Name, s.Label)
		}
	}
	if err != nil {
		msg += "\n详情：" + err.Error()
	}
	if wb.Detail != "" {
		msg += "\n" + wb.Detail
	}
	return compactUILogMessage(msg, 900)
}
