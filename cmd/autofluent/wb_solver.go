package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// solveWBOnce is one user-authorized steady-state iteration batch. It NEVER
// initializes, changes solver settings, launches another Fluent, or retries.
// The Python worker holds an exclusive on-disk lease if remote execution is
// uncertain, including subprocess timeout or user closing the UI.
func (a *App) solveWBOnce(ctx context.Context, existing WorkbenchAttach, iterations int) (map[string]any, error) {
	if strings.TrimSpace(existing.ProjectFile) == "" {
		return nil, fmt.Errorf("当前 WB 工程文件身份无法核验；请先保存工程并重新检查 Fluent")
	}
	if iterations < 1 || iterations > 50 {
		return nil, fmt.Errorf("单批迭代数必须是 1～50")
	}
	if existing.Status != "fluent_connected" || !verifiedWBTransport(existing) {
		return nil, fmt.Errorf("没有经过验证的 WB → 已打开的 Fluent 连接")
	}
	system, _ := existing.TUI["system"].(string)
	count := 0
	for _, s := range existing.Systems {
		if s.Name == system && s.Fluent {
			count++
		}
	}
	if count != 1 {
		return nil, fmt.Errorf("Fluent 系统不是唯一的，禁止发送迭代命令")
	}
	py := splitCommand(existing.Python)
	if len(py) == 0 {
		return nil, fmt.Errorf("已验证的 PyWorkbench Python 不可用")
	}
	root := filepath.Join(a.dataDir, "solver_tasks")
	if e := os.MkdirAll(root, 0700); e != nil {
		return nil, e
	}
	args := append(append([]string{}, py[1:]...), filepath.Join(a.base, "worker", "af_wb_solver.py"),
		"--port", fmt.Sprint(existing.Port), "--security", existing.Security,
		"--system", system, "--iterations", fmt.Sprint(iterations), "--output-dir", root)
	if existing.ProjectFile != "" {
		args = append(args, "--expected-project", existing.ProjectFile)
	}
	// This is the operator's one-shot budget, not an automatic convergence loop.
	// If the process times out, Fluent may STILL BE ITERATING. A retained lease
	// forbids accidental resubmission; the GUI never kills the Fluent process.
	q, cancel := context.WithTimeout(ctx, 45*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(q, py[0], args...)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	configureChildProcess(cmd)
	output, err := cmd.CombinedOutput()
	if q.Err() != nil {
		return map[string]any{"status": "remote_execution_unconfirmed", "detail": q.Err().Error()}, fmt.Errorf("Workbench 迭代超时或本地等待中断，远端状态未知；禁止直接重复提交：%w", q.Err())
	}
	var result map[string]any
	if e := decodeBridgeOutput(output, &result); e != nil {
		return map[string]any{"status": "remote_execution_unconfirmed"}, fmt.Errorf("Workbench 迭代结果不可解析，须检查任务目录，不能重发：%v；%s", e, trim(string(output), 350))
	}
	if err != nil {
		return result, fmt.Errorf("Workbench 迭代未确认，禁止重试：%v；%s", err, trim(string(output), 350))
	}
	if result["status"] != "residuals_observed_not_convergence_certified" {
		return result, fmt.Errorf("命令是否实际完成无法确认；查看任务记录和 Fluent，严禁自动重发")
	}
	return result, nil
}

// Reading local progress is safe while WB blocks on the Fluent solve.
func (a *App) recentSolveStatus() (map[string]any, error) {
	root := filepath.Join(a.dataDir, "solver_tasks")
	dirs, e := os.ReadDir(root)
	if e != nil {
		return nil, e
	}
	latest := ""
	for _, dir := range dirs {
		if !dir.IsDir() || len(dir.Name()) != 32 {
			continue
		}
		candidate := filepath.Join(root, dir.Name(), "job.json")
		if st, e := os.Stat(candidate); e == nil && (latest == "" || st.ModTime().After(statMod(latest))) {
			latest = candidate
		}
	}
	if latest == "" {
		return nil, fmt.Errorf("还没有求解记录")
	}
	b, e := os.ReadFile(latest)
	if e != nil {
		return nil, e
	}
	var result map[string]any
	if e = json.Unmarshal(b, &result); e != nil {
		return nil, e
	}
	folder := filepath.Dir(latest)
	result["job_file"] = latest
	if b, e = os.ReadFile(filepath.Join(folder, "wb_receipt.json")); e == nil {
		var receipt map[string]any
		if json.Unmarshal(b, &receipt) == nil {
			result["remote_receipt"] = receipt
		}
	}
	if b, e = os.ReadFile(filepath.Join(folder, "residuals.json")); e == nil {
		var rs map[string]any
		if json.Unmarshal(b, &rs) == nil {
			result["monitor"] = map[string]any{"status": rs["status"], "latest": rs["latest"], "row_count": rs["row_count"]}
		}
	}
	if st, e := os.Stat(filepath.Join(folder, "solve.trn")); e == nil {
		result["transcript_bytes"] = st.Size()
	}
	_, err := os.Stat(filepath.Join(root, "active_solve.lock"))
	result["active_lease_retained"] = err == nil
	return result, nil
}
func statMod(path string) time.Time {
	st, e := os.Stat(path)
	if e != nil {
		return time.Time{}
	}
	return st.ModTime()
}

func latestSolverSummary(result map[string]any) string {
	var b strings.Builder
	fmt.Fprintf(&b, "任务：%v\r\n状态：%v\r\n授权迭代数：%v\r\n", result["job_id"], result["status"], result["requested_iterations"])
	if mon, ok := result["monitor"].(map[string]any); ok {
		fmt.Fprintf(&b, "残差记录：%v 行\r\n最后记录：%v\r\n", mon["row_count"], mon["latest"])
	}
	if result["active_lease_retained"] == true {
		b.WriteString("警告：任务锁仍在，不能重复投递。请先在 Fluent 核查远端状态。\r\n")
	}
	b.WriteString("残差并不能单独证明收敛。质量守恒及关键流量/压降稳定性尚需另行核验。")
	return b.String()
}
