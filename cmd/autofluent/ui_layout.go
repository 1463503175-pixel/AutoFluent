package main

// UILayout is expressed in 96-DPI device-independent pixels. Keep all visible
// controls within the content column. A separate layout planner makes viewport
// regressions testable on Linux without pretending to render a Windows window.
// Navigation order: primary saved-solution analysis, then optional live controls.
var navPageOrder = [6]int{0, 3, 5, 1, 2, 4}
var navPageTitle = [6]string{"工作台", "连接工程", "标准工况", "保存解分析", "求解与任务", "模型对比"}

type uiRect struct{ X, Y, W, H int }

func uiMax(a, b int) int {
	if a > b {
		return a
	}
	return b
}
func uiMin(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func makeUILayout(dipWidth, dipHeight int) map[int]uiRect {
	left, right := 235, dipWidth-28
	width := uiMax(660, right-left)
	if dipHeight < 755 {
		dipHeight = 755
	} // native window is constrained to this height
	outputTop := uiMax(592, dipHeight-178)
	statusX := uiMax(680, dipWidth-310)
	statusW := uiMax(175, right-statusX)
	one := uiMin(290, uiMax(252, width*39/100))
	two := uiMin(220, uiMax(190, width*30/100))
	three := uiMax(150, right-(left+one+12+two+12))
	if three > 210 {
		three = 210
	}
	return map[int]uiRect{
		9001:            {235, 18, uiMin(405, statusX-253), 43},
		9002:            {235, 65, uiMin(600, statusX-252), 26},
		9003:            {statusX, 29, statusW, 25},
		9004:            {235, outputTop - 40, uiMax(350, uiMax(750, right-196)-253), 28},
		9005:            {235, outputTop, width, uiMax(86, dipHeight-outputTop-47)},
		9006:            {235, dipHeight - 29, width, 24},
		9007:            {uiMax(750, right-196), outputTop - 40, 195, 26},
		idStop:          {27, dipHeight - 104, 160, 34},
		9010:            {26, 516, 170, 27},
		9011:            {27, 548, 160, 17},
		9012:            {26, 577, 169, 65},
		2008:            {235, 493, width, 28},
		idHomeH5:        {235, 424, 296, 42},
		idScanWork:      {545, 424, 232, 42},
		idWBPortInput:   {235, 230, 200, 37},
		idAutoConnect:   {452, 228, 195, 41},
		idCopyServerCmd: {662, 228, 166, 41},
		2115:            {235, 286, width, 58},
		idWBOpenDiag:    {235, 375, 252, 38},
		idRepairPython:  {502, 375, 215, 38},
		2113:            {235, 436, width, 25},
		idSelectServer:  {235, 466, width, 104},
		2209:            {235, 221, width, 36},
		2210:            {235, 395, width, 29},
		2214:            {235, 483, width, 35},
		2303:            {235, 151, width, 30},
		idPickH5:        {235, 234, one, 42},
		idBatchH5:       {235 + one + 12, 234, two, 42},
		idOpenReport:    {235 + one + 12 + two + 12, 234, three, 42},
		2311:            {235, 297, width, 28},
		2312:            {496, 365, uiMax(185, right-496), 28},
		2305:            {235, 402, width, 33},
		2306:            {235, 485, width, 27},
		2307:            {235, 400, width, 27},
		2324:            {235, 428, width - 116, 31},
		2325:            {235, 466, width, 31},
		2326:            {235 + width - 105, 428, 105, 31},
		2414:            {235, 244, width, 30},
		2415:            {235, 285, width, 30},
		2403:            {235, 374, width, 151},
		idSolveWB:       {530, 183, 240, 42},
		idInspectSolve:  {780, 183, uiMax(128, right-780), 42},
		2502:            {235, 149, width, 28},
		2510:            {235, 227, width, 34},
		2511:            {235, 350, width, 34},
		idCompare:       {235, 441, 255, 40},
		2503:            {235, 494, width, 30},
	}
}
