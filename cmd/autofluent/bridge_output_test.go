package main

import "testing"

func TestBridgeOutputWarningBeforeReceipt(t *testing.T) {
	raw := []byte("C:\\Python314\\Lib\\site-packages\\ansys\\tools Warning: Starting gRPC client without TLS on 127.0.0.1:9935\n" + `{"status":"fluent_connected","port":9935,"security":"insecure"}` + "\n")
	var wb WorkbenchAttach
	if err := decodeBridgeOutput(raw, &wb); err != nil || wb.Status != "fluent_connected" || wb.Port != 9935 {
		t.Fatalf("valid WB receipt rejected: %v %+v", err, wb)
	}
}
func TestBridgeOutputNoReceipt(t *testing.T) {
	for _, s := range []string{"Starting gRPC client without TLS", `{"status":`, ""} {
		var wb WorkbenchAttach
		if err := decodeBridgeOutput([]byte(s), &wb); err == nil {
			t.Fatalf("accepted non-JSON: %s", s)
		}
	}
}
func TestBridgeOutputReportMap(t *testing.T) {
	var m map[string]any
	raw := []byte("gRPC security warning\n{\"status\":\"report_captured\",\"file\":\"x.trn\"}\n")
	if err := decodeBridgeOutput(raw, &m); err != nil || m["status"] != "report_captured" {
		t.Fatalf("report receipt: %v %v", err, m)
	}
}
