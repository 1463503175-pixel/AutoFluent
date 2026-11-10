package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

const Version = "2.10.4-geometry-baffle-export"

func exportReportCopy(dataDir, source, directory string) (string, error) {
	if !isApprovedReportPath(dataDir, source) {
		return "", errors.New("没有已验证的 HTML 报告可导出")
	}
	dir, err := filepath.Abs(directory)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		return "", errors.New("报告保存目录必须是已存在的文件夹")
	}
	destination := filepath.Join(dir, filepath.Base(source))
	if strings.EqualFold(filepath.Clean(source), destination) {
		return source, nil
	}
	in, err := os.Open(source)
	if err != nil {
		return "", err
	}
	defer in.Close()
	out, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0666)
	if err != nil {
		return "", err
	}
	_, copyErr := io.Copy(out, in)
	closeErr := out.Close()
	if copyErr != nil || closeErr != nil {
		_ = os.Remove(destination)
		return "", errors.Join(copyErr, closeErr)
	}
	return destination, nil
}

// isApprovedReportPath allows only an existing AutoFluent-generated HTML
// under one of the three report stores. Rel checks path components rather than
// a string prefix; EvalSymlinks rejects a malicious link leaving dataDir.
func isApprovedReportPath(dataDir, requested string) bool {
	if strings.TrimSpace(requested) == "" || !strings.EqualFold(filepath.Ext(requested), ".html") {
		return false
	}
	info, err := os.Stat(requested)
	if err != nil || info.IsDir() {
		return false
	}
	actual, err := filepath.EvalSymlinks(requested)
	if err != nil {
		return false
	}
	actual, err = filepath.Abs(actual)
	if err != nil {
		return false
	}
	for _, folder := range []string{"libraries", "saved_reports", "native_reports"} {
		root := filepath.Join(dataDir, folder)
		resolved, err := filepath.EvalSymlinks(root)
		if err != nil {
			continue
		}
		rel, err := filepath.Rel(resolved, actual)
		if err == nil && rel != "." && rel != ".." && !filepath.IsAbs(rel) && !strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
			return true
		}
	}
	return false
}

// JSON recovery input must stay inside the generated native_reports folder.
// Do not allow arbitrary local JSON to be used as an executable report source.
func isApprovedAnalysisJSON(dataDir, requested string) bool {
	if strings.TrimSpace(requested) == "" || !strings.EqualFold(filepath.Ext(requested), ".json") {
		return false
	}
	info, err := os.Stat(requested)
	if err != nil || info.IsDir() {
		return false
	}
	actual, err := filepath.EvalSymlinks(requested)
	if err != nil {
		return false
	}
	actual, err = filepath.Abs(actual)
	if err != nil {
		return false
	}
	folder, err := filepath.EvalSymlinks(filepath.Join(dataDir, "native_reports"))
	if err != nil {
		return false
	}
	relative, err := filepath.Rel(folder, actual)
	return err == nil && relative != "." && relative != ".." && !filepath.IsAbs(relative) && !strings.HasPrefix(relative, ".."+string(os.PathSeparator))
}

// HTML can be recreated from a computed JSON snapshot without rereading a
// 26-million-face saved solution. Failures never change physical result status.
func (a *App) rerenderNativeJSON(ctx context.Context, source string) (map[string]any, error) {
	if !isApprovedAnalysisJSON(a.dataDir, source) {
		return nil, errors.New("Only a saved native_report JSON may be re-rendered")
	}
	python, err := a.h5PythonCommand()
	if err != nil {
		return nil, err
	}
	folder := filepath.Join(a.dataDir, "native_reports")
	args := append(append([]string{}, python[1:]...), filepath.Join(a.base, "worker", "af_render_saved.py"),
		"--json", source, "--output-dir", folder)
	cmd := exec.CommandContext(ctx, python[0], args...)
	configureChildProcess(cmd)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	output, execErr := cmd.CombinedOutput()
	var reply map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(output), &reply); err != nil {
		return nil, fmt.Errorf("HTML re-export returned no JSON: %v; %s", execErr, trim(string(output), 400))
	}
	if execErr != nil {
		return reply, fmt.Errorf("HTML re-export failed: %v", reply["reason"])
	}
	html, _ := reply["html_file"].(string)
	if !isApprovedReportPath(a.dataDir, html) {
		return reply, errors.New("Re-exported HTML path was not approved")
	}
	return reply, nil
}

type App struct {
	mu                                              sync.Mutex
	base, dataDir, python, serverInfo, templatePath string
	template                                        map[string]any
	lastScan                                        map[string]any
	history                                         []HistoryItem
	busy                                            bool
}

type HistoryItem struct {
	At     string `json:"at"`
	Action string `json:"action"`
	Status string `json:"status"`
	Detail string `json:"detail"`
}
type Candidate struct {
	Path     string `json:"path"`
	Modified string `json:"modified"`
}

func newApp(base string) *App {
	root, err := os.UserConfigDir()
	if err != nil || root == "" {
		root = os.TempDir()
	}
	dir := filepath.Join(root, "AutoFluentV1")
	_ = os.MkdirAll(dir, 0700)
	a := &App{base: base, dataDir: dir, history: []HistoryItem{}}
	var cfg struct {
		Python       string `json:"python"`
		ServerInfo   string `json:"serverInfo"`
		TemplatePath string `json:"templatePath"`
	}
	if b, e := os.ReadFile(filepath.Join(dir, "settings.json")); e == nil {
		_ = json.Unmarshal(b, &cfg)
	}
	a.python = cfg.Python
	a.serverInfo = cfg.ServerInfo
	a.templatePath = cfg.TemplatePath
	_ = a.loadTemplatePath(cfg.TemplatePath)
	if b, e := os.ReadFile(filepath.Join(dir, "history.json")); e == nil {
		_ = json.Unmarshal(b, &a.history)
	}
	return a
}
func (a *App) saveConfig() error {
	a.mu.Lock()
	defer a.mu.Unlock()
	b, _ := json.MarshalIndent(map[string]string{"python": a.python, "serverInfo": a.serverInfo, "templatePath": a.templatePath}, "", "  ")
	return os.WriteFile(filepath.Join(a.dataDir, "settings.json"), b, 0600)
}
func (a *App) setConnection(python, info string) error {
	a.mu.Lock()
	a.python = strings.TrimSpace(python)
	a.serverInfo = strings.TrimSpace(info)
	a.mu.Unlock()
	return a.saveConfig()
}
func (a *App) loadTemplatePath(path string) error {
	if strings.TrimSpace(path) == "" {
		return nil
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	var data map[string]any
	if e = json.Unmarshal(b, &data); e != nil {
		return fmt.Errorf("模板不是有效 JSON：%w", e)
	}
	if data["schema"] != "autofluent.conditions.v1" {
		return errors.New("模板 schema 不符合 autofluent.conditions.v1")
	}
	a.template = data
	a.templatePath = path
	return nil
}
func (a *App) saveTemplate(data map[string]any) (string, error) {
	if data["schema"] != "autofluent.conditions.v1" {
		return "", errors.New("模板 schema 错误")
	}
	if _, ok := data["fields"].([]any); !ok {
		return "", errors.New("缺少 fields 数组")
	}
	if _, ok := data["zones"].([]any); !ok {
		return "", errors.New("缺少 zones 数组")
	}
	delete(data, "saved_template_path")
	b, e := json.MarshalIndent(data, "", "  ")
	if e != nil {
		return "", e
	}
	path := filepath.Join(a.dataDir, "current_template.json")
	if e = os.WriteFile(path, b, 0600); e != nil {
		return "", e
	}
	a.mu.Lock()
	a.template = data
	a.templatePath = path
	a.mu.Unlock()
	return path, a.saveConfig()
}
func (a *App) templateJSON() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.template == nil {
		return ""
	}
	b, _ := json.MarshalIndent(a.template, "", "  ")
	return string(b)
}
func (a *App) snapshot() (string, string, string, map[string]any) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.python, a.serverInfo, a.templatePath, a.template
}
func (a *App) pythonCommand() []string {
	python, _, _, _ := a.snapshot()
	s := strings.TrimSpace(python)
	if s == "" {
		s = os.Getenv("AUTOFLUENT_PYTHON")
	}
	if s == "" {
		if runtime.GOOS == "windows" {
			return []string{"py", "-3"}
		}
		return []string{"python3"}
	}
	if strings.HasPrefix(s, "\"") {
		if i := strings.Index(s[1:], "\""); i >= 0 {
			return append([]string{s[1 : i+1]}, strings.Fields(s[i+2:])...)
		}
	}
	if strings.HasSuffix(strings.ToLower(s), ".exe") {
		return []string{s}
	}
	return strings.Fields(s)
}
func (a *App) runWorker(ctx context.Context, action, confirm string, tmpl map[string]any, expectedPlan string) (map[string]any, error) {
	_, server, _, current := a.snapshot()
	if tmpl == nil {
		tmpl = current
	}
	if action != "selftest" {
		if !validServerInfo(server) {
			return nil, errors.New("缺少有效的 Fluent server-info；请先在 Fluent 启动 gRPC 服务并选择对应文件")
		}
	}
	worker := filepath.Join(a.base, "worker", "af_worker.py")
	if _, e := os.Stat(worker); e != nil {
		return nil, fmt.Errorf("缺少 Python Worker: %s", worker)
	}
	command := a.pythonCommand()
	if len(command) == 0 {
		return nil, errors.New("没有 Python 命令")
	}
	req := map[string]any{"action": action, "server_info": server, "template": tmpl, "confirm": confirm, "template_id": "ALD-EXTRACTED", "expected_plan_signature": expectedPlan}
	b, _ := json.Marshal(req)
	args := append(append([]string{}, command[1:]...), worker)
	cmd := exec.CommandContext(ctx, command[0], args...)
	cmd.Dir = a.base
	configureChildProcess(cmd)
	cmd.Stdin = bytes.NewReader(b)
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	var out, errs bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errs
	err := cmd.Run()
	if ctx.Err() != nil {
		return nil, errors.New("任务超时或取消；如果尝试过发布，请在 Fluent 中核验当前设置")
	}
	if err != nil {
		return nil, fmt.Errorf("Worker 启动失败：%w\n%s", err, trim(errs.String(), 1200))
	}
	var answer map[string]any
	if e := json.Unmarshal(bytes.TrimSpace(out.Bytes()), &answer); e != nil {
		return nil, fmt.Errorf("Worker 未返回合法 JSON：%s; %s", e, trim(out.String(), 500))
	}
	if answer["ok"] != true {
		return answer, fmt.Errorf("%v", answer["error"])
	}
	return answer, nil
}
func trim(s string, max int) string {
	r := []rune(s)
	if len(r) > max {
		return string(r[:max]) + "…"
	}
	return s
}
func (a *App) tryStart() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.busy {
		return false
	}
	a.busy = true
	return true
}
func (a *App) finish() { a.mu.Lock(); a.busy = false; a.mu.Unlock() }
func (a *App) record(action, status, details string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.history = append([]HistoryItem{{At: time.Now().Format("2006-01-02 15:04:05"), Action: action, Status: status, Detail: trim(details, 350)}}, a.history...)
	if len(a.history) > 60 {
		a.history = a.history[:60]
	}
	b, _ := json.MarshalIndent(a.history, "", "  ")
	_ = os.WriteFile(filepath.Join(a.dataDir, "history.json"), b, 0600)
}
func (a *App) histories() []HistoryItem {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]HistoryItem{}, a.history...)
}
func (a *App) extractResult(result map[string]any) (string, error) {
	data, ok := result["data"].(map[string]any)
	if !ok {
		return "", errors.New("工况模板提取结果无效")
	}
	b, e := json.MarshalIndent(data, "", "  ")
	if e != nil {
		return "", e
	}
	p := filepath.Join(a.dataDir, "baseline_extracted.json")
	if e = os.WriteFile(p, b, 0600); e != nil {
		return "", e
	}
	a.mu.Lock()
	a.template = data
	a.templatePath = p
	a.mu.Unlock()
	return p, a.saveConfig()
}
func validServerInfo(path string) bool {
	st, e := os.Stat(path)
	if e != nil || st.IsDir() || st.Size() < 10 || st.Size() > 8192 {
		return false
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return false
	}
	l := strings.Split(strings.TrimSpace(string(b)), "\n")
	if len(l) < 2 {
		return false
	}
	first := strings.TrimSpace(l[0])
	i := strings.LastIndex(first, ":")
	return i > 0 && i < len(first)-1 && strings.TrimSpace(l[1]) != ""
}
func (a *App) discover() []Candidate {
	dirs := []string{os.TempDir(), a.base, os.Getenv("TEMP"), os.Getenv("TMP"), os.Getenv("LOCALAPPDATA"), os.Getenv("APPDATA")}
	_, saved, _, _ := a.snapshot()
	if saved != "" {
		dirs = append(dirs, filepath.Dir(saved))
	}
	found := []Candidate{}
	seen := map[string]bool{}
	cutoff := time.Now().Add(-7 * 24 * time.Hour)
	// Fluent may generate the server info beside its executable or in a
	// Workbench working directory; search only shallow known locations.
	if root := os.Getenv("AWP_ROOT242"); root != "" {
		dirs = append(dirs, filepath.Join(root, "fluent", "ntbin", "win64"), filepath.Join(root, "fluent", "bin"))
	}
	if home := os.Getenv("USERPROFILE"); home != "" {
		dirs = append(dirs, home, filepath.Join(home, "Documents", "Ansys"))
	}
	for _, dir := range dirs {
		if dir == "" || seen[strings.ToLower(dir)] {
			continue
		}
		seen[strings.ToLower(dir)] = true
		entries, e := os.ReadDir(dir)
		if e != nil {
			continue
		}
		if len(entries) > 8000 {
			continue
		} // avoid expensive filesystem walks
		for _, f := range entries {
			if f.IsDir() {
				continue
			}
			name := strings.ToLower(f.Name())
			// Fluent's generated server-info filenames may have no extension.
			if !(strings.Contains(name, "server") || strings.Contains(name, "fluent")) ||
				!(strings.HasSuffix(name, ".txt") || strings.Contains(name, "server-info") || strings.Contains(name, "server_info")) {
				continue
			}
			p := filepath.Join(dir, f.Name())
			info, e := f.Info()
			if e != nil || info.ModTime().Before(cutoff) || !validServerInfo(p) {
				continue
			}
			found = append(found, Candidate{p, info.ModTime().Format("2006-01-02 15:04")})
		}
	}
	sort.Slice(found, func(i, j int) bool { return found[i].Modified > found[j].Modified })
	return found
}
func (a *App) metrics(path string) (map[string]any, error) {
	info, e := os.Stat(path)
	if e != nil || info.IsDir() || info.Size() > 32*1024*1024 {
		return nil, errors.New("统计文件不存在或超过 32 MB")
	}
	ext := strings.ToLower(filepath.Ext(path))
	if ext != ".json" && ext != ".html" && ext != ".htm" {
		return nil, errors.New("请导入历史 Codex HTML 或逐通道通量 JSON")
	}
	command := a.pythonCommand()
	if len(command) == 0 {
		return nil, errors.New("没有 Python 命令")
	}
	args := append(append([]string{}, command[1:]...), filepath.Join(a.base, "worker", "af_metrics.py"), path)
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, command[0], args...)
	cmd.Dir = a.base
	configureChildProcess(cmd)
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	out, e := cmd.CombinedOutput()
	if e != nil {
		return nil, fmt.Errorf("计算失败：%v %s", e, trim(string(out), 600))
	}
	var result map[string]any
	if e = json.Unmarshal(bytes.TrimSpace(out), &result); e != nil {
		return nil, e
	}
	return result, nil
}

// h5PythonCommand prefers a Python that imports h5py + numpy + PyFluent,
// falling back to h5py-only for indexed diagnostics only. No downloads or
// environment changes, and no need to ask the operator for a Python path.
func (a *App) h5PythonCommand() ([]string, error) {
	initial := a.pythonCommand()
	candidates := [][]string{initial}
	home, _ := os.UserHomeDir()
	if home != "" {
		candidates = append(candidates,
			[]string{filepath.Join(home, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe")})
		dirs, _ := filepath.Glob(filepath.Join(home, ".cache", "codex-runtimes", "*", "dependencies", "python", "python.exe"))
		for _, path := range dirs {
			candidates = append(candidates, []string{path})
		}
	}
	if runtime.GOOS == "windows" {
		// ANSYS 2024 R2 bundled CPython is known to include numpy/h5py in the
		// user's proven native workflow. No installs and no change to PATH.
		if root := os.Getenv("AWP_ROOT242"); root != "" {
			candidates = append(candidates, []string{filepath.Join(root, "commonfiles", "CPython", "3_10", "winx64", "Release", "python", "python.exe")})
		}
		for _, root := range []string{`E:\ANSYS Inc\v242`, `C:\Program Files\ANSYS Inc\v242`} {
			candidates = append(candidates, []string{filepath.Join(root, "commonfiles", "CPython", "3_10", "winx64", "Release", "python", "python.exe")})
		}
		candidates = append(candidates, []string{"py", "-3.11"}, []string{"py", "-3.12"}, []string{"py", "-3"}, []string{"python"})
	} else {
		candidates = append(candidates, []string{"python3"}, []string{"python"})
	}
	seen := map[string]bool{}
	details := []string{}
	var fallback []string
	for _, candidate := range candidates {
		if len(candidate) == 0 {
			continue
		}
		key := strings.ToLower(strings.Join(candidate, " "))
		if seen[key] {
			continue
		}
		seen[key] = true
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		args := append(append([]string{}, candidate[1:]...), "-c", "import h5py,numpy;import importlib.util;print('AUTOFLUENT_H5_FULL' if (importlib.util.find_spec('ansys') and importlib.util.find_spec('ansys.fluent') and importlib.util.find_spec('ansys.fluent.core')) else 'AUTOFLUENT_H5_ONLY')")
		cmd := exec.CommandContext(ctx, candidate[0], args...)
		configureChildProcess(cmd)
		out, err := cmd.CombinedOutput()
		cancel()
		if err == nil && strings.Contains(string(out), "AUTOFLUENT_H5_FULL") {
			return candidate, nil
		}
		if err == nil && strings.Contains(string(out), "AUTOFLUENT_H5_ONLY") && fallback == nil {
			fallback = append([]string{}, candidate...)
		}
		if len(details) < 4 {
			details = append(details, key+": "+trim(string(out), 90))
		}
	}
	if fallback != nil {
		return fallback, nil // Index-only; Python reader will explicitly state PyFluent is missing.
	}
	return nil, fmt.Errorf("没有发现可用的 H5 Python 环境（h5py + numpy），已检查现有 Python 环境。诊断：%s", strings.Join(details, "; "))
}

// inspectSavedH5 reads the existing pair offline: no WB port and no running Fluent.
// H5 structural success does not certify physical/wafer-gap analysis.
func (a *App) inspectSavedH5(selected string) (map[string]any, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 480*time.Second)
	defer cancel()
	return a.inspectSavedH5Context(ctx, selected)
}

// inspectSavedH5Context allows the visible Cancel button to stop a local H5
// reader process. It has no remote Fluent side effects.
func (a *App) inspectSavedH5Context(ctx context.Context, selected string) (map[string]any, error) {
	return a.inspectSavedH5ConfigContext(ctx, selected, "")
}

// An explicitly selected geometry configuration must never fall back to the
// historical 11.9 profile. Offline analysis does not require a WB connection.
func (a *App) inspectSavedH5ConfigContext(ctx context.Context, selected, configPath string) (map[string]any, error) {
	return a.inspectSavedH5ConfigWithProgress(ctx, selected, configPath, "")
}

func (a *App) inspectSavedH5ConfigWithProgress(ctx context.Context, selected, configPath, progressPath string) (map[string]any, error) {
	s := strings.ToLower(strings.TrimSpace(selected))
	if !strings.HasSuffix(s, ".cas.h5") && !strings.HasSuffix(s, ".dat.h5") {
		return nil, errors.New("请选择已保存的 .cas.h5 或 .dat.h5")
	}
	info, err := os.Stat(selected)
	if err != nil || info.IsDir() || info.Size() < 100 {
		return nil, errors.New("Case/Data 文件不存在或文件过小")
	}
	command, envErr := a.h5PythonCommand()
	if envErr != nil {
		return nil, envErr
	}
	outputDir := filepath.Join(a.dataDir, "h5_indexes")
	args := append(append([]string{}, command[1:]...),
		filepath.Join(a.base, "worker", "af_h5_offline.py"),
		"--file", selected, "--output-dir", outputDir, "--skip-pyfluent")
	if configPath != "" {
		if fi, err := os.Stat(configPath); err != nil || fi.IsDir() {
			return nil, errors.New("指定的几何配置 JSON 文件不存在")
		}
		args = append(args, "--config", configPath)
	}
	cmd := exec.CommandContext(ctx, command[0], args...)
	configureChildProcess(cmd)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	if progressPath != "" {
		cmd.Env = append(cmd.Env, "AUTOFLUENT_PROGRESS_FILE="+progressPath)
	}
	out, e := cmd.CombinedOutput()
	if ctx.Err() != nil {
		return nil, fmt.Errorf("H5 读取已取消或超时：%w（原始 Case/Data 未修改）", ctx.Err())
	}
	var result map[string]any
	if je := json.Unmarshal(bytes.TrimSpace(out), &result); je != nil {
		return nil, fmt.Errorf("H5 读取程序没有返回 JSON: %v; %s", e, trim(string(out), 700))
	}
	if e != nil {
		return result, fmt.Errorf("H5 分析尚未完成: %v; %v", e, result["error"])
	}
	// Geometry validation can fail after genuine port/pressure/baffle results
	// have been computed. This is a successful partial read, not a corrupt H5.
	if result["status"] == "partial_physical_metrics" {
		return result, nil
	}
	if result["status"] != "pair_verified_structure_indexed" {
		if configPath != "" {
			if info, ok := result["configured_analysis"].(map[string]any); ok {
				return result, fmt.Errorf("配置模式物理核验未通过：%v", info["detail"])
			}
			return result, errors.New("配置模式未完成几何或原生通量检查；已停止，不回退自动识别")
		}
		return result, errors.New("未能完成 Case/Data 结构核验")
	}
	return result, nil
}

// Case-only geometry precheck: reads actual mesh wall geometry and never starts
// Workbench or Fluent. A blocked wafer pairing is a readable diagnostic result,
// not evidence that the Case is invalid or the saved solution was corrupted.
func (a *App) geometryPrecheckH5(ctx context.Context, selected, configPath, progressPath string) (map[string]any, error) {
	command, err := a.h5PythonCommand()
	if err != nil {
		return nil, err
	}
	args := append(append([]string{}, command[1:]...),
		filepath.Join(a.base, "worker", "af_h5_offline.py"),
		"--file", selected, "--geometry-check", "--output-dir", filepath.Join(a.dataDir, "geometry_prechecks"))
	if configPath != "" {
		args = append(args, "--config", configPath)
	}
	cmd := exec.CommandContext(ctx, command[0], args...)
	configureChildProcess(cmd)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	if progressPath != "" {
		cmd.Env = append(cmd.Env, "AUTOFLUENT_PROGRESS_FILE="+progressPath)
	}
	out, runErr := cmd.CombinedOutput()
	var result map[string]any
	if jsonErr := json.Unmarshal(bytes.TrimSpace(out), &result); jsonErr != nil {
		return nil, fmt.Errorf("几何预检未返回有效 JSON: %v; %s", runErr, trim(string(out), 700))
	}
	if runErr != nil {
		return result, fmt.Errorf("几何预检执行错误: %v", runErr)
	}
	return result, nil
}

// Real common-cut comparison always reruns each saved CFF snapshot using
// a shared explicit physical geometry configuration; no cached percentages.
func (a *App) recomputeCommonCut(ctx context.Context, before, after, configPath, progressPath string) (map[string]any, error) {
	command, err := a.h5PythonCommand()
	if err != nil {
		return nil, err
	}
	args := append(append([]string{}, command[1:]...),
		filepath.Join(a.base, "worker", "af_common_cut_compare.py"),
		"--before", before, "--after", after, "--config", configPath,
		"--output-dir", filepath.Join(a.dataDir, "comparisons"))
	cmd := exec.CommandContext(ctx, command[0], args...)
	configureChildProcess(cmd)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	if progressPath != "" {
		cmd.Env = append(cmd.Env, "AUTOFLUENT_PROGRESS_FILE="+progressPath)
	}
	out, runErr := cmd.CombinedOutput()
	var result map[string]any
	if e := json.Unmarshal(bytes.TrimSpace(out), &result); e != nil {
		return nil, fmt.Errorf("统一截面对比未返回有效 JSON: %v; %s", runErr, trim(string(out), 750))
	}
	if runErr != nil {
		return result, fmt.Errorf("统一截面复算失败: %v; %v", runErr, result["reason"])
	}
	return result, nil
}

// Archive a compact, read-only Fluent setting snapshot: not a Case/Data copy.
func (a *App) archiveScan(snapshot map[string]any) (string, error) {
	if snapshot["schema"] != "autofluent.snapshot.v1" {
		return "", errors.New("只读扫描数据的结构不匹配")
	}
	b, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	dir := filepath.Join(a.dataDir, "snapshots")
	if err = os.MkdirAll(dir, 0700); err != nil {
		return "", err
	}
	name := fmt.Sprintf("scan_%s_%x.json", time.Now().Format("20060102_150405"), sum[:4])
	path := filepath.Join(dir, name)
	if err = os.WriteFile(path, b, 0600); err != nil {
		return "", err
	}
	return path, nil
}

// compareFiles is an import-only foundation, NOT an automatic Fluent field analysis.
func (a *App) compareFiles(left, right string) (map[string]any, error) {
	if left == "" || right == "" {
		return nil, errors.New("请选择两份 AutoFluent 保存解结果 JSON（或历史报告）")
	}
	for _, p := range []string{left, right} {
		info, e := os.Stat(p)
		if e != nil || info.IsDir() || info.Size() > 32*1024*1024 {
			return nil, fmt.Errorf("文件缺失或超过32MB：%s", p)
		}
	}
	// The same local Python environment that read H5 can compare the generated
	// saved-analysis JSON. Do not require a second manually configured runtime.
	command, pyErr := a.h5PythonCommand()
	if pyErr != nil {
		command = a.pythonCommand() // Legacy HTML/JSON comparison is pure Python.
	}
	if len(command) == 0 {
		return nil, errors.New("没有找到可用于结果对比的 Python")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	args := append(append([]string{}, command[1:]...), filepath.Join(a.base, "worker", "af_compare.py"), left, right)
	cmd := exec.CommandContext(ctx, command[0], args...)
	cmd.Dir = a.base
	configureChildProcess(cmd)
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	raw, e := cmd.CombinedOutput()
	if e != nil {
		return nil, fmt.Errorf("对比程序失败：%w；%s", e, trim(string(raw), 900))
	}
	var result map[string]any
	if e = json.Unmarshal(raw, &result); e != nil {
		return nil, fmt.Errorf("对比结果 JSON 无效：%w", e)
	}
	return result, nil
}
func pretty(v any) string {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return fmt.Sprint(v)
	}
	return string(b)
}

// previewIdentity binds local template bytes and connection identity to the preview.
// This is an additional client-side guard, not a guarantee of identical geometry.
func (a *App) previewIdentity() string {
	python, server, _, template := a.snapshot()
	b, _ := json.Marshal(template)
	st, err := os.ReadFile(server)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(append([]byte(python+"\x00"+server+"\x00"), append(st, b...)...))
	return hex.EncodeToString(sum[:])
}

func (a *App) isBusy() bool { a.mu.Lock(); defer a.mu.Unlock(); return a.busy }

// The last EXPLICITLY entered port is only an editable convenience value.
// It is never probed until the user clicks Connect.
func (a *App) savedWBPort() string {
	data, err := os.ReadFile(filepath.Join(a.dataDir, "last_user_wb_port.txt"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}
func (a *App) storeWBPort(port int) error {
	if port < 1024 || port > 65535 {
		return errors.New("Workbench 端口超出范围")
	}
	return os.WriteFile(filepath.Join(a.dataDir, "last_user_wb_port.txt"), []byte(fmt.Sprint(port)), 0600)
}

// runLocalAnalyzer uses a bundled, read-only Python script with bounded time.
// It never contacts Workbench or starts Fluent.
func (a *App) runLocalAnalyzer(scriptName string, arguments ...string) (map[string]any, error) {
	allowed := map[string]bool{"af_report_library.py": true, "af_h5_batch.py": true}
	if !allowed[scriptName] {
		return nil, errors.New("unsupported local analyzer")
	}
	command := a.pythonCommand()
	if len(command) == 0 {
		return nil, errors.New("没有可用 Python")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	args := append(append([]string{}, command[1:]...), filepath.Join(a.base, "worker", scriptName))
	args = append(args, arguments...)
	cmd := exec.CommandContext(ctx, command[0], args...)
	cmd.Dir = a.base
	configureChildProcess(cmd)
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	raw, err := cmd.CombinedOutput()
	var result map[string]any
	if e := json.Unmarshal(bytes.TrimSpace(raw), &result); e != nil {
		return nil, fmt.Errorf("离线任务未返回合法 JSON: %s", trim(string(raw), 500))
	}
	if err != nil {
		return result, fmt.Errorf("离线任务失败: %v: %v", err, result["error"])
	}
	return result, nil
}
func (a *App) scanHistoricalReports(path string) (map[string]any, error) {
	if path == "" {
		return nil, errors.New("请选择历史报告文件或输入目录")
	}
	stat, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	root := path
	if !stat.IsDir() {
		root = filepath.Dir(path)
	}
	return a.runLocalAnalyzer("af_report_library.py", "--root", root,
		"--output-dir", filepath.Join(a.dataDir, "libraries"))
}
func (a *App) scanSavedH5Directory(path string) (map[string]any, error) {
	if path == "" {
		return nil, errors.New("请选择 Case/Data 文件或输入目录")
	}
	stat, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	root := path
	if !stat.IsDir() {
		root = filepath.Dir(path)
	}
	return a.runLocalAnalyzer("af_h5_batch.py", "--root", root,
		"--output-dir", filepath.Join(a.dataDir, "libraries"))
}
