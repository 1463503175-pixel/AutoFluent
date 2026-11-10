package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// WorkbenchAttach matches AutoMesh's authenticated existing-session bridge.
// Port and process detection alone never count as a connection. This is an
// adapter, NOT evidence that every Fluent Case setting has been read back.
type WorkbenchSystem struct {
	Name        string `json:"name"`
	Label       string `json:"label"`
	Fluent      bool   `json:"fluent"`
	Container   string `json:"container"`
	Evidence    string `json:"evidence"`
	HasSetup    bool   `json:"has_setup"`
	HasSolution bool   `json:"has_solution"`
}
type WBFileCandidate struct {
	Path          string  `json:"path"`
	Bytes         int64   `json:"bytes"`
	ModifiedEpoch float64 `json:"modified_epoch"`
	Kind          string  `json:"kind"`
}

type WorkbenchAttach struct {
	ProjectFile         string            `json:"project_file"`
	ProjectDir          string            `json:"project_dir"`
	CaseDataCandidates  []WBFileCandidate `json:"case_data_candidates"`
	ProjectIdentityNote string            `json:"project_identity_note"`
	Status              string            `json:"status"`
	Detail              string            `json:"detail"`
	Port                int               `json:"port"`
	Security            string            `json:"security"`
	Python              string            `json:"python"`
	Source              string            `json:"source"`
	Systems             []WorkbenchSystem `json:"systems"`
	AllSystems          []WorkbenchSystem `json:"all_systems"`
	Attempts            []string          `json:"attempts"`
	Bootstrap           map[string]any    `json:"bootstrap"`
	TUI                 map[string]any    `json:"tui"`
	WBProbe             map[string]any    `json:"wb_probe"`
}

// Probe for PyWorkbench as AutoMesh does; do not require PyFluent and do not
// accidentally choose the first Python (which might lack ansys.workbench.core).
func probeWorkbenchPython(ctx context.Context, candidate string) (string, error) {
	parts := splitCommand(candidate)
	if len(parts) == 0 {
		return "", fmt.Errorf("empty candidate")
	}
	q, cancel := context.WithTimeout(ctx, 9*time.Second)
	defer cancel()
	args := append(append([]string{}, parts[1:]...), "-c", "import sys;import ansys.workbench.core;print('AF_WB_READY')")
	cmd := exec.CommandContext(q, parts[0], args...)
	configureChildProcess(cmd)
	b, e := cmd.CombinedOutput()
	if e != nil || !bytes.Contains(b, []byte("AF_WB_READY")) {
		why := trim(string(b), 160)
		if why == "" && e != nil {
			why = e.Error()
		}
		return "", fmt.Errorf("%s: %s", candidate, why)
	}
	return candidate, nil
}
func (a *App) autoWorkbench(ctx context.Context) (WorkbenchAttach, error) {
	saved, _, _, _ := a.snapshot()
	candidates := pythonCandidates(saved)
	script := filepath.Join(a.base, "worker", "af_wb_bridge.py")
	if _, err := os.Stat(script); err != nil {
		return WorkbenchAttach{}, err
	}
	var python string
	for _, candidate := range candidates {
		if ctx.Err() != nil {
			break
		}
		if _, err := probeWorkbenchPython(ctx, candidate); err == nil {
			python = candidate
			break
		}
	}
	if python == "" {
		return WorkbenchAttach{Status: "missing_pyworkbench", Detail: "当前候选 Python 均无法导入 ansys.workbench.core"}, nil
	}
	parts := splitCommand(python)
	sub, cancel := context.WithTimeout(ctx, 65*time.Second)
	defer cancel()
	args := append(append([]string{}, parts[1:]...), script, "--mode", "auto")
	cmd := exec.CommandContext(sub, parts[0], args...)
	cmd.Dir = a.base
	configureChildProcess(cmd)
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	b, err := cmd.CombinedOutput()
	if sub.Err() != nil {
		return WorkbenchAttach{Status: "timeout", Python: python, Detail: "Workbench 验证超时；未自动重复提交 StartServer"}, sub.Err()
	}
	var result WorkbenchAttach
	if e := decodeBridgeOutput(b, &result); e != nil {
		return WorkbenchAttach{Status: "error", Python: python, Detail: trim(string(b), 700)}, fmt.Errorf("Workbench bridge response invalid: %w", e)
	}
	result.Python = python
	if err != nil {
		return result, fmt.Errorf("Workbench bridge failure: %w; %s", err, trim(string(b), 350))
	}
	return result, nil
}

func (a *App) probeWorkbenchSelected(ctx context.Context, existing WorkbenchAttach, system string) (WorkbenchAttach, error) {
	if existing.Port < 1024 || existing.Port > 65535 || system == "" {
		return WorkbenchAttach{}, fmt.Errorf("invalid Workbench selection")
	}
	found := false
	for _, candidate := range existing.Systems {
		if candidate.Name == system && candidate.Fluent {
			found = true
		}
	}
	if !found {
		return WorkbenchAttach{}, fmt.Errorf("selected Fluent system was not in the validated Workbench list")
	}
	p := splitCommand(existing.Python)
	script := filepath.Join(a.base, "worker", "af_wb_bridge.py")
	args := append(append([]string{}, p[1:]...), script, "--mode", "probe", "--port", fmt.Sprint(existing.Port), "--security", existing.Security, "--system", system)
	q, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	cmd := exec.CommandContext(q, p[0], args...)
	configureChildProcess(cmd)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	err := cmd.Run()
	if q.Err() != nil {
		return WorkbenchAttach{}, q.Err()
	}
	var r WorkbenchAttach
	if e := decodeBridgeOutput(output.Bytes(), &r); e != nil {
		return r, fmt.Errorf("invalid Workbench selected probe response: %w (%s)", e, trim(output.String(), 240))
	}
	r.Python = existing.Python
	if err != nil {
		return r, fmt.Errorf("Workbench selected probe failed: %v", err)
	}
	return r, nil
}

// workbenchPythonCandidates discovers actual local runtimes instead of assuming
// Python 3.11 is installed.  Python 3.14 is supported by PyWorkbench 0.14.
// Returned strings are executable paths or validated launcher aliases; no
// global installation or filesystem-wide recursive search is performed.
func workbenchPythonCandidates(saved string) []string {
	out := []string{}
	seen := map[string]bool{}
	add := func(p string) {
		p = strings.TrimSpace(p)
		if p == "" || seen[strings.ToLower(p)] {
			return
		}
		seen[strings.ToLower(p)] = true
		out = append(out, p)
	}
	add(os.Getenv("AUTOFLUENT_WB_PYTHON"))
	add(os.Getenv("AUTOMESH_PYTHON"))
	add(saved)
	for _, candidate := range pythonCandidates(saved) {
		add(candidate)
	}
	return out
}

// Local WB environment is independent of the legacy PyFluent Python setting.
func (a *App) wbCandidates() []string {
	local := filepath.Join(a.dataDir, "wb_runtime", "Scripts", "python.exe")
	if os.PathSeparator == '/' {
		local = filepath.Join(a.dataDir, "wb_runtime", "bin", "python")
	}
	return append([]string{local}, workbenchPythonCandidates(a.python)...)
}

// Python launcher aliases such as `py -3.11` are not evidence that a runtime
// exists.  This readonly probe only uses the Python standard library.
func probeRunnablePython(ctx context.Context, candidate string) (string, error) {
	parts := splitCommand(candidate)
	if len(parts) == 0 {
		return "", fmt.Errorf("empty Python candidate")
	}
	q, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	args := append(append([]string{}, parts[1:]...), "-c", "import sys;assert (3,10)<=sys.version_info[:2]<(4,0);print(sys.executable)")
	cmd := exec.CommandContext(q, parts[0], args...)
	configureChildProcess(cmd)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("%s: %w; %s", candidate, err, trim(strings.TrimSpace(string(out)), 150))
	}
	exe := strings.TrimSpace(string(out))
	if exe == "" {
		return "", fmt.Errorf("%s did not report executable", candidate)
	}
	return exe, nil
}

// Install only after explicit approval, in a private virtualenv. No changes to
// system Python, Workbench, Fluent or the user's simulation project.
func (a *App) installPyWorkbench(ctx context.Context) (string, error) {
	root := filepath.Join(a.dataDir, "wb_runtime")
	venvPython := filepath.Join(root, "Scripts", "python.exe")
	if os.PathSeparator == '/' {
		venvPython = filepath.Join(root, "bin", "python")
	}
	if _, e := probeWorkbenchPython(ctx, venvPython); e == nil {
		return venvPython, nil // Idempotent: do not reinstall a working environment.
	}
	// Prefer an explicitly installed interpreter; reject missing py -3.11.
	// The current computer's Python 3.14 is acceptable.
	var source string
	failed := []string{}
	for _, candidate := range workbenchPythonCandidates(a.python) {
		exe, e := probeRunnablePython(ctx, candidate)
		if e == nil && filepath.Clean(exe) != filepath.Clean(venvPython) {
			source = candidate
			break
		}
		if len(failed) < 4 && e != nil {
			failed = append(failed, trim(e.Error(), 150))
		}
	}
	if source == "" {
		return "", fmt.Errorf("没有可用的 Python 3.10+，已检查现有解释器：%s", strings.Join(failed, " | "))
	}
	if err := os.MkdirAll(a.dataDir, 0700); err != nil {
		return "", err
	}
	if stat, err := os.Stat(venvPython); err != nil || stat.IsDir() {
		parts := splitCommand(source)
		args := append(append([]string{}, parts[1:]...), "-m", "venv", root)
		cmd := exec.CommandContext(ctx, parts[0], args...)
		configureChildProcess(cmd)
		if raw, e := cmd.CombinedOutput(); e != nil {
			return "", fmt.Errorf("独立 WB 环境创建失败: %w: %s", e, trim(string(raw), 500))
		}
	}
	cmd := exec.CommandContext(ctx, venvPython, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "ansys-workbench-core==0.14.0")
	cmd.Env = append(os.Environ(), "PIP_DISABLE_PIP_VERSION_CHECK=1", "PIP_NO_INPUT=1")
	configureChildProcess(cmd)
	if raw, e := cmd.CombinedOutput(); e != nil {
		return "", fmt.Errorf("PyWorkbench 安装失败（检查网络或 pip）：%w: %s", e, trim(string(raw), 900))
	}
	if _, e := probeWorkbenchPython(ctx, venvPython); e != nil {
		return "", fmt.Errorf("安装后 PyWorkbench 导入核验失败：%w", e)
	}
	return venvPython, nil
}

// wbModes tries the protocol selected by AutoMesh for this very same port
// first, then all public supported modes. A remembered mode is only a hint.
func wbModes(port int) []string {
	modes := []string{}
	for _, root := range []string{filepath.Join(os.Getenv("ProgramData"), "AutoMesh2024R2", "Data"),
		filepath.Join(os.Getenv("LOCALAPPDATA"), "AutoMesh2024R2", "Data")} {
		b, err := os.ReadFile(filepath.Join(root, "last_connection.json"))
		if err != nil {
			continue
		}
		var v struct {
			Port     int    `json:"port"`
			Security string `json:"security"`
		}
		if json.Unmarshal(b, &v) == nil && v.Port == port &&
			(v.Security == "mtls" || v.Security == "insecure" || v.Security == "wnua") {
			modes = append(modes, v.Security)
			break
		}
	}
	for _, mode := range []string{"insecure", "wnua", "mtls"} {
		found := false
		for _, m := range modes {
			if m == mode {
				found = true
				break
			}
		}
		if !found {
			modes = append(modes, mode)
		}
	}
	return modes
}

// wbMTLSCredentialsPresent verifies the client-side files required by mTLS.
// It does not create self-signed certificates, search unrelated directories,
// or silently disable a server's security policy. The mTLS transport package
// reports "certs/ca.crt" relative to the process working directory when the
// ANSYS_GRPC_CERTIFICATES environment variable is absent.
func wbMTLSCredentialsPresent(appBase string) (bool, string) {
	roots := []string{}
	if dir := strings.TrimSpace(os.Getenv("ANSYS_GRPC_CERTIFICATES")); dir != "" {
		if !filepath.IsAbs(dir) {
			dir = filepath.Join(appBase, dir)
		}
		roots = append(roots, dir)
	}
	roots = append(roots, filepath.Join(appBase, "certs"))
	for _, dir := range roots {
		if info, err := os.Stat(filepath.Join(dir, "ca.crt")); err == nil && !info.IsDir() {
			return true, dir
		}
	}
	return false, "客户端没有可用的 ca.crt；mTLS 连接必须由实际 Workbench 服务提供匹配证书。已检查 ANSYS_GRPC_CERTIFICATES 与 AutoFluent\\certs，不会伪造或跳过证书校验"
}

// wbPortReachability checks only the exact user-supplied port. A failed TCP
// probe is a diagnosis, not a reason to scan or switch ports.
func wbPortReachability(port int) error {
	var failed []string
	for _, host := range []string{"127.0.0.1", "[::1]"} {
		addr := fmt.Sprintf("%s:%d", host, port)
		conn, err := net.DialTimeout("tcp", addr, 700*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			return nil
		}
		failed = append(failed, err.Error())
	}
	return fmt.Errorf("当前端口 %d 无本机 TCP 监听响应：%s", port, trim(strings.Join(failed, "; "), 240))
}

var wbPortProbe = wbPortReachability // test-replaceable; production uses local TCP read-only probe

// decodeWBHandshakeResponse accepts the last complete machine-readable JSON
// line from the child process. PyWorkbench and Python may emit import warnings
// on stderr; CombinedOutput must not mistake a valid handshake for garbage.
func decodeWBHandshakeResponse(raw []byte, target *WorkbenchAttach) error {
	if err := decodeBridgeOutput(raw, target); err != nil {
		return fmt.Errorf("%w；输出摘要：%s", err, trim(string(raw), 300))
	}
	return nil
}

// manualWorkbench only uses the operator-provided port. Each transport test
// has its own subprocess timeout; a stuck mTLS handshake cannot prevent trying
// insecure or WNUA on Workbench 2024 R2. No StartServer or solver command.
func (a *App) manualWorkbench(ctx context.Context, port int) (WorkbenchAttach, error) {
	if port < 1024 || port > 65535 {
		return WorkbenchAttach{}, fmt.Errorf("Workbench 端口必须在 1024～65535 范围内")
	}
	script := filepath.Join(a.base, "worker", "af_wb_bridge.py")
	if _, err := os.Stat(script); err != nil {
		return WorkbenchAttach{}, err
	}
	if err := wbPortProbe(port); err != nil {
		return WorkbenchAttach{Status: "wb_port_not_listening", Port: port, Detail: err.Error(), Attempts: []string{err.Error()}}, nil
	}
	attempts := []string{}
	readyCount := 0
	for _, candidate := range a.wbCandidates() {
		if ctx.Err() != nil {
			break
		}
		if _, err := probeWorkbenchPython(ctx, candidate); err != nil {
			if len(attempts) < 20 {
				attempts = append(attempts, "Python 环境："+trim(err.Error(), 240))
			}
			continue
		}
		readyCount++
		parts := splitCommand(candidate)
		for _, sec := range wbModes(port) {
			if ctx.Err() != nil {
				break
			}
			if sec == "mtls" {
				if ok, why := wbMTLSCredentialsPresent(a.base); !ok {
					attempts = append(attempts, candidate+" / mtls：未尝试；"+why)
					continue
				}
			}
			checkCtx, cancel := context.WithTimeout(ctx, 18*time.Second)
			args := append(append([]string{}, parts[1:]...), script, "--mode", "manual", "--port", fmt.Sprint(port), "--security", sec)
			cmd := exec.CommandContext(checkCtx, parts[0], args...)
			cmd.Dir = a.base
			configureChildProcess(cmd)
			cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
			raw, runErr := cmd.CombinedOutput()
			timedOut := checkCtx.Err() != nil
			cancel()
			prefix := fmt.Sprintf("%s / %s：", candidate, sec)
			if timedOut {
				attempts = append(attempts, prefix+"认证/脚本握手超时（单模式18秒），已安全结束本次诊断进程")
				continue
			}
			var result WorkbenchAttach
			if e := decodeWBHandshakeResponse(raw, &result); e != nil {
				attempts = append(attempts, prefix+"返回格式错误："+trim(e.Error(), 310))
				continue
			}
			if result.Status == "workbench_connected" && runErr == nil && result.Security == sec &&
				(result.Port == 0 || result.Port == port) {
				result.Port = port
				result.Python = candidate
				result.Attempts = attempts
				return result, nil
			}
			diagnostic := result.Detail
			// Distinguish an absent certificate from a remote handshake error.
			// Neither proves anything about the *other* supported transports.
			if len(result.Attempts) > 0 {
				diagnostic += " " + strings.Join(result.Attempts, "; ")
			}
			if runErr != nil {
				diagnostic += " (" + runErr.Error() + ")"
			}
			attempts = append(attempts, prefix+trim(diagnostic, 390))
		}
		// Workbench can be installed in several Python runtimes; try a few
		// complete compatible environments without exhausting the GUI task.
		if readyCount >= 3 {
			break
		}
	}
	status := "workbench_unavailable"
	detail := "端口有 TCP 响应，但 PyWorkbench 握手失败；详见每个 Python/认证模式的真实异常。"
	if readyCount == 0 {
		status = "missing_pyworkbench"
		detail = "未找到可导入 ansys.workbench.core 的 Python。AutoFluent 未尝试安装或更改环境。"
	}
	if ctx.Err() != nil {
		status = "timeout"
		detail = "诊断时间耗尽；没有自动换端口，也未向 Fluent 发送命令。"
	}
	if len(attempts) > 0 {
		// Capture the whole authentication matrix, not only the first mTLS
		// error. An absent ca.crt is local configuration, not a port verdict.
		var handshake, certificate, runtime string
		for _, attempt := range attempts {
			switch {
			case strings.Contains(attempt, "connect_workbench") || strings.Contains(attempt, "run_script_string"):
				if handshake == "" {
					handshake = attempt
				}
			case strings.Contains(attempt, "ca.crt"):
				if certificate == "" {
					certificate = attempt
				}
			default:
				if runtime == "" {
					runtime = attempt
				}
			}
		}
		if handshake != "" {
			detail += " 握手诊断：" + trim(handshake, 300)
		}
		if certificate != "" {
			detail += " 证书诊断：" + trim(certificate, 200)
		}
		if handshake == "" && certificate == "" && runtime != "" {
			detail += " 环境诊断：" + trim(runtime, 240)
		}
	}
	return WorkbenchAttach{Status: status, Port: port, Detail: detail, Attempts: attempts}, nil
}

// wbDiagnosticText provides the untruncated per-transport evidence. No
// certificates or local simulation Case/Data are read or included.
func wbDiagnosticText(port int, result WorkbenchAttach, err error) string {
	var b strings.Builder
	fmt.Fprintf(&b, "AutoFluent WB connection diagnostic\nTime: %s\nRequested port: %d\n", time.Now().Format(time.RFC3339), port)
	fmt.Fprintf(&b, "Status: %s\nVerified transport: %s\nInterpreter: %s\n", result.Status, result.Security, result.Python)
	fmt.Fprintf(&b, "Detail: %s\n", result.Detail)
	if err != nil {
		fmt.Fprintf(&b, "Error: %v\n", err)
	}
	for i, attempt := range result.Attempts {
		fmt.Fprintf(&b, "\nAttempt %d: %s\n", i+1, attempt)
	}
	b.WriteString("\nA TCP listener is not proof of a Workbench handshake. This file contains only local connection diagnostics.\n")
	return b.String()
}

func (a *App) saveWBDiagnostic(port int, result WorkbenchAttach, err error) (string, error) {
	filename := filepath.Join(a.dataDir, "wb_last_connection_diagnostic.txt")
	if e := os.MkdirAll(a.dataDir, 0700); e != nil {
		return "", e
	}
	if e := os.WriteFile(filename, []byte(wbDiagnosticText(port, result, err)), 0600); e != nil {
		return "", e
	}
	return filename, nil
}

// probeFluentFromWorkbench is an explicit second action; WB-only handshake
// never requires a running Fluent editor.
func (a *App) probeFluentFromWorkbench(ctx context.Context, existing WorkbenchAttach) (WorkbenchAttach, error) {
	if existing.Port < 1024 || existing.Port > 65535 ||
		(existing.Security != "mtls" && existing.Security != "insecure" && existing.Security != "wnua") {
		return WorkbenchAttach{}, fmt.Errorf("no verified Workbench port and security")
	}
	parts := splitCommand(existing.Python)
	if len(parts) == 0 {
		return WorkbenchAttach{}, fmt.Errorf("missing verified PyWorkbench Python")
	}
	script := filepath.Join(a.base, "worker", "af_wb_bridge.py")
	args := append(append([]string{}, parts[1:]...), script, "--mode", "fluent", "--port", fmt.Sprint(existing.Port), "--security", existing.Security)
	q, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(q, parts[0], args...)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	configureChildProcess(cmd)
	raw, err := cmd.CombinedOutput()
	if q.Err() != nil {
		return WorkbenchAttach{Status: "timeout", Port: existing.Port, Security: existing.Security, Python: existing.Python}, q.Err()
	}
	var result WorkbenchAttach
	if e := decodeBridgeOutput(raw, &result); e != nil {
		return WorkbenchAttach{Status: "error", Detail: trim(string(raw), 500)}, fmt.Errorf("Fluent probe response invalid: %w", e)
	}
	result.Python = existing.Python
	if result.Port != 0 && result.Port != existing.Port {
		return result, fmt.Errorf("Fluent probe port mismatch")
	}
	result.Port = existing.Port
	result.Security = existing.Security
	if err != nil {
		return result, fmt.Errorf("Fluent probe failed: %w", err)
	}
	return result, nil
}

// verifiedWBTransport is the only acceptable basis for later Fluent checks.
// A transport error, a source-side failure or a mere port echo is not evidence
// that a live Workbench script executed successfully.
func verifiedWBTransport(info WorkbenchAttach) bool {
	switch info.Status {
	case "workbench_connected", "workbench_connected_scan_failed", "no_fluent_system",
		"fluent_probe_failed", "fluent_connected", "selection_required":
		return info.Port >= 1024 && info.Port <= 65535 && info.Python != "" &&
			(info.Security == "mtls" || info.Security == "wnua" || info.Security == "insecure")
	default:
		return false
	}
}

// Preserve the last verified WB identity when Fluent enumeration or TUI
// probing fails. Do not silently replace a verified transport by an unverified
// error object, which would make the next click contact an incorrect session.
func mergeWBContext(previous, incoming WorkbenchAttach, action string, err error) WorkbenchAttach {
	if action == "wb-manual" {
		return incoming
	}
	if verifiedWBTransport(incoming) && err == nil {
		if previous.Port == incoming.Port && previous.Security == incoming.Security {
			if incoming.ProjectFile == "" {
				incoming.ProjectFile = previous.ProjectFile
			}
			if incoming.ProjectDir == "" {
				incoming.ProjectDir = previous.ProjectDir
			}
			if len(incoming.CaseDataCandidates) == 0 {
				incoming.CaseDataCandidates = previous.CaseDataCandidates
			}
		}
		return incoming
	}
	// We did not receive proof of a successful WB round trip on this attempt.
	// Do not claim that the last cached connection is still alive, and do not
	// allow a subsequent click to dispatch Fluent commands without reattaching.
	if verifiedWBTransport(previous) {
		previous.Status = "workbench_recheck_required"
		previous.Detail = "Workbench 会话可能已关闭或认证失效。请重新连接原端口；不会自动换端口。"
		previous.Systems = nil
		previous.TUI = nil
		return previous
	}
	return incoming
}

// exportWBSettings copies no Case/Data and never starts a Fluent instance.
// Only an explicit button from a verified WB->Fluent session may invoke it.
// A real on-disk Fluent settings export is required; WB dispatch alone is not success.
func (a *App) exportWBSettings(ctx context.Context, existing WorkbenchAttach) (map[string]any, error) {
	if existing.Status != "fluent_connected" || !verifiedWBTransport(existing) {
		return nil, fmt.Errorf("尚未验证 Workbench 到当前 Fluent 的控制通道")
	}
	system, ok := existing.TUI["system"].(string)
	if !ok || system == "" {
		return nil, fmt.Errorf("未能唯一确认当前 Fluent 系统")
	}
	found := false
	for _, s := range existing.Systems {
		if s.Name == system && s.Fluent {
			found = true
		}
	}
	if !found {
		return nil, fmt.Errorf("当前 Fluent 身份与连接列表不一致")
	}
	py := splitCommand(existing.Python)
	if len(py) == 0 {
		return nil, fmt.Errorf("缺少已验证的 PyWorkbench Python")
	}
	dest := filepath.Join(a.dataDir, "settings_exports")
	if e := os.MkdirAll(dest, 0700); e != nil {
		return nil, e
	}
	args := append(append([]string{}, py[1:]...), filepath.Join(a.base, "worker", "af_wb_bridge.py"),
		"--mode", "settings", "--port", fmt.Sprint(existing.Port), "--security", existing.Security,
		"--system", system, "--output-dir", dest)
	if existing.ProjectFile != "" {
		args = append(args, "--expected-project", existing.ProjectFile)
	}
	q, cancel := context.WithTimeout(ctx, 55*time.Second)
	defer cancel()
	cmd := exec.CommandContext(q, py[0], args...)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	configureChildProcess(cmd)
	raw, err := cmd.CombinedOutput()
	if q.Err() != nil {
		return nil, fmt.Errorf("工况导出是否完成未知；不得自动重发：%w", q.Err())
	}
	var result map[string]any
	if e := decodeBridgeOutput(raw, &result); e != nil {
		return nil, fmt.Errorf("工况导出返回格式错误：%v；%s", e, trim(string(raw), 500))
	}
	if err != nil {
		return result, fmt.Errorf("Workbench 导出报错：%v", err)
	}
	if result["status"] != "settings_exported" {
		return result, fmt.Errorf("未验证到 Fluent 写出的设置文件，不得标为成功或自动重试")
	}
	file, _ := result["file"].(string)
	if file == "" {
		return result, fmt.Errorf("设置文件路径不存在")
	}
	if !strings.EqualFold(filepath.Dir(file), dest) {
		return result, fmt.Errorf("设置文件不在本次受控输出目录")
	}
	stat, e := os.Stat(file)
	if e != nil || stat.IsDir() || stat.Size() < 32 || stat.Size() > 64*1024*1024 {
		return result, fmt.Errorf("未能验证设置文件完整性")
	}
	return result, nil
}

// captureWBReport collects the built-in Fluent Summary Report from the CURRENT
// open editor, using the explicitly verified Workbench port. No solver launch,
// initialization, iteration, or Case/Data replacement. It is not a substitute
// for native SV_FLUX per-channel extraction.
func (a *App) captureWBReport(ctx context.Context, existing WorkbenchAttach) (map[string]any, error) {
	if existing.Status != "fluent_connected" || !verifiedWBTransport(existing) {
		return nil, fmt.Errorf("尚未验证当前 WB 端口上的 Fluent 编辑器")
	}
	system, ok := existing.TUI["system"].(string)
	if !ok || system == "" {
		return nil, fmt.Errorf("当前 Fluent 系统未唯一确认")
	}
	found := false
	for _, s := range existing.Systems {
		if s.Name == system && s.Fluent {
			found = true
		}
	}
	if !found {
		return nil, fmt.Errorf("Fluent 系统已改变，必须重新检查")
	}
	py := splitCommand(existing.Python)
	if len(py) == 0 {
		return nil, fmt.Errorf("缺少经确认的 PyWorkbench 解释器")
	}
	dest := filepath.Join(a.dataDir, "live_reports")
	if err := os.MkdirAll(dest, 0700); err != nil {
		return nil, err
	}
	args := append(append([]string{}, py[1:]...), filepath.Join(a.base, "worker", "af_wb_bridge.py"),
		"--mode", "report", "--port", fmt.Sprint(existing.Port), "--security", existing.Security,
		"--system", system, "--output-dir", dest)
	if existing.ProjectFile != "" {
		args = append(args, "--expected-project", existing.ProjectFile)
	}
	q, cancel := context.WithTimeout(ctx, 85*time.Second)
	defer cancel()
	cmd := exec.CommandContext(q, py[0], args...)
	configureChildProcess(cmd)
	cmd.Dir = a.base
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	raw, err := cmd.CombinedOutput()
	if q.Err() != nil {
		return nil, fmt.Errorf("Fluent 报告采集超时，远端状态未知，不得自动重发：%w", q.Err())
	}
	var output map[string]any
	if e := decodeBridgeOutput(raw, &output); e != nil {
		return nil, fmt.Errorf("Fluent 报告返回数据格式错误：%v；%s", e, trim(string(raw), 500))
	}
	if err != nil {
		return output, fmt.Errorf("Workbench 报告采集错误：%v；%s", err, trim(string(raw), 400))
	}
	if output["status"] != "report_captured" {
		return output, fmt.Errorf("报告未通过内容验证，不能视为工况已提取；请检查日志及 transcript")
	}
	path, _ := output["file"].(string)
	if path == "" || !strings.EqualFold(filepath.Dir(path), dest) {
		return output, fmt.Errorf("报告不是受控输出路径")
	}
	st, e := os.Stat(path)
	if e != nil || st.IsDir() || st.Size() < 200 || st.Size() > 8*1024*1024 {
		return output, fmt.Errorf("未验证到完整的本地 Fluent 报告")
	}
	return output, nil
}
