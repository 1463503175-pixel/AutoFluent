package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func (a *App) wbConditions(ctx context.Context, wb WorkbenchAttach, mode, sourceOrPlan string) (map[string]any, error) {
	if strings.TrimSpace(wb.ProjectFile) == "" {
		return nil, fmt.Errorf("当前 WB 工程文件身份无法核验；请先保存工程并重新检查 Fluent")
	}
	if wb.Status != "fluent_connected" || !verifiedWBTransport(wb) {
		return nil, fmt.Errorf("尚未连接并验证当前 Fluent")
	}
	system, ok := wb.TUI["system"].(string)
	if !ok || system == "" {
		return nil, fmt.Errorf("未确定唯一 Fluent 系统")
	}
	valid := false
	for _, s := range wb.Systems {
		if s.Name == system && s.Fluent {
			valid = true
		}
	}
	if !valid {
		return nil, fmt.Errorf("Fluent 系统身份已失效")
	}
	py := splitCommand(wb.Python)
	if len(py) == 0 {
		return nil, fmt.Errorf("未取得经验证的 WB Python")
	}
	if mode != "preview" && mode != "publish" {
		return nil, fmt.Errorf("无效工况操作")
	}
	args := append(append([]string{}, py[1:]...), filepath.Join(a.base, "worker", "af_wb_conditions.py"),
		"--mode", mode, "--port", fmt.Sprint(wb.Port), "--security", wb.Security,
		"--system", system, "--app-dir", a.dataDir)
	if mode == "preview" {
		root := filepath.Join(a.dataDir, "settings_exports")
		actual, e := filepath.Abs(sourceOrPlan)
		if e != nil {
			return nil, e
		}
		if filepath.Clean(filepath.Dir(actual)) != filepath.Clean(root) || !strings.EqualFold(filepath.Ext(actual), ".set") {
			return nil, fmt.Errorf("基准 .set 必须从 AutoFluent 的 settings_exports 目录选择，先在原模型导出一次设置")
		}
		args = append(args, "--source", actual)
	} else {
		args = append(args, "--plan", sourceOrPlan, "--approved")
	}
	if wb.ProjectFile != "" {
		args = append(args, "--expected-project", wb.ProjectFile)
	}
	// The worker writes a durable JSON receipt/lease before any non-idempotent
	// /file/read-settings. Timeout is unknown remote state, never permission to retry.
	q, cancel := context.WithTimeout(ctx, 4*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(q, py[0], args...)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	configureChildProcess(cmd)
	raw, err := cmd.CombinedOutput()
	if q.Err() != nil {
		return map[string]any{"status": "remote_unconfirmed"}, fmt.Errorf("工况命令超时；远端状态未知，不得自动重发：%w", q.Err())
	}
	var result map[string]any
	if e := decodeBridgeOutput(raw, &result); e != nil {
		return result, fmt.Errorf("工况执行日志不可解析：%v %s", e, trim(string(raw), 260))
	}
	if err != nil {
		return result, fmt.Errorf("Workbench 工况操作失败或未知：%v；%v", err, result["detail"])
	}
	if mode == "preview" && result["status"] != "review_required" {
		return result, fmt.Errorf("未获得可审核的发布计划")
	}
	if mode == "publish" && result["status"] != "user_modified_settings_readback_match" {
		return result, fmt.Errorf("修改设置未通过回读比对，不能标记为完成；禁止直接重试")
	}
	return result, nil
}

// Persist a reviewed source-file PATH only, never publication approval.
func (a *App) rememberWBStandardSource(path string) error {
	root := filepath.Join(a.dataDir, "settings_exports")
	clean, e := filepath.Abs(path)
	if e != nil {
		return e
	}
	if filepath.Clean(filepath.Dir(clean)) != filepath.Clean(root) || !strings.EqualFold(filepath.Ext(clean), ".set") {
		return fmt.Errorf("基准源必须来自程序导出的设置文件目录")
	}
	st, e := os.Stat(clean)
	if e != nil || st.IsDir() {
		return fmt.Errorf("设置文件不存在")
	}
	return os.WriteFile(filepath.Join(a.dataDir, "wb_standard_source.txt"), []byte(clean), 0600)
}
func (a *App) lastWBStandardSource() string {
	b, e := os.ReadFile(filepath.Join(a.dataDir, "wb_standard_source.txt"))
	if e != nil {
		return ""
	}
	path := strings.TrimSpace(string(b))
	root := filepath.Join(a.dataDir, "settings_exports")
	clean, e := filepath.Abs(path)
	if e != nil {
		return ""
	}
	if filepath.Clean(filepath.Dir(clean)) != filepath.Clean(root) || !strings.EqualFold(filepath.Ext(clean), ".set") {
		return ""
	}
	if st, e := os.Stat(clean); e == nil && !st.IsDir() {
		return clean
	}
	return ""
}
