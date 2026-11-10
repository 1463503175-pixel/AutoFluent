package main

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// decodeBridgeOutput reads the last complete JSON object. PyWorkbench may
// print gRPC security warnings to stderr before the worker's final JSON line.
// A warning is never accepted as an execution receipt; a valid JSON object is
// required. This is a transport fix, not evidence of Solver command success.
func decodeBridgeOutput(raw []byte, target any) error {
	all := bytes.TrimSpace(raw)
	if json.Valid(all) {
		if err := json.Unmarshal(all, target); err == nil {
			return nil
		}
	}
	lines := bytes.Split(all, []byte{'\n'})
	for i := len(lines) - 1; i >= 0; i-- {
		line := bytes.TrimSpace(lines[i])
		if len(line) == 0 || line[0] != '{' || !json.Valid(line) {
			continue
		}
		if err := json.Unmarshal(line, target); err == nil {
			return nil
		}
	}
	return fmt.Errorf("未收到有效的 JSON 操作回执（可能只有 Python 警告或错误输出）")
}
