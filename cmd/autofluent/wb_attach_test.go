package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func wbVerifiedExample(status string) WorkbenchAttach {
	return WorkbenchAttach{Status: status, Port: 9935, Security: "mtls", Python: "python.exe"}
}

func TestWBTransportProofRequiresVerifiedStatusAndIdentity(t *testing.T) {
	for _, status := range []string{"workbench_connected", "workbench_connected_scan_failed", "no_fluent_system", "fluent_probe_failed", "fluent_connected", "selection_required"} {
		if !verifiedWBTransport(wbVerifiedExample(status)) {
			t.Errorf("expected verified: %s", status)
		}
	}
	for _, status := range []string{"error", "missing_pyworkbench", "timeout", "workbench_unavailable", "workbench_recheck_required"} {
		if verifiedWBTransport(wbVerifiedExample(status)) {
			t.Errorf("unverified status accepted: %s", status)
		}
	}
	x := wbVerifiedExample("workbench_connected")
	x.Python = ""
	if verifiedWBTransport(x) {
		t.Fatal("missing Python accepted")
	}
	x.Python = "python.exe"
	x.Security = "auto"
	if verifiedWBTransport(x) {
		t.Fatal("unverified security accepted")
	}
}

func TestFluentFailureCannotReuseStaleWBSession(t *testing.T) {
	old := wbVerifiedExample("fluent_connected")
	old.Systems = []WorkbenchSystem{{Name: "FFF", Fluent: true, Container: "Setup"}}
	old.TUI = map[string]any{"status": "tui_probe_submitted"}
	next := mergeWBContext(old, WorkbenchAttach{Status: "error", Port: 9935}, "wb-fluent", errors.New("connection lost"))
	if next.Status != "workbench_recheck_required" || verifiedWBTransport(next) {
		t.Fatalf("stale WB still usable: %+v", next)
	}
	if len(next.Systems) != 0 || next.TUI != nil {
		t.Fatal("stale Fluent proof retained")
	}
}

func TestFluentAbsentKeepsWBConnection(t *testing.T) {
	old := wbVerifiedExample("workbench_connected")
	result := wbVerifiedExample("no_fluent_system")
	got := mergeWBContext(old, result, "wb-fluent", nil)
	if !verifiedWBTransport(got) || got.Status != "no_fluent_system" {
		t.Fatalf("WB should stay usable: %+v", got)
	}
}

func TestManualWBReplacesPriorIdentity(t *testing.T) {
	old := wbVerifiedExample("fluent_connected")
	newResult := WorkbenchAttach{Status: "missing_pyworkbench", Port: 9935}
	got := mergeWBContext(old, newResult, "wb-manual", nil)
	if got.Status != "missing_pyworkbench" || verifiedWBTransport(got) {
		t.Fatal("manual reconnect did not invalidate stale identity")
	}
}

func TestManualWorkbenchUsesOnlySpecifiedPortWithWorkingInterpreter(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("mock POSIX interpreter")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "mock-python.sh")
	content := `#!/bin/sh
if [ "$1" = "-c" ]; then
 echo AF_WB_READY
else
 case " $* " in
 *" --mode manual --port 9935 --security insecure "*) echo '{"status":"workbench_connected","port":9935,"security":"insecure"}';;
 *) echo '{"status":"error","detail":"unexpected port or command"}';;
 esac
fi
`
	if e := os.WriteFile(script, []byte(content), 0700); e != nil {
		t.Fatal(e)
	}
	if e := os.MkdirAll(filepath.Join(dir, "worker"), 0755); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(filepath.Join(dir, "worker", "af_wb_bridge.py"), []byte("# mock"), 0600); e != nil {
		t.Fatal(e)
	}
	app := &App{base: dir, dataDir: dir, python: script}
	previousProbe := wbPortProbe
	wbPortProbe = func(p int) error {
		if p != 9935 {
			return errors.New("wrong port")
		}
		return nil
	}
	defer func() { wbPortProbe = previousProbe }()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	got, e := app.manualWorkbench(ctx, 9935)
	if e != nil || got.Status != "workbench_connected" || got.Port != 9935 || got.Python != script {
		t.Fatalf("port handshake expected: %+v %v", got, e)
	}
}

func TestWBModesTryThreeDistinctTransports(t *testing.T) {
	// A missing AutoMesh cache must not suppress insecure/WNUA retries.
	got := wbModes(9935)
	seen := map[string]bool{}
	for _, m := range got {
		seen[m] = true
	}
	if len(got) != 3 || !seen["mtls"] || !seen["insecure"] || !seen["wnua"] {
		t.Fatalf("all modes must be probed: %v", got)
	}
}

func TestWBConnectFallsBackToInsecureOnSamePort(t *testing.T) {
	// Force an AM-provided mTLS preference with mock certificate metadata.
	// Only the transport response is mocked; no real credentials are used.
	t.Setenv("ANSYS_GRPC_CERTIFICATES", "")
	cache := t.TempDir()
	t.Setenv("ProgramData", cache)
	t.Setenv("LOCALAPPDATA", t.TempDir())
	folder := filepath.Join(cache, "AutoMesh2024R2", "Data")
	if err := os.MkdirAll(folder, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(folder, "last_connection.json"), []byte(`{"port":9935,"security":"mtls"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" {
		t.Skip("POSIX mock interpreter")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "mock-wb-python.sh")
	source := `#!/bin/sh
if [ "$1" = "-c" ];then
 echo AF_WB_READY
 exit 0
fi
case " $* " in
 *" --mode manual --port 9935 --security mtls "*) echo '{"status":"workbench_unavailable","port":9935,"attempts":["mtls / connect_workbench: RuntimeError: auth fail"]}';;
 *" --mode manual --port 9935 --security insecure "*) echo '{"status":"workbench_connected","port":9935,"security":"insecure"}';;
 *) echo '{"status":"error","detail":"unexpected command"}';;
esac
`
	if err := os.WriteFile(script, []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "worker"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "worker", "af_wb_bridge.py"), []byte("# mocked"), 0600); err != nil {
		t.Fatal(err)
	}
	prev := wbPortProbe
	wbPortProbe = func(p int) error {
		if p != 9935 {
			return errors.New("wrong port")
		}
		return nil
	}
	defer func() { wbPortProbe = prev }()
	if err := os.MkdirAll(filepath.Join(dir, "certs"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "certs", "ca.crt"), []byte("fixture-only"), 0600); err != nil {
		t.Fatal(err)
	}
	app := &App{base: dir, dataDir: dir, python: script}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	result, err := app.manualWorkbench(ctx, 9935)
	if err != nil || result.Status != "workbench_connected" || result.Security != "insecure" {
		t.Fatalf("expected insecure fallback success: %+v %v", result, err)
	}
	if len(result.Attempts) == 0 || !strings.Contains(strings.Join(result.Attempts, " "), "auth fail") {
		t.Fatalf("lost original mTLS failure: %v", result.Attempts)
	}
}

func TestWBHandshakeAllowsPythonWarningBeforeJSON(t *testing.T) {
	var result WorkbenchAttach
	raw := []byte("DeprecationWarning: python package\r\n{\"status\":\"workbench_connected\",\"port\":9935,\"security\":\"insecure\"}\r\n")
	if err := decodeWBHandshakeResponse(raw, &result); err != nil {
		t.Fatal(err)
	}
	if result.Status != "workbench_connected" || result.Port != 9935 || result.Security != "insecure" {
		t.Fatalf("result not parsed: %+v", result)
	}
}

// Python 3.14 is supported by ansys-workbench-core 0.14.0. Never force a
// nonexistent `py -3.11` launcher as the primary or mandatory path.
func TestWBPythonCandidatesDoNotRequirePython311(t *testing.T) {
	list := workbenchPythonCandidates("python.exe")
	if len(list) == 0 {
		t.Fatal("missing Python candidates")
	}
	for _, candidate := range list {
		if candidate == "py -3.11" || candidate == "py -3.12" {
			t.Fatalf("pinned launcher falsely required: %s", candidate)
		}
	}
}

func TestWBLocalRuntimeTakesPriorityWithoutChangingSystemPython(t *testing.T) {
	dir := t.TempDir()
	a := &App{dataDir: dir, python: "python.exe"}
	candidates := a.wbCandidates()
	want := filepath.Join(dir, "wb_runtime", "bin", "python")
	if runtime.GOOS == "windows" {
		want = filepath.Join(dir, "wb_runtime", "Scripts", "python.exe")
	}
	if len(candidates) == 0 || candidates[0] != want {
		t.Fatalf("private WB runtime not first: %v", candidates)
	}
}

func TestProbeRunnablePythonRejectsBadLauncher(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := probeRunnablePython(ctx, "missing-uninstalled-runtime"); err == nil {
		t.Fatal("accepted uninstalled Python launcher")
	}
}

func TestWBAutoModesAvoidUnconfiguredMTLS(t *testing.T) {
	t.Setenv("ProgramData", t.TempDir())
	t.Setenv("LOCALAPPDATA", t.TempDir())
	t.Setenv("ANSYS_GRPC_CERTIFICATES", "")
	modes := wbModes(9935)
	if len(modes) != 3 || modes[0] != "insecure" || modes[1] != "wnua" || modes[2] != "mtls" {
		t.Fatalf("preferred non-certificate modes first: %v", modes)
	}
	ok, why := wbMTLSCredentialsPresent(t.TempDir())
	if ok || !strings.Contains(why, "ca.crt") {
		t.Fatalf("missing mTLS certificate accepted: %v %s", ok, why)
	}
}

func TestWBAMCachedWNUAIsFirst(t *testing.T) {
	root := t.TempDir()
	t.Setenv("ProgramData", root)
	t.Setenv("LOCALAPPDATA", t.TempDir())
	folder := filepath.Join(root, "AutoMesh2024R2", "Data")
	if err := os.MkdirAll(folder, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(folder, "last_connection.json"), []byte(`{"port":9935,"security":"wnua"}`), 0600); err != nil {
		t.Fatal(err)
	}
	modes := wbModes(9935)
	if modes[0] != "wnua" {
		t.Fatalf("did not reuse AM chosen transport: %v", modes)
	}
}

func TestWBDiagnosticTextPreservesAllTransportFailures(t *testing.T) {
	r := WorkbenchAttach{Status: "workbench_unavailable", Port: 9935, Attempts: []string{
		"insecure / connect_workbench: protocol mismatch",
		"wnua / run_script_string: server unavailable",
		"mtls: certs/ca.crt not found",
	}}
	result := wbDiagnosticText(9935, r, nil)
	for _, fragment := range []string{"9935", "insecure / connect_workbench", "wnua / run_script_string", "certs/ca.crt"} {
		if !strings.Contains(result, fragment) {
			t.Fatalf("missing %s", fragment)
		}
	}
}

func TestWBDiagnosticFileContainsEntireAttemptList(t *testing.T) {
	dir := t.TempDir()
	a := &App{dataDir: dir}
	r := WorkbenchAttach{Status: "workbench_unavailable", Port: 9935, Attempts: []string{
		strings.Repeat("first failure details ", 30),
		"wnua: real error from transport layer",
	}}
	path, err := a.saveWBDiagnostic(9935, r, nil)
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(content), r.Attempts[0]) || !strings.Contains(string(content), r.Attempts[1]) {
		t.Fatal("connection details truncated")
	}
}
