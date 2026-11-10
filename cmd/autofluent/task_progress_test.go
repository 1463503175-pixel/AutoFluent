package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestTaskProgressContract(t *testing.T) {
	p := filepath.Join(t.TempDir(), "progress.json")
	good := `{"schema":"autofluent.task_progress.v1","percent":45,"stage":"读取 H5","detail":"12/30 blocks","meaning":"weighted_completed_stages_not_eta","completed_units":12,"total_units":30}`
	if err := os.WriteFile(p, []byte(good), 0600); err != nil {
		t.Fatal(err)
	}
	s, err := readTaskProgress(p)
	if err != nil || s.Percent != 45 || *s.TotalUnits != 30 {
		t.Fatalf("bad valid progress: %+v %v", s, err)
	}
	for _, bad := range []string{
		`{"schema":"invalid","percent":55,"meaning":"weighted_completed_stages_not_eta"}`,
		`{"schema":"autofluent.task_progress.v1","percent":104,"meaning":"weighted_completed_stages_not_eta"}`,
		`{"schema":"autofluent.task_progress.v1","percent":55,"meaning":"weighted_completed_stages_not_eta","completed_units":5,"total_units":3}`,
	} {
		if err := os.WriteFile(p, []byte(bad), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readTaskProgress(p); err == nil {
			t.Fatal("accepted malformed progress", bad)
		}
	}
}
