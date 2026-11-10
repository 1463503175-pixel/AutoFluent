//go:build !windows

package main

import (
	"fmt"
	"os/exec"
)

func main() {
	fmt.Println("AutoFluent native UI is available on Windows x64. Build using GOOS=windows GOARCH=amd64.")
}

func configureChildProcess(cmd *exec.Cmd) {}
