package main

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
)

// TaskProgress is a worker-published atomic snapshot. Percent measures weighted
// completed stages, NOT estimated elapsed time or Fluent solver convergence.
type TaskProgress struct {
	Schema         string `json:"schema"`
	Percent        int    `json:"percent"`
	Stage          string `json:"stage"`
	Detail         string `json:"detail"`
	Meaning        string `json:"meaning"`
	CompletedUnits *int64 `json:"completed_units,omitempty"`
	TotalUnits     *int64 `json:"total_units,omitempty"`
}

func readTaskProgress(path string) (TaskProgress, error) {
	var p TaskProgress
	if strings.TrimSpace(path) == "" {
		return p, errors.New("no progress file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return p, err
	}
	if len(data) > 4096 {
		return p, errors.New("progress JSON too large")
	}
	if err = json.Unmarshal(data, &p); err != nil {
		return p, err
	}
	if p.Schema != "autofluent.task_progress.v1" || p.Meaning != "weighted_completed_stages_not_eta" || p.Percent < 0 || p.Percent > 100 ||
		len([]rune(p.Stage)) > 75 || len([]rune(p.Detail)) > 220 {
		return p, errors.New("invalid worker progress record")
	}
	if p.CompletedUnits != nil || p.TotalUnits != nil {
		if p.CompletedUnits == nil || p.TotalUnits == nil || *p.TotalUnits <= 0 || *p.CompletedUnits < 0 || *p.CompletedUnits > *p.TotalUnits {
			return p, errors.New("invalid measured progress units")
		}
	}
	return p, nil
}
