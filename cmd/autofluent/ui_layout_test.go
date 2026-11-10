package main

import "testing"

func overlap(a, b uiRect) bool {
	return a.X < b.X+b.W && b.X < a.X+a.W && a.Y < b.Y+b.H && b.Y < a.Y+a.H
}
func TestUILayoutPrimaryActionsDoNotOverlap(t *testing.T) {
	for _, viewport := range [][2]int{{1030, 785}, {1200, 805}, {1440, 900}, {1030, 1000}} {
		m := makeUILayout(viewport[0], viewport[1])
		for _, id := range []int{9001, 9002, 9003, 9004, 9005, 9006, 2008, idHomeH5, idScanWork, idAutoConnect, idWBOpenDiag, idPickH5, idBatchH5, idOpenReport, idSolveWB, idInspectSolve, idCompare} {
			r := m[id]
			if r.X < 0 || r.Y < 0 || r.X+r.W > viewport[0] || r.Y+r.H > viewport[1] {
				t.Fatalf("viewport %v id %d out of bounds: %+v", viewport, id, r)
			}
		}
		for _, pair := range [][2]int{{2008, idHomeH5}, {2008, idScanWork}, {idHomeH5, idScanWork}, {idPickH5, idBatchH5}, {idBatchH5, idOpenReport}, {idPickH5, idOpenReport}, {9001, 9003}, {9002, 9003}, {9005, 9006}, {9004, 9005}, {2415, 2403}} {
			if overlap(m[pair[0]], m[pair[1]]) {
				t.Fatalf("viewport %v controls %d and %d overlap", viewport, pair[0], pair[1])
			}
		}
	}
}
func TestSidebarOfflineFirst(t *testing.T) {
	expected := [6]int{0, 3, 5, 1, 2, 4}
	if navPageOrder != expected {
		t.Fatalf("navigation order changed: %v", navPageOrder)
	}
	for _, page := range navPageOrder {
		if page < 0 || page >= len(navPageTitle) || navPageTitle[page] == "" {
			t.Fatalf("invalid nav page %d", page)
		}
	}
}
