package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
)

// Automatic discovery is read-only. In particular it never launches Fluent,
// installs Python packages or modifies the user's existing Solver.
type PythonProbe struct {
	Command    string `json:"command"`
	Executable string `json:"executable"`
	Version    string `json:"version"`
	PyFluent   string `json:"pyfluent"`
	Compatible bool   `json:"compatible"`
	Error      string `json:"error,omitempty"`
}

type SetupResult struct {
	Python            PythonProbe `json:"python"`
	PythonCandidates  int         `json:"python_candidates"`
	Servers           []Candidate `json:"servers"`
	Server            string      `json:"server"`
	Ready             bool        `json:"ready"`
	RequiresSelection bool        `json:"requires_selection"`
	Hints             []string    `json:"hints"`
}

func splitCommand(s string) []string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	if strings.HasPrefix(s, "\"") {
		if i := strings.Index(s[1:], "\""); i >= 0 {
			executable := s[1 : i+1]
			rest := strings.Fields(s[i+2:])
			return append([]string{executable}, rest...)
		}
	}
	if strings.HasSuffix(strings.ToLower(s), ".exe") {
		return []string{s}
	}
	return strings.Fields(s)
}

// Executable paths obtained from the Python launcher are not trusted as commands;
// argv is assembled directly without a shell.
func pythonCandidates(saved string) []string {
	result := []string{}
	seen := map[string]bool{}
	add := func(s string) {
		s = strings.TrimSpace(s)
		if s == "" {
			return
		}
		key := strings.ToLower(s)
		if seen[key] {
			return
		}
		seen[key] = true
		result = append(result, s)
	}
	add(saved)
	add(os.Getenv("AUTOFLUENT_PYTHON"))
	for _, env := range []string{"VIRTUAL_ENV", "CONDA_PREFIX"} {
		if d := os.Getenv(env); d != "" {
			if runtime.GOOS == "windows" {
				add(filepath.Join(d, "Scripts", "python.exe"))
			} else {
				add(filepath.Join(d, "bin", "python"))
			}
		}
	}
	if p, e := os.Executable(); e == nil {
		root := filepath.Dir(p)
		if runtime.GOOS == "windows" {
			add(filepath.Join(root, "runtime", "Scripts", "python.exe"))
		}
	}
	if runtime.GOOS == "windows" {
		user := os.Getenv("LOCALAPPDATA")
		// Standard per-user and project-local installations, no recursive disk scan.
		for _, parent := range []string{filepath.Join(user, "Programs", "Python"), filepath.Join(os.Getenv("USERPROFILE"), "AppData", "Local", "Programs", "Python")} {
			if entries, err := os.ReadDir(parent); err == nil {
				sort.Slice(entries, func(i, j int) bool { return entries[i].Name() > entries[j].Name() })
				for _, e := range entries {
					if e.IsDir() && strings.HasPrefix(strings.ToLower(e.Name()), "python3") {
						add(filepath.Join(parent, e.Name(), "python.exe"))
					}
				}
			}
		}
		// py -0p lists the exact registered interpreter paths, not the launcher itself.
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		raw, err := exec.CommandContext(ctx, "py", "-0p").CombinedOutput()
		cancel()
		if err == nil {
			pat := regexp.MustCompile(`(?i)[A-Z]:\\[^\r\n]*?python(?:w)?\.exe`)
			for _, line := range strings.Split(string(raw), "\n") {
				if path := pat.FindString(line); path != "" {
					add(strings.TrimSpace(path))
				}
			}
		}
		for _, cmd := range []string{"python", "python3", "py -3"} {
			add(cmd)
		}
	} else {
		add("python3")
		add("python")
	}
	if len(result) > 32 {
		return result[:32]
	}
	return result
}

// Probing executes only Python's standard library and attempts one import; it
// does not call Fluent and does not access the network.
func probePython(ctx context.Context, candidate string) PythonProbe {
	out := PythonProbe{Command: candidate}
	parts := splitCommand(candidate)
	if len(parts) == 0 {
		out.Error = "空 Python 命令"
		return out
	}
	code := `import sys, json
result = {"executable": sys.executable,
          "version": "%d.%d.%d" % sys.version_info[:3],
          "compatible": (3, 10) <= sys.version_info[:2] <= (3, 14),
          "pyfluent": ""}
try:
    import ansys.fluent.core as core
    result["pyfluent"] = str(getattr(core, "__version__", "installed"))
except Exception as exc:
    result["import_error"] = str(exc)[:150]
print(json.dumps(result))`
	timeout, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	args := append(append([]string{}, parts[1:]...), "-c", code)
	cmd := exec.CommandContext(timeout, parts[0], args...)
	configureChildProcess(cmd)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if e := cmd.Run(); e != nil {
		out.Error = fmt.Sprintf("%v: %s", e, trim(stderr.String(), 160))
		return out
	}
	var data struct {
		Executable  string `json:"executable"`
		Version     string `json:"version"`
		Compatible  bool   `json:"compatible"`
		PyFluent    string `json:"pyfluent"`
		ImportError string `json:"import_error"`
	}
	if e := json.Unmarshal(bytes.TrimSpace(stdout.Bytes()), &data); e != nil {
		out.Error = "Python 自检未返回合法结果"
		return out
	}
	out.Executable = data.Executable
	out.Version = data.Version
	out.Compatible = data.Compatible
	out.PyFluent = data.PyFluent
	if data.ImportError != "" {
		out.Error = "PyFluent 未就绪: " + data.ImportError
	}
	if !out.Compatible {
		out.Error = "Python 版本不在 PyFluent 支持的 3.10–3.14 范围"
	}
	return out
}

// A valid file may belong to a Fluent session that has already closed.
// Probe only TCP reachability; real identity/authentication is still verified
// by PyFluent's subsequent read-only scan.
func serverReachable(path string) bool {
	if !validServerInfo(path) {
		return false
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return false
	}
	line := strings.TrimSpace(strings.SplitN(string(b), "\n", 2)[0])
	if _, _, e = net.SplitHostPort(line); e != nil {
		return false
	}
	c, e := net.DialTimeout("tcp", line, 350*time.Millisecond)
	if e != nil {
		return false
	}
	c.Close()
	return true
}

func chooseServerInfo(saved string, live []Candidate) (string, bool) {
	if saved != "" && serverReachable(saved) {
		return saved, false
	}
	switch len(live) {
	case 0:
		return "", false
	case 1:
		return live[0].Path, false
	default:
		return "", true
	}
}

func (a *App) autoSetup(ctx context.Context) SetupResult {
	savedPython, savedServer, _, _ := a.snapshot()
	// Filter stale server-info files. Multiple reachable sessions always
	// require an explicit target selection unless already saved by the user.
	live := []Candidate{}
	for _, candidate := range a.discover() {
		if ctx.Err() != nil {
			break
		}
		if serverReachable(candidate.Path) {
			live = append(live, candidate)
		}
	}
	result := SetupResult{Hints: []string{}, Servers: live}
	probes := pythonCandidates(savedPython)
	result.PythonCandidates = len(probes)
	var fallback *PythonProbe
	for _, name := range probes {
		if ctx.Err() != nil {
			break
		}
		candidate := probePython(ctx, name)
		if candidate.Executable == "" || !candidate.Compatible {
			continue
		}
		if candidate.PyFluent != "" {
			result.Python = candidate
			break
		}
		if fallback == nil {
			temp := candidate
			fallback = &temp
		}
	}
	if result.Python.Executable == "" && fallback != nil {
		result.Python = *fallback
	}
	if result.Python.Executable == "" {
		result.Hints = append(result.Hints, "没有找到可运行的 Python 3.10–3.14；需安装兼容 Python，之后可由本程序自动配置 PyFluent。")
	} else if result.Python.PyFluent == "" {
		result.Hints = append(result.Hints, "已检测到 Python，但 PyFluent 不可用；可点击「一键配置 PyFluent」创建隔离环境。")
	}
	// A previous explicit selection is authoritative only if its file still validates.
	result.Server, result.RequiresSelection = chooseServerInfo(savedServer, result.Servers)
	if result.RequiresSelection {
		result.Hints = append(result.Hints, "发现多个仍在线的 Fluent Server；为避免连接错误模型，需要选择目标会话。")
	} else if result.Server == "" {
		result.Hints = append(result.Hints, "没有发现可连接的 Fluent Server。当前 Fluent 内选择「文件 File → 应用程序 Applications → Server → Start」启动外部连接，再点击「自动连接」。不要继续输入之前失败的控制台命令。")
	}

	result.Ready = result.Python.Executable != "" && result.Python.PyFluent != "" && result.Server != ""
	return result
}

// User-initiated environment repair only: creates an isolated venv inside
// application data. Never changes system Python or quietly installs on startup.
func (a *App) installPyFluent(ctx context.Context, detected string) (string, error) {
	parts := splitCommand(detected)
	if len(parts) == 0 {
		return "", errors.New("找不到有效 Python，无法自动安装 PyFluent")
	}
	p := probePython(ctx, detected)
	if !p.Compatible || p.Executable == "" {
		return "", errors.New("请先确保机器有 Python 3.10–3.14：" + p.Error)
	}
	root := filepath.Join(a.dataDir, "pyfluent_runtime")
	venvPython := filepath.Join(root, "Scripts", "python.exe")
	if runtime.GOOS != "windows" {
		venvPython = filepath.Join(root, "bin", "python")
	}
	if st, e := os.Stat(venvPython); e != nil || st.IsDir() {
		args := append(append([]string{}, parts[1:]...), "-m", "venv", root)
		cmd := exec.CommandContext(ctx, parts[0], args...)
		configureChildProcess(cmd)
		if raw, e := cmd.CombinedOutput(); e != nil {
			return "", fmt.Errorf("创建独立 Python 环境失败：%w；%s", e, trim(string(raw), 800))
		}
	}
	cmd := exec.CommandContext(ctx, venvPython, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "ansys-fluent-core")
	cmd.Env = append(os.Environ(), "PIP_DISABLE_PIP_VERSION_CHECK=1", "PIP_NO_INPUT=1")
	configureChildProcess(cmd)
	if raw, e := cmd.CombinedOutput(); e != nil {
		return "", fmt.Errorf("PyFluent 安装失败（可能需要网络或补丁匹配）：%w；%s", e, trim(string(raw), 1000))
	}
	if check := probePython(ctx, venvPython); check.PyFluent == "" {
		return "", fmt.Errorf("安装后导入仍失败：%s", check.Error)
	}
	_, server, _, _ := a.snapshot()
	if e := a.setConnection(venvPython, server); e != nil {
		return "", e
	}
	return venvPython, nil
}

// User-facing next step is based on observed capability, not guesses about the cause.
func connectionNextStep(s SetupResult, err error) string {
	if err != nil && s.Ready {
		return "已找到连接条件，但 Fluent 回读失败；请查看具体错误。无需重新安装 Python。"
	}
	if s.RequiresSelection {
		return "找到多个可连接 Fluent 会话；只需选择目标模型一次，后续自动记住。"
	}
	if s.Server == "" {
		return "尚未发现已开启的外部连接服务。请优先验证 Workbench 桥接是否可用；如只支持独立 Fluent，需在当前 Solver 内启用一次 Server。不要重装 Python。"
	}
	if s.Python.Executable == "" {
		return "当前没找到可用的 Python 解释器；优先复用 AutoMesh 的 Python 环境。"
	}
	if s.Python.PyFluent == "" {
		return "找到 Python 但缺少 PyFluent；可一次性配置隔离环境，成功后无需重复。"
	}
	if s.Ready {
		return "环境和服务已就绪，正在自动只读扫描。"
	}
	return "连接条件尚不完整；具体诊断信息见下方。"
}
