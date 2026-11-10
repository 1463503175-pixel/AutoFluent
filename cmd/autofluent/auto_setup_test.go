package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestSplitCommandPreservesSpacesInQuotedExecutable(t *testing.T) {
	got := splitCommand(`"C:\Program Files\Python 3.11\python.exe" -I`)
	if len(got) != 2 || got[0] != `C:\Program Files\Python 3.11\python.exe` || got[1] != "-I" {
		t.Fatalf("%q", got)
	}
}

func TestProbePythonDoesNotNeedPyFluent(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("tested using local interpreter on Linux")
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 unavailable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result := probePython(ctx, python)
	if result.Executable == "" || !result.Compatible || !strings.HasPrefix(result.Version, "3.") {
		t.Fatalf("unexpected Python probe result: %+v", result)
	}
}

func TestDiscoverySelectsOnlyValidServerInfoFile(t *testing.T) {
	dir := t.TempDir()
	valid := filepath.Join(dir, "fluent-server-info.txt")
	invalid := filepath.Join(dir, "fluent-other.txt")
	if err := os.WriteFile(valid, []byte("127.0.0.1:59593\nsecret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(invalid, []byte("invalid\n"), 0600); err != nil {
		t.Fatal(err)
	}
	app := &App{base: dir, dataDir: dir}
	seen := false
	for _, c := range app.discover() {
		if c.Path == valid {
			seen = true
		}
		if c.Path == invalid {
			t.Fatal("accepted invalid server-info")
		}
	}
	if !seen {
		t.Fatal("missed valid generated server-info")
	}
}

func TestPythonCandidatesPreserveManualPreference(t *testing.T) {
	cs := pythonCandidates("python -I")
	if len(cs) == 0 || cs[0] != "python -I" {
		t.Fatalf("%v", cs)
	}
}

func TestReachableServerDistinguishesStaleCredentialFile(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	file := filepath.Join(dir, "fluent-server-info.txt")
	if err := os.WriteFile(file, []byte(listener.Addr().String()+"\npassword\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if !serverReachable(file) {
		t.Fatal("expected local server reachable")
	}
	listener.Close()
	if serverReachable(file) {
		t.Fatal("accepted stale server credential file")
	}
}

func TestServerSelectionAvoidsGuessingAmongMultipleLiveSessions(t *testing.T) {
	listeners := make([]net.Listener, 2)
	files := make([]Candidate, 2)
	for i := range files {
		l, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		listeners[i] = l
		f := filepath.Join(t.TempDir(), fmt.Sprintf("fluent-server-info-%d.txt", i))
		if err := os.WriteFile(f, []byte(l.Addr().String()+"\nsecret\n"), 0600); err != nil {
			t.Fatal(err)
		}
		files[i] = Candidate{Path: f}
	}
	defer listeners[0].Close()
	defer listeners[1].Close()
	if target, ask := chooseServerInfo("", files); target != "" || !ask {
		t.Fatal("must require selection with two active Fluent sessions")
	}
	if target, ask := chooseServerInfo(files[1].Path, files); target != files[1].Path || ask {
		t.Fatal("must honor explicitly saved session")
	}
}

func TestAutoConnectOrchestrationUsesExistingSolverReadOnly(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("mock POSIX interpreter; Windows requires real Fluent acceptance")
	}
	dir := t.TempDir()
	fake := filepath.Join(dir, "mock-python.sh")
	script := `#!/bin/sh
if [ "$1" = "-c" ]; then
  printf '%s\n' '{"executable":"/mock/python","version":"3.11.0","compatible":true,"pyfluent":"mock"}'
else
  printf '%s\n' '{"ok":true,"data":{"schema":"autofluent.snapshot.v1","scan_readiness":"ready_to_review","fields":[],"boundaries":[],"fluid_zones":[]}}'
fi
`
	if err := os.WriteFile(fake, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	info := filepath.Join(dir, "fluent-server-info.txt")
	if err := os.WriteFile(info, []byte(listener.Addr().String()+"\nauth-secret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_ = os.MkdirAll(filepath.Join(dir, "worker"), 0755)
	if err := os.WriteFile(filepath.Join(dir, "worker", "af_worker.py"), []byte("# mock worker entry"), 0600); err != nil {
		t.Fatal(err)
	}
	app := &App{base: dir, dataDir: dir, python: fake, serverInfo: info}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	setup := app.autoSetup(ctx)
	if !setup.Ready || setup.Python.Command != fake || setup.Server != info {
		t.Fatalf("expected auto setup to retain the saved live session, got %+v", setup)
	}
	data, err := app.runWorker(ctx, "scan", "", nil, "")
	if err != nil {
		t.Fatal(err)
	}
	snap, ok := data["data"].(map[string]any)
	if !ok || snap["scan_readiness"] != "ready_to_review" {
		t.Fatalf("%v", data)
	}
}

func TestConnectionNextStepAvoidsUnnecessaryPythonReinstall(t *testing.T) {
	s := SetupResult{Python: PythonProbe{Executable: `C:\Python314\python.exe`, PyFluent: "0.43.0"}}
	msg := connectionNextStep(s, nil)
	if !strings.Contains(msg, "Workbench") || !strings.Contains(msg, "不要重装 Python") {
		t.Fatalf("wrong next step: %s", msg)
	}
	s.Server = "C:/Temp/fluent-server-info.txt"
	s.Ready = true
	msg = connectionNextStep(s, fmt.Errorf("rpc error"))
	if !strings.Contains(msg, "无需重新安装 Python") {
		t.Fatal(msg)
	}
}
