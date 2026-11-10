package main

import "fmt"

// Short UI summary; full JSON is archived separately for provenance.
func summarizeScan(data map[string]any) string {
	if data == nil {
		return "没有返回 Fluent 扫描数据"
	}
	fields, _ := data["fields"].([]any)
	boundaries, _ := data["boundaries"].([]any)
	fluids, _ := data["fluid_zones"].([]any)
	nread := 0
	for _, f := range fields {
		if obj, ok := f.(map[string]any); ok && obj["state"] == "read" {
			nread++
		}
	}
	result := fmt.Sprintf("只读扫描：%s；已读取全局字段 %d/%d；边界 %d 个；流体域 %d 个。", data["scan_readiness"], nread, len(fields), len(boundaries), len(fluids))
	if draft, ok := data["draft_template"].(map[string]any); ok && draft["verified_with_real_case"] != true {
		result += "\r\n已生成工况草稿（未审核；不会自动发布）。"
	}
	if plan, ok := data["auto_preview"].(map[string]any); ok {
		result += fmt.Sprintf("\r\n标准模板自动差异预检：阻断 %v 项、需修改 %v 项。", plan["blocker_count"], plan["change_count"])
		if plan["ok"] == true {
			result += "可进入人工确认发布。"
		} else {
			result += "暂不允许发布，需核对差异。"
		}
	}
	return result
}
