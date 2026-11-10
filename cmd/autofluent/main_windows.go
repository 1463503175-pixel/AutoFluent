//go:build windows

// AutoFluent V2.9 native Win32 interface: explicit Workbench port attach. No HTTP server, browser or WebView.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	wsOverlapped       = 0x00CF0000
	wsVisible          = 0x10000000
	wsChild            = 0x40000000
	wsVScroll          = 0x00200000
	wsTabStop          = 0x00010000
	wsBorder           = 0x00800000
	wsClipChildren     = 0x02000000
	wsExClientEdge     = 0x00000200
	esAutoHScroll      = 0x0080
	esMultiLine        = 0x0004
	esAutoVScroll      = 0x0040
	esReadOnly         = 0x0800
	esWantReturn       = 0x1000
	bsPushButton       = 0x00000000
	bsOwnerDraw        = 0x0000000B
	odsDisabled        = 0x0004
	dtCenter           = 0x0001
	dtVCenter          = 0x0004
	dtSingleLine       = 0x0020
	dtEndEllipsis      = 0x8000
	enChange           = 0x0300
	ssLeft             = 0x00000000
	wmDestroy          = 0x0002
	wmClose            = 0x0010
	wmSize             = 0x0005
	wmCommand          = 0x0111
	wmPaint            = 0x000F
	wmDrawItem         = 0x002B
	wmEraseBkgnd       = 0x0014
	wmGetMinMaxInfo    = 0x0024
	wmSetFont          = 0x0030
	wmSetText          = 0x000C
	wmCtlColorStatic   = 0x0138
	opaqueBkMode       = 2
	wmCtlColorEdit     = 0x0133
	wmCtlColorBtn      = 0x0135
	wmSetIcon          = 0x0080
	stmSetIcon         = 0x0170
	ssIcon             = 0x0003
	wmDpiChanged       = 0x02E0
	wmTimer            = 0x0113
	wmAppDone          = 0x8001
	wmAppStartAuto     = 0x8002
	pbmSetPos          = 0x0402
	emLimitText        = 0x00C5
	emSetSel           = 0x00B1
	emScrollCaret      = 0x00B7
	swShow             = 5
	swHide             = 0
	mbYesNo            = 0x4
	mbIconWarning      = 0x30
	mbIconInfo         = 0x40
	mbIconError        = 0x10
	idYes              = 6
	cbGetCurSel        = 0x0147
	cbAddString        = 0x0143
	cbResetContent     = 0x014B
	cbSetCurSel        = 0x014E
	cbsDropDownList    = 0x0003
	ofnFileMustExist   = 0x1000
	ofnPathMustExist   = 0x0800
	ofnExplorer        = 0x80000
	imageIcon          = 1
	lrLoadFromFile     = 0x0010
	fontNormal         = 0
	fontStrong         = 1
	fontSmall          = 2
	fontTitle          = 3
	idNavBase          = 1000
	idSaveConnect      = 2100
	idDiscover         = 2101
	idSelftest         = 2102
	idScan             = 2103
	idPickServer       = 2104
	idPickPython       = 2105
	idExtract          = 2200
	idPreview          = 2201
	idPublish          = 2202
	idSaveTemplate     = 2203
	idPickTemplate     = 2204
	idLoadTemplate     = 2205
	idWBExport         = 2216
	idWBReport         = 2217
	idPickFlux         = 2300
	idPickH5Config     = 2317
	idGeometryPrecheck = 2318
	idCommonCut        = 2518
	idMetrics          = 2301
	idToggleLegacy     = 2316
	idScanLibrary      = 2308
	idRefreshHistory   = 2400
	idSolveIterations  = 2412
	idPickCompareA     = 2505
	idPickCompareB     = 2506
	idToggleAdvanced   = 2123
	idWBPortLabel      = 2130
)

var (
	user32              = syscall.NewLazyDLL("user32.dll")
	shell32             = syscall.NewLazyDLL("shell32.dll")
	pShellExecute       = shell32.NewProc("ShellExecuteW")
	gdi32               = syscall.NewLazyDLL("gdi32.dll")
	kernel32            = syscall.NewLazyDLL("kernel32.dll")
	ntdll               = syscall.NewLazyDLL("ntdll.dll")
	comdlg32            = syscall.NewLazyDLL("comdlg32.dll")
	pRegisterClass      = user32.NewProc("RegisterClassExW")
	pCreateWindow       = user32.NewProc("CreateWindowExW")
	pDefWnd             = user32.NewProc("DefWindowProcW")
	pShowWindow         = user32.NewProc("ShowWindow")
	pUpdateWindow       = user32.NewProc("UpdateWindow")
	pGetMessage         = user32.NewProc("GetMessageW")
	pIsDialogMessage    = user32.NewProc("IsDialogMessageW")
	pTranslate          = user32.NewProc("TranslateMessage")
	pDispatch           = user32.NewProc("DispatchMessageW")
	pPostQuit           = user32.NewProc("PostQuitMessage")
	pSetTimer           = user32.NewProc("SetTimer")
	pKillTimer          = user32.NewProc("KillTimer")
	pPostMessage        = user32.NewProc("PostMessageW")
	pSendMessage        = user32.NewProc("SendMessageW")
	pSetWindowText      = user32.NewProc("SetWindowTextW")
	pGetWindowText      = user32.NewProc("GetWindowTextW")
	pGetTextLength      = user32.NewProc("GetWindowTextLengthW")
	pMoveWindow         = user32.NewProc("MoveWindow")
	pEnableWindow       = user32.NewProc("EnableWindow")
	pMessageBox         = user32.NewProc("MessageBoxW")
	pGetClientRect      = user32.NewProc("GetClientRect")
	pInvalidateRect     = user32.NewProc("InvalidateRect")
	pBeginPaint         = user32.NewProc("BeginPaint")
	pEndPaint           = user32.NewProc("EndPaint")
	pFillRect           = user32.NewProc("FillRect")
	pSetBkColor         = gdi32.NewProc("SetBkColor")
	pSetTextColor       = gdi32.NewProc("SetTextColor")
	pSetBkMode          = gdi32.NewProc("SetBkMode")
	pCreateBrush        = gdi32.NewProc("CreateSolidBrush")
	pCreateFont         = gdi32.NewProc("CreateFontW")
	pDeleteObject       = gdi32.NewProc("DeleteObject")
	pSelectObject       = gdi32.NewProc("SelectObject")
	pCreatePen          = gdi32.NewProc("CreatePen")
	pRoundRect          = gdi32.NewProc("RoundRect")
	pDrawText           = user32.NewProc("DrawTextW")
	pGetStockObject     = gdi32.NewProc("GetStockObject")
	pLoadCursor         = user32.NewProc("LoadCursorW")
	pLoadImage          = user32.NewProc("LoadImageW")
	pGetModuleHandle    = kernel32.NewProc("GetModuleHandleW")
	pRtlMoveMemory      = ntdll.NewProc("RtlMoveMemory")
	pGetOpenFile        = comdlg32.NewProc("GetOpenFileNameW")
	pSetProcessDpi      = user32.NewProc("SetProcessDPIAware")
	pInitCommonControls = syscall.NewLazyDLL("comctl32.dll").NewProc("InitCommonControls")
	pGetDpi             = user32.NewProc("GetDpiForWindow")
	pGetDpiSystem       = user32.NewProc("GetDpiForSystem")
	pGetSystemMetric    = user32.NewProc("GetSystemMetrics")
	pOpenClipboard      = user32.NewProc("OpenClipboard")
	pCloseClipboard     = user32.NewProc("CloseClipboard")
	pEmptyClipboard     = user32.NewProc("EmptyClipboard")
	pSetClipboard       = user32.NewProc("SetClipboardData")
	pGlobalAlloc        = kernel32.NewProc("GlobalAlloc")
	pGlobalLock         = kernel32.NewProc("GlobalLock")
	pGlobalUnlock       = kernel32.NewProc("GlobalUnlock")
	pGlobalFree         = kernel32.NewProc("GlobalFree")
)

type point struct{ X, Y int32 }
type rect struct{ Left, Top, Right, Bottom int32 }
type msg struct {
	Hwnd           uintptr
	Message        uint32
	WParam, LParam uintptr
	Time           uint32
	Pt             point
	Private        uint32
}
type windowClass struct {
	Size, Style                        uint32
	WndProc                            uintptr
	ClsExtra, WndExtra                 int32
	Instance, Icon, Cursor, Background uintptr
	MenuName, ClassName                *uint16
	SmallIcon                          uintptr
}
type paintStruct struct {
	DC        uintptr
	Erase     int32
	Rc        rect
	Restore   int32
	IncUpdate int32
	Reserved  [32]byte
}
type minMax struct{ Reserved, MaxSize, MaxPosition, MinTrack, MaxTrack point }
type drawItem struct {
	CtlType, CtlID, ItemID, ItemAction, ItemState uint32
	HwndItem, DC                                  uintptr
	RcItem                                        rect
	ItemData                                      uintptr
}
type openFileName struct {
	Size                         uint32
	Owner, Instance              uintptr
	Filter, CustomFilter         *uint16
	MaxCustFilter, FilterIndex   uint32
	File                         *uint16
	MaxFile                      uint32
	FileTitle                    *uint16
	MaxFileTitle                 uint32
	InitialDir, Title            *uint16
	Flags                        uint32
	FileOffset, FileExtension    uint16
	DefExt                       *uint16
	CustData, Hook, TemplateName uintptr
	Reserved                     uintptr
	ReservedDword, FlagsEx       uint32
}
type control struct {
	id                 int
	hwnd               uintptr
	page               int
	x, y, w, h         int
	stretchW, stretchH bool
	enabled            bool
}
type completion struct {
	workbench *WorkbenchAttach
	setup     *SetupResult
	action    string
	result    map[string]any
	err       error
	message   string
}
type UI struct {
	app                                                                                             *App
	hwnd                                                                                            uintptr
	page                                                                                            int
	width, height, dpi                                                                              int
	font                                                                                            [4]uintptr
	brushBg, brushSide, brushWhite                                                                  uintptr
	controls                                                                                        map[int]*control
	nav                                                                                             [6]uintptr
	status, header, subheader, logTitle, output, footer, busyText                                   uintptr
	pythonInput, serverInput, serverList, templateInput, templatePathInput, fluxInput, confirmInput uintptr
	h5Selected                                                                                      string
	lastReportHTML, lastAnalysisJSON                                                                string
	reportDirectory, reportSavedPath                                                                uintptr
	serverCandidates                                                                                []Candidate
	jobCancel                                                                                       context.CancelFunc
	jobAction                                                                                       string
	done                                                                                            chan completion
	currentScan                                                                                     map[string]any
	closed                                                                                          bool
	previewSig, previewIdentity                                                                     string
	previewAt                                                                                       time.Time
	jobPreview                                                                                      string
	compareAInput, compareBInput                                                                    uintptr
	scanCompleted, published                                                                        bool
	flowHint                                                                                        uintptr
	showAdvanced, showLegacy                                                                        bool
	needsRepair                                                                                     bool
	connectionHint                                                                                  uintptr
	wbSelectionRequired                                                                             bool
	wbContext                                                                                       WorkbenchAttach
	wbPortInput                                                                                     uintptr
	solveIterationsInput                                                                            uintptr
	wbSetPlanPath, wbSetSourcePath                                                                  string
	wbSetPlanAt                                                                                     time.Time
	logBuffer                                                                                       []string
	progressPath                                                                                    string
	progressPercent                                                                                 int
	progressStage                                                                                   string
}

var ui *UI

func ptr(s string) *uint16        { return syscall.StringToUTF16Ptr(s) }
func color(r, g, b uint8) uintptr { return uintptr(uint32(r) | uint32(g)<<8 | uint32(b)<<16) }
func winMsg(hwnd uintptr, m uint32, w, l uintptr) uintptr {
	v, _, _ := pSendMessage.Call(hwnd, uintptr(m), w, l)
	return v
}
func setText(hwnd uintptr, s string) {
	if hwnd != 0 {
		pSetWindowText.Call(hwnd, uintptr(unsafe.Pointer(ptr(s))))
	}
}

func chooseReportDirectory(owner uintptr, current string) string {
	ole := syscall.NewLazyDLL("ole32.dll")
	hr, _, _ := ole.NewProc("CoInitializeEx").Call(0, 2) // STA for the Explorer-style folder picker.
	flags := uint32(1)                                   // Only file-system folders.
	if int32(hr) >= 0 {
		defer ole.NewProc("CoUninitialize").Call()
		flags |= 0x40
	}
	var display, path [260]uint16
	title := ptr("选择 HTML 报告保存文件夹")
	initial := ptr(current)
	info := struct {
		owner, root     uintptr
		display, title  *uint16
		flags           uint32
		callback, param uintptr
		image           int32
	}{owner: owner, display: &display[0], title: title, flags: flags, param: uintptr(unsafe.Pointer(initial))}
	if current != "" {
		info.callback = reportFolderCallback
	}
	pidl, _, _ := shell32.NewProc("SHBrowseForFolderW").Call(uintptr(unsafe.Pointer(&info)))
	runtime.KeepAlive(initial)
	if pidl == 0 {
		return ""
	}
	defer ole.NewProc("CoTaskMemFree").Call(pidl)
	ok, _, _ := shell32.NewProc("SHGetPathFromIDListW").Call(pidl, uintptr(unsafe.Pointer(&path[0])))
	if ok == 0 {
		return ""
	}
	return syscall.UTF16ToString(path[:])
}

var reportFolderCallback = syscall.NewCallback(func(hwnd, message, _, initial uintptr) uintptr {
	if message == 1 {
		winMsg(hwnd, 0x467, 1, initial) // BFFM_SETSELECTIONW.
	}
	return 0
})

func getText(hwnd uintptr) string {
	if hwnd == 0 {
		return ""
	}
	n, _, _ := pGetTextLength.Call(hwnd)
	if n > 2000000 {
		n = 2000000
	}
	s := make([]uint16, n+1)
	pGetWindowText.Call(hwnd, uintptr(unsafe.Pointer(&s[0])), n+1)
	return syscall.UTF16ToString(s)
}
func show(hwnd uintptr, visible bool) {
	if hwnd != 0 {
		if visible {
			pShowWindow.Call(hwnd, swShow)
		} else {
			pShowWindow.Call(hwnd, swHide)
		}
	}
}
func enable(hwnd uintptr, on bool) {
	if hwnd != 0 {
		var x uintptr
		if on {
			x = 1
		}
		pEnableWindow.Call(hwnd, x)
	}
}
func messageBox(title, body string, flags uintptr) int {
	v, _, _ := pMessageBox.Call(ui.hwnd, uintptr(unsafe.Pointer(ptr(body))), uintptr(unsafe.Pointer(ptr(title))), flags)
	return int(v)
}
func (u *UI) scale(n int) int { return n * u.dpi / 96 }
func (u *UI) create(cls, name string, id int, style, exStyle uintptr) uintptr {
	h, _, _ := pCreateWindow.Call(exStyle, uintptr(unsafe.Pointer(ptr(cls))), uintptr(unsafe.Pointer(ptr(name))), style,
		0, 0, 100, 26, u.hwnd, uintptr(id), 0, 0)
	if h == 0 {
		panic(fmt.Sprintf("创建 Windows 控件失败 %s %d", cls, id))
	}
	return h
}
func (u *UI) add(page, id int, cls, text string, x, y, w, h int, style, exStyle uintptr, font int) *control {
	c := &control{id: id, page: page, x: x, y: y, w: w, h: h, enabled: true, stretchW: w < 0, stretchH: h < 0}
	c.hwnd = u.create(cls, text, id, style|wsChild|wsVisible, exStyle)
	u.controls[id] = c
	if font >= 0 && font < len(u.font) {
		winMsg(c.hwnd, wmSetFont, u.font[font], 1)
	}
	if cls == "EDIT" {
		winMsg(c.hwnd, emLimitText, 2000000, 0)
	}
	return c
}
func (u *UI) label(page, id int, text string, x, y, w, h int, font int) uintptr {
	return u.add(page, id, "STATIC", text, x, y, w, h, ssLeft, 0, font).hwnd
}
func (u *UI) button(page, id int, text string, x, y, w, h int) uintptr {
	return u.add(page, id, "BUTTON", text, x, y, w, h, bsOwnerDraw|wsTabStop, 0, fontStrong).hwnd
}
func (u *UI) edit(page, id int, text string, x, y, w, h int, multiline, readonly bool) uintptr {
	style := uintptr(esAutoHScroll | wsTabStop)
	if multiline {
		style = esMultiLine | esAutoVScroll | wsVScroll | esWantReturn | wsTabStop
	}
	if readonly {
		style |= esReadOnly
	}
	return u.add(page, id, "EDIT", text, x, y, w, h, style, wsExClientEdge, fontNormal).hwnd
}

// Read-only Win32 EDIT windows send WM_CTLCOLORSTATIC, not WM_CTLCOLOREDIT.
// These must paint with an opaque brush to avoid severe overlapping glyphs.
func (u *UI) isReadOnlyEdit(hwnd uintptr) bool {
	for _, c := range u.controls {
		if c != nil && c.hwnd == hwnd && (c.id == 2403 || c.id == 9005) {
			return true
		}
	}
	return false
}
func (u *UI) buildControls() {
	// Global header and footer -- native STATIC and EDIT controls.
	u.add(-1, 9008, "STATIC", "", 70, 18, 70, 70, ssIcon, 0, -1)
	u.label(-1, 9009, "AutoFluent", 46, 103, 162, 29, fontStrong)
	u.header = u.label(-1, 9001, "AutoFluent", 233, 18, 400, 39, fontTitle)
	u.subheader = u.label(-1, 9002, "ALD 仿真工况标准化与模型分析   ·   Windows 原生版", 235, 59, -1, 24, fontSmall)
	u.status = u.label(-1, 9003, "未连接 Fluent", 835, 26, 265, 22, fontStrong)
	u.logTitle = u.label(-1, 9004, "最近操作 · 详细记录在任务日志中", 233, 0, 450, 26, fontStrong)
	u.output = u.edit(-1, 9005, "选择 Fluent 保存的 .cas.h5 文件开始离线分析，无需连接 Workbench。", 233, 0, -1, -1, true, true)
	u.footer = u.label(-1, 9006, "只读操作不修改 Case   ·   工况发布/求解需人工确认   ·   绝不自动初始化", 235, 0, -1, 22, fontSmall)
	u.busyText = u.label(-1, 9007, "就绪", 835, 0, 220, 22, fontSmall)
	u.button(-1, idStop, "取消只读任务", 0, 0, 145, 30)
	u.label(-1, 9010, "全局任务进度 · 0%", 26, 528, 171, 27, fontStrong)
	u.add(-1, 9011, "msctls_progress32", "", 26, 559, 162, 16, 0, 0, -1)
	u.label(-1, 9012, "就绪 · 无正在执行的任务", 26, 584, 169, 49, fontSmall)

	for i, page := range navPageOrder {
		u.nav[i] = u.button(-1, idNavBase+i, navPageTitle[page], 24, 154+i*54, 164, 42)
	}
	// 0 Workbench
	u.label(0, 2000, "工作台", 235, 108, 500, 35, fontTitle)
	u.label(0, 2001, "分析已保存的 Fluent 结果，无需打开 Fluent 或连接 Workbench。", 235, 151, -1, 28, fontNormal)
	u.label(0, 2002, "当前版本", 235, 205, 250, 26, fontStrong)
	u.label(0, 2003, "V"+strings.SplitN(Version, "-", 2)[0]+"  H5 原生通量 · 外边界审计与截面核验", 235, 236, -1, 28, fontNormal)
	u.label(0, 2004, "安全策略", 235, 295, 250, 26, fontStrong)
	u.label(0, 2005, "不会启动浏览器、创建新 Fluent 会话、初始化、迭代或覆盖 Case/Data。", 235, 326, -1, 26, fontNormal)
	u.label(0, 2006, "分析结果标注可信等级；几何识别未经核验时，不用于正式模型排名。", 235, 356, -1, 24, fontSmall)
	u.flowHint = u.label(0, 2008, "离线分析：待选择保存解", 235, 495, -1, 26, fontStrong)
	u.button(0, idHomeH5, "选择 Case / Data 开始分析", 235, 424, 296, 42)
	u.button(0, idScanWork, "连接现有 Workbench", 545, 424, 232, 42)
	// 1 Connection: explicit WB port only. No auto-discovery on startup.
	u.label(1, 2107, "连接工程", 235, 108, 550, 34, fontTitle)
	u.label(1, 2108, "输入 Workbench 端口后连接；程序随即只读识别当前 Fluent，无需重复点扫描。", 235, 149, -1, 25, fontNormal)
	u.label(1, idWBPortLabel, "Workbench 端口（手动输入）", 235, 198, 270, 25, fontStrong)
	u.wbPortInput = u.edit(1, idWBPortInput, u.app.savedWBPort(), 235, 229, 205, 36, false, false)
	winMsg(u.wbPortInput, emLimitText, 5, 0)
	u.button(1, idAutoConnect, "连接 Workbench", 462, 226, 197, 42)
	u.button(1, idCopyServerCmd, "端口在哪里？", 681, 226, 150, 42)
	u.button(1, idRepairPython, "配置 WB 连接依赖", 858, 345, 205, 36)
	u.connectionHint = u.label(1, 2115, "等待连接 Workbench；只有你明确点击后才检查 Fluent。", 235, 279, -1, 53, fontSmall)
	u.button(1, idToggleAdvanced, "显示技术配置（可选）", 235, 345, 230, 36)
	u.button(1, idWBOpenDiag, "打开完整连接诊断", 483, 345, 230, 36)
	u.label(1, 2109, "Python 环境（仅旧 PyFluent 备用连接使用）", 235, 399, -1, 24, fontStrong)
	u.pythonInput = u.edit(1, 2110, u.app.python, 235, 428, -1, 31, false, false)
	u.button(1, idPickPython, "手动选择 Python", 235, 465, 164, 31)
	u.button(1, idSelftest, "诊断 PyFluent", 411, 465, 144, 31)
	u.label(1, 2111, "Fluent Server 信息文件（仅备用）", 235, 506, -1, 24, fontStrong)
	u.serverInput = u.edit(1, 2112, u.app.serverInfo, 235, 531, -1, 31, false, false)
	u.button(1, idPickServer, "手动选文件", 235, 570, 120, 32)
	u.button(1, idDiscover, "重新查找", 369, 570, 110, 32)
	u.button(1, idSaveConnect, "保存高级配置", 493, 570, 144, 32)
	u.button(1, idScan, "重新扫描", 647, 570, 120, 32)
	u.label(1, 2113, "多 Fluent 系统时请选择目标", 235, 616, -1, 20, fontSmall)
	u.serverList = u.add(1, idSelectServer, "COMBOBOX", "", 235, 645, -1, 100, cbsDropDownList|wsVScroll, 0, fontNormal).hwnd
	// 2 Conditions
	u.label(2, 2206, "标准工况", 235, 108, 420, 34, fontTitle)
	u.label(2, 2207, "对比源模型与当前 Fluent 的设置，确认后发布；仅迁移 .set 中的用户修改项。", 235, 146, -1, 26, fontSmall)
	u.label(2, 2208, "① 选择基准工况文件（.set）", 235, 187, 600, 26, fontStrong)
	u.templatePathInput = u.edit(2, 2209, u.app.lastWBStandardSource(), 235, 222, -1, 34, false, false)
	u.button(2, idPickTemplate, "选择基准 .set", 235, 273, 171, 38)
	u.button(2, idLoadTemplate, "载入模板", 374, 250, 130, 32)
	u.button(2, idExtract, "提取模板(高级)", 514, 250, 174, 32)
	u.button(2, idWBExport, "导出当前 .set", 423, 273, 180, 38)
	u.button(2, idWBReport, "只读工况摘要", 618, 273, 180, 38)
	u.label(2, 2210, "仅迁移用户修改项；不同几何的边界名称、材料和默认值须独立核对。", 235, 395, -1, 28, fontSmall)
	u.templateInput = u.edit(2, 2211, u.app.templateJSON(), 235, 357, -1, 114, true, false)
	u.button(2, idSaveTemplate, "保存模板", 235, 478, 115, 32)
	u.button(2, idPreview, "② 审核源与当前设置", 235, 342, 241, 40)
	u.label(2, 2212, "发布确认：", 235, 434, 110, 28, fontSmall)
	u.confirmInput = u.edit(2, 2213, "", 355, 428, 175, 34, false, false)
	u.button(2, idPublish, "③ 发布并回读", 492, 342, 228, 40)
	u.label(2, 2214, "先连接当前 Fluent，并在源模型导出设置文件。", 235, 483, -1, 29, fontSmall)
	// 3 metrics
	u.label(3, 2302, "保存解分析", 235, 108, 480, 34, fontTitle)
	u.label(3, 2303, "离线模式不要求 WB 端口或打开 Fluent；自动配对保存的 Case/Data 文件。", 235, 149, -1, 28, fontSmall)
	u.label(3, 2304, "选择一个 .cas.h5 或 .dat.h5，自动配对并分析", 235, 192, -1, 27, fontStrong)
	u.button(3, idPickH5, "选择 H5 · 直接分析", 235, 234, 280, 42)
	u.button(3, idBatchH5, "浏览同目录保存解", 528, 234, 214, 42)
	u.button(3, idOpenReport, "查看汇总与明细", 755, 234, 180, 42)
	u.button(3, idPickH5Config, "配置辅助分析（可选）", 235, 291, 230, 35)
	u.button(3, idGeometryPrecheck, "模型几何预检", 479, 291, 225, 35)
	u.label(3, 2311, "格式检查不等于几何识别成功；可单独对 Case 做真实壁面预检。", 235, 332, -1, 25, fontSmall)
	u.button(3, idToggleLegacy, "导入历史报告（可选）", 235, 360, 240, 36)
	u.label(3, 2312, "正常分析只需 H5，无需 Codex 报告", 496, 366, -1, 26, fontSmall)
	u.fluxInput = u.edit(3, 2305, "", 235, 401, -1, 31, false, false)
	u.button(3, idPickFlux, "选择历史报告", 235, 443, 153, 34)
	u.button(3, idMetrics, "重新读取", 406, 443, 165, 34)
	u.button(3, idScanLibrary, "整理历史报告", 585, 443, 203, 34)
	u.label(3, 2306, "历史兼容不会替代当前保存解的原生计算。", 235, 484, -1, 25, fontSmall)
	u.label(3, 2307, "HTML 报告保存目录（可浏览或粘贴，留空使用默认目录）", 235, 400, -1, 25, fontSmall)
	u.reportDirectory = u.edit(3, 2324, filepath.Join(u.app.dataDir, "native_reports"), 235, 428, -1, 31, false, false)
	u.reportSavedPath = u.edit(3, 2325, "默认："+filepath.Join(u.app.dataDir, "native_reports"), 235, 466, -1, 31, true, false)
	u.button(3, 2326, "浏览…", 775, 428, 105, 31)
	// 4: explicit, bounded solve; never initialize or auto-repeat remote commands.
	u.label(4, 2401, "求解与任务", 235, 108, 480, 34, fontTitle)
	u.label(4, 2402, "仅控制当前已打开的 WB → Fluent。必须确认 Case 已设置并保存备份。", 235, 149, -1, 24, fontSmall)
	u.label(4, 2413, "单批迭代数（1～50）", 235, 188, 194, 28, fontStrong)
	u.solveIterationsInput = u.edit(4, idSolveIterations, "10", 435, 183, 91, 36, false, false)
	u.button(4, idSolveWB, "确认后执行并记录残差", 542, 183, 242, 38)
	u.button(4, idInspectSolve, "查看求解记录", 801, 183, 144, 38)
	u.label(4, 2414, "不自动初始化或重发命令；远端状态不确定时停止后续自动操作。", 235, 247, -1, 27, fontSmall)
	u.label(4, 2415, "先连接并验证当前 Fluent；残差不能单独证明质量守恒或收敛。", 235, 288, -1, 29, fontSmall)
	u.button(4, idRefreshHistory, "刷新任务记录", 235, 328, 150, 34)
	u.edit(4, 2403, "", 235, 375, -1, 149, true, true)
	// 5 compare
	u.label(5, 2501, "模型对比", 235, 108, 480, 34, fontTitle)
	u.label(5, 2502, "对比两次保存解分析结果；先核对工况与截面口径，避免误判优劣。", 235, 149, -1, 26, fontNormal)
	u.label(5, 2512, "模型 A · H5 分析 JSON（或历史报告）", 235, 193, -1, 24, fontStrong)
	u.compareAInput = u.edit(5, 2510, "", 235, 226, -1, 32, false, false)
	u.button(5, idPickCompareA, "① 选择模型 A", 235, 270, 155, 34)
	u.label(5, 2513, "模型 B · H5 分析 JSON（或历史报告）", 235, 315, -1, 24, fontStrong)
	u.compareBInput = u.edit(5, 2511, "", 235, 349, -1, 33, false, false)
	u.button(5, idPickCompareB, "② 选择模型 B", 235, 391, 155, 34)
	u.button(5, idCompare, "③ 核对口径并比较", 235, 441, 250, 38)
	u.button(5, idCommonCut, "共同截面重新计算 H5", 505, 441, 259, 38)
	u.label(5, 2503, "未核验几何、工况和收敛时，只显示候选差异，不做正式排名。", 235, 494, -1, 29, fontSmall)
	u.label(5, 2504, "", 235, 479, -1, 25, fontSmall)
	u.changePage(3)
	enable(u.controls[idStop].hwnd, false)
	u.syncActions()
}
func (u *UI) updateLayout() {
	if u.hwnd == 0 {
		return
	}
	var rc rect
	pGetClientRect.Call(u.hwnd, uintptr(unsafe.Pointer(&rc)))
	u.width, u.height = int(rc.Right), int(rc.Bottom)
	dipW, dipH := u.width*96/u.dpi, u.height*96/u.dpi
	layout := makeUILayout(dipW, dipH)
	for id, c := range u.controls {
		if c == nil || c.hwnd == 0 {
			continue
		}
		b := uiRect{c.x, c.y, c.w, c.h}
		if c.stretchW {
			b.W = dipW - c.x - 28
		}
		if c.stretchH {
			b.H = dipH - c.y - 35
		}
		if planned, ok := layout[id]; ok {
			b = planned
		}
		// Avoid rendering detached legacy controls beyond viewport even if
		// a developer re-enables their visibility in a future version.
		if b.X+b.W > dipW-12 {
			b.W = uiMax(70, dipW-b.X-12)
		}
		if b.W < 70 {
			b.W = 70
		}
		if b.H < 22 {
			b.H = 22
		}
		pMoveWindow.Call(c.hwnd, uintptr(u.scale(b.X)), uintptr(u.scale(b.Y)), uintptr(u.scale(b.W)), uintptr(u.scale(b.H)), 1)
	}
}

// Advanced connection controls are deliberately hidden unless requested.
// Nothing in the normal workflow should require selecting Python paths or server-info.
func (u *UI) connectionVisibility() {
	manual := []int{2109, 2110, idPickPython, idSelftest, 2111, 2112, idPickServer, idDiscover, idSaveConnect, idScan}
	for _, id := range manual {
		if c := u.controls[id]; c != nil {
			show(c.hwnd, false)
		}
	}
	for _, id := range []int{2113, idSelectServer} {
		if c := u.controls[id]; c != nil {
			show(c.hwnd, u.page == 1 && u.wbSelectionRequired)
		}
	}
	if c := u.controls[idRepairPython]; c != nil {
		show(c.hwnd, u.page == 1 && u.needsRepair)
	}
	if u.connectionHint != 0 {
		show(u.connectionHint, u.page == 1)
	}
	if c := u.controls[idToggleAdvanced]; c != nil {
		// Legacy PyFluent controls are kept in source for compatibility,
		// but intentionally hidden in the WB-port-only default flow.
		show(c.hwnd, false)
		if u.showAdvanced {
			setText(c.hwnd, "收起高级配置")
		} else {
			setText(c.hwnd, "查看高级诊断与配置")
		}
	}
	u.updateLayout()
}

// The offline workflow must never inherit a stale WB status banner. The
// banner describes this page, not the last unrelated action in the application.
func (u *UI) updatePageStatus() {
	if u.app.isBusy() {
		return
	}
	switch u.page {
	case 0, 3, 5:
		if isApprovedReportPath(u.app.dataDir, u.lastReportHTML) {
			setText(u.status, "保存解报告已生成")
		} else if u.h5Selected != "" {
			setText(u.status, "保存解已选择 · 结果待核验")
		} else {
			setText(u.status, "离线分析 · 无需连接 WB")
		}
	case 1:
		switch u.wbContext.Status {
		case "fluent_connected":
			setText(u.status, "WB / Fluent 已验证")
		case "workbench_connected", "no_fluent_system", "selection_required", "fluent_probe_failed":
			setText(u.status, "Workbench 已连接")
		case "workbench_recheck_required":
			setText(u.status, "WB 上次已连 · Fluent 待核验")
		default:
			setText(u.status, "Workbench 尚未连接")
		}
	case 2, 4:
		if u.wbContext.Status == "fluent_connected" {
			setText(u.status, "当前 Fluent 已验证")
		} else {
			setText(u.status, "需先连接当前 Fluent")
		}
	}
}

func (u *UI) changePage(page int) {
	u.page = page
	for _, c := range u.controls {
		if c.page >= 0 {
			show(c.hwnd, c.page == page)
		}
	}
	for i, h := range u.nav {
		name := navPageTitle[navPageOrder[i]]
		if navPageOrder[i] == page {
			setText(h, "●  "+name)
		} else {
			setText(h, name)
		}
	}
	u.connectionVisibility()
	u.legacyVisibility()
	u.standardVisibility()
	u.updatePageStatus()
	if page == 4 {
		u.refreshHistory()
	}

}

// The gRPC JSON prototype is intentionally not presented as the main WB
// standardization workflow. Its controls remain in source for compatibility.
func (u *UI) standardVisibility() {
	for _, id := range []int{idExtract, idLoadTemplate, idSaveTemplate, 2211} {
		if c := u.controls[id]; c != nil {
			show(c.hwnd, false)
		}
	}
}

func (u *UI) legacyVisibility() {
	for _, id := range []int{2307, 2324, 2325, 2326} {
		if c := u.controls[id]; c != nil {
			show(c.hwnd, u.page == 3 && !u.showLegacy)
		}
	}
	for _, id := range []int{2305, idPickFlux, idMetrics, idScanLibrary, 2306} {
		if c := u.controls[id]; c != nil {
			show(c.hwnd, u.page == 3 && u.showLegacy)
		}
	}
	if c := u.controls[idToggleLegacy]; c != nil {
		show(c.hwnd, u.page == 3)
		if u.showLegacy {
			setText(c.hwnd, "收起历史结果兼容")
		} else {
			setText(c.hwnd, "导入历史报告（可选）")
		}
	}
}

func (u *UI) refreshHistory() {
	items := u.app.histories()
	lines := []string{}
	for _, item := range items {
		lines = append(lines, fmt.Sprintf("[%s] %s  %s\r\n%s\r\n", item.At, item.Action, item.Status, item.Detail))
	}
	if len(lines) == 0 {
		lines = []string{"暂无操作记录"}
	}
	if c := u.controls[2403]; c != nil {
		setText(c.hwnd, normalizeWindowsEditText(strings.Join(lines, "\n")))
	}
}

// Keep diagnostic history visible in the current window; V1.6.1 silently
// replaced the previous error on every subsequent status update.
func (u *UI) log(message string) {
	line := time.Now().Format("15:04:05") + "  " + compactUILogMessage(message, 1300)
	if len(u.logBuffer) == 0 || u.logBuffer[len(u.logBuffer)-1] != line {
		u.logBuffer = append(u.logBuffer, line)
	}
	if len(u.logBuffer) > 18 {
		u.logBuffer = u.logBuffer[len(u.logBuffer)-18:]
	}
	combined := normalizeWindowsEditText(strings.Join(u.logBuffer, "\n\n"))
	for len([]rune(combined)) > 9500 && len(u.logBuffer) > 1 {
		u.logBuffer = u.logBuffer[1:]
		combined = normalizeWindowsEditText(strings.Join(u.logBuffer, "\n\n"))
	}
	setText(u.output, combined)
	winMsg(u.output, emSetSel, ^uintptr(0), ^uintptr(0))
	winMsg(u.output, emScrollCaret, 0, 0)
}
func (u *UI) invalidatePreview() {
	u.previewSig, u.previewIdentity = "", ""
	u.previewAt = time.Time{}
	u.published = false
	u.syncActions()
}
func (u *UI) syncActions() {
	wbPlanReady := u.wbContext.Status == "fluent_connected" && u.wbSetPlanPath != "" &&
		u.wbSetSourcePath == strings.TrimSpace(getText(u.templatePathInput)) &&
		time.Since(u.wbSetPlanAt) < 15*time.Minute && !u.app.isBusy()
	if c := u.controls[idPublish]; c != nil {
		enable(c.hwnd, wbPlanReady && strings.TrimSpace(getText(u.confirmInput)) == "发布工况")
	}
	// The existing gRPC Worker does not attach via PyWorkbench. A WB TUI
	// receipt is not a Fluent Case scan and must never unlock gRPC actions.
	hasGPRCScan := u.scanCompleted && validServerInfo(u.app.serverInfo)
	if c := u.controls[idExtract]; c != nil {
		enable(c.hwnd, hasGPRCScan && !u.app.isBusy())
	}
	if c := u.controls[idWBExport]; c != nil {
		enable(c.hwnd, u.wbContext.Status == "fluent_connected" && !u.app.isBusy())
	}
	if c := u.controls[idWBReport]; c != nil {
		enable(c.hwnd, u.wbContext.Status == "fluent_connected" && !u.app.isBusy())
	}
	if c := u.controls[idSolveWB]; c != nil {
		enable(c.hwnd, u.wbContext.Status == "fluent_connected" && !u.app.isBusy())
	}
	if c := u.controls[idPreview]; c != nil {
		enable(c.hwnd, u.wbContext.Status == "fluent_connected" && !u.app.isBusy())
	}
	if c := u.controls[2214]; c != nil {
		label := "发布权限：先导出源模型 .set，选择该文件，点击审核。"
		if wbPlanReady {
			label = "② 审核已完成。输入『发布工况』后，③ 发布按钮才会启用。"
		}
		setText(c.hwnd, label)
	}
	if c := u.controls[idOpenReport]; c != nil {
		enable(c.hwnd, !u.app.isBusy() && (isApprovedReportPath(u.app.dataDir, u.lastReportHTML) || isApprovedAnalysisJSON(u.app.dataDir, u.lastAnalysisJSON)))
		if !isApprovedReportPath(u.app.dataDir, u.lastReportHTML) && isApprovedAnalysisJSON(u.app.dataDir, u.lastAnalysisJSON) {
			setText(c.hwnd, "仅重试 HTML 导出")
		} else {
			setText(c.hwnd, "查看汇总与明细")
		}
	}
	if c := u.controls[idBatchH5]; c != nil {
		enable(c.hwnd, !u.app.isBusy() && u.h5Selected != "")
	}
	if c := u.controls[2311]; c != nil {
		if u.h5Selected == "" {
			setText(c.hwnd, "先选择一份 H5；分析完成后可查看报告，同目录清单也将可用。")
		} else if !isApprovedReportPath(u.app.dataDir, u.lastReportHTML) {
			setText(c.hwnd, "已选择保存解；等待原生数据分析完成，未核验的几何会标记为候选。")
		} else {
			setText(c.hwnd, "报告已生成；未独立验证的几何和收敛结论仍标记为候选。")
		}
	}
	if c := u.controls[idCompare]; c != nil {
		enable(c.hwnd, !u.app.isBusy() && strings.TrimSpace(getText(u.compareAInput)) != "" && strings.TrimSpace(getText(u.compareBInput)) != "")
	}
	if c := u.controls[2415]; c != nil {
		if u.wbContext.Status == "fluent_connected" {
			setText(c.hwnd, "已连接当前 Fluent；运行前请备份。残差下降不等于物理收敛。")
		} else {
			setText(c.hwnd, "按钮不可用：先在「连接工程」验证当前 Fluent。离线 H5 分析不受影响。")
		}
	}
	if u.flowHint != 0 {
		if u.h5Selected == "" {
			setText(u.flowHint, "分析流程：选择一份 Case/Data → 自动计算 → 查看报告 → 选择第二份进行对比")
		} else if isApprovedReportPath(u.app.dataDir, u.lastReportHTML) {
			setText(u.flowHint, "最近一次保存解已生成报告；可在「保存解分析」直接打开。")
		} else {
			setText(u.flowHint, "保存解已选择；查看下方任务状态，计算完成后自动生成报告。")
		}
	}

}
func (u *UI) showTaskProgress(percent int, stage, detail string) {
	if percent < 0 {
		percent = 0
	}
	if percent > 100 {
		percent = 100
	}
	u.progressPercent = percent
	u.progressStage = stage
	if ctl := u.controls[9010]; ctl != nil {
		setText(ctl.hwnd, fmt.Sprintf("任务进度 · %d%%", percent))
	}
	if ctl := u.controls[9011]; ctl != nil {
		winMsg(ctl.hwnd, pbmSetPos, uintptr(percent), 0)
	}
	if ctl := u.controls[9012]; ctl != nil {
		setText(ctl.hwnd, stage+"\r\n"+trim(detail, 47))
	}
}

func (u *UI) pollTaskProgress() {
	if u.jobAction == "" || u.progressPath == "" {
		return
	}
	p, err := readTaskProgress(u.progressPath)
	if err != nil {
		return
	}
	if p.Percent < u.progressPercent {
		return
	}
	message := p.Detail
	if p.CompletedUnits != nil && p.TotalUnits != nil {
		message = fmt.Sprintf("%d/%d · %s", *p.CompletedUnits, *p.TotalUnits, p.Detail)
	}
	u.showTaskProgress(p.Percent, p.Stage, message)
}

func (u *UI) setBusy(action string, on bool) {
	if on {
		u.jobAction = action
		u.progressPath = filepath.Join(u.app.dataDir, fmt.Sprintf("progress_%d_%d.json", os.Getpid(), time.Now().UnixNano()))
		u.showTaskProgress(4, action, "已提交，等待子任务处理（无剩余时间预测）")
		pSetTimer.Call(u.hwnd, 122, 350, 0)
		setText(u.busyText, "任务执行中 · "+action)
		setText(u.status, "运行中")
	}
	if !on {
		u.pollTaskProgress()
		pKillTimer.Call(u.hwnd, 122)
		u.jobAction = ""
		setText(u.busyText, "就绪")
	}
	for _, id := range []int{idSaveConnect, idDiscover, idSelftest, idScan, idScanWork, idHomeH5, idAutoConnect, idRepairPython, idCopyServerCmd, idWBOpenDiag, idPickPython, idPickServer, idSelectServer, idExtract, idPreview, idPublish, idSaveTemplate, idPickTemplate, idLoadTemplate, idPickH5, idPickH5Config, idGeometryPrecheck, idBatchH5, idToggleLegacy, idPickFlux, idMetrics, idScanLibrary, idOpenReport, idPickCompareA, idPickCompareB, idCompare, idCommonCut, idSolveWB, idSolveIterations, idWBExport, idWBReport} {
		if c := u.controls[id]; c != nil {
			enable(c.hwnd, !on)
		}
	}
	enable(u.controls[idStop].hwnd, on && action != "publish" && action != "wb-solve" && action != "wb-conditions-publish")
	u.syncActions()
}
func (u *UI) saveConnection() error {
	prevPython, prevServer, _, _ := u.app.snapshot()
	newPython, newServer := getText(u.pythonInput), getText(u.serverInput)
	if prevPython != strings.TrimSpace(newPython) || prevServer != strings.TrimSpace(newServer) {
		u.invalidatePreview()
		u.scanCompleted = false
	}
	if e := u.app.setConnection(newPython, newServer); e != nil {
		return e
	}
	return nil
}
func (u *UI) saveEditor() error {
	raw := strings.TrimSpace(getText(u.templateInput))
	if raw == "" {
		return errors.New("模板编辑区为空，请先提取或加载 JSON")
	}
	var tmpl map[string]any
	if e := json.Unmarshal([]byte(raw), &tmpl); e != nil {
		return fmt.Errorf("工况模板 JSON 格式错误：%w", e)
	}
	path, e := u.app.saveTemplate(tmpl)
	if e != nil {
		return e
	}
	setText(u.templatePathInput, path)
	return nil
}
func (u *UI) loadEditor() error {
	path := strings.TrimSpace(getText(u.templatePathInput))
	if path == "" {
		return errors.New("未指定模板文件路径")
	}
	u.app.mu.Lock()
	e := u.app.loadTemplatePath(path)
	u.app.mu.Unlock()
	if e != nil {
		return e
	}
	if e = u.app.saveConfig(); e != nil {
		return e
	}
	setText(u.templateInput, u.app.templateJSON())
	return nil
}

// Automatic connection performs all read-only setup in one task.
func (u *UI) startManualWBConnect() {
	value := strings.TrimSpace(getText(u.wbPortInput))
	port, e := strconv.Atoi(value)
	if e != nil || port < 1024 || port > 65535 {
		u.log("请输入有效的 Workbench 端口（1024～65535），例如 3247。未连接任何服务。")
		setText(u.status, "等待有效 WB 端口")
		u.changePage(1)
		return
	}
	if !u.app.tryStart() {
		return
	}
	u.invalidatePreview()
	u.scanCompleted = false
	u.wbSelectionRequired = false
	u.wbContext = WorkbenchAttach{}
	setText(u.controls[idAutoConnect].hwnd, "连接 Workbench")
	u.setBusy(fmt.Sprintf("连接 WB:%d", port), true)
	u.log(fmt.Sprintf("正在验证你指定的 Workbench 端口 %d；不会扫描其他端口或自动启动 Server。", port))
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		result, err := u.app.manualWorkbench(ctx, port)
		// Persist the full security-mode matrix, not only 350-char history.
		// The local TXT is available even if the app window is later closed.
		_, _ = u.app.saveWBDiagnostic(port, result, err)
		if err == nil && (result.Status == "workbench_connected") {
			_ = u.app.storeWBPort(port)
		}
		u.app.record("wb-manual-port", result.Status, fmt.Sprintf("port=%d; %s; %v; attempts: %s", port, result.Detail, err, strings.Join(result.Attempts, " || ")))
		select {
		case u.done <- completion{action: "wb-manual", workbench: &result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

// Checking Fluent is a separate, user-triggered task. Never implies WB attach failure.
func (u *UI) startWBFluentCheck() {
	current := u.wbContext
	if !verifiedWBTransport(current) {
		u.log("尚未验证 Workbench 连接，请先连接指定端口。")
		return
	}
	if !u.app.tryStart() {
		return
	}
	u.setBusy("检查当前 Fluent", true)
	u.log("Workbench 已连接；开始只读识别 Fluent 系统。未打开 Fluent 时不修改或启动求解器。")
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		result, err := u.app.probeFluentFromWorkbench(ctx, current)
		u.app.record("wb-fluent-check", result.Status, fmt.Sprintf("port=%d; %s; %v", current.Port, result.Detail, err))
		select {
		case u.done <- completion{action: "wb-fluent", workbench: &result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startRepairPython() {
	if u.app.isBusy() {
		return
	}
	if messageBox("配置独立 PyWorkbench 环境？", "AutoFluent 将复用电脑已经安装的 Python（包括 Python 3.14），在当前用户的 AutoFluent 配置目录建立隔离环境，并安装 ANSYS 官方 ansys-workbench-core 0.14.0。\n不会安装 Python 3.11，不修改系统 Python、Workbench 或 Fluent，不更改你输入的 WB 端口。需要网络连接。\n\n是否授权此一次性环境配置？", mbYesNo|mbIconWarning) != idYes {
		return
	}
	if !u.app.tryStart() {
		return
	}
	u.setBusy("安装 WB 连接依赖", true)
	u.log("正在检查本机 Python 并配置独立 PyWorkbench 运行环境；不会修改仿真。")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		path, e := u.app.installPyWorkbench(ctx)
		out := map[string]any{"python": path}
		status := "完成"
		if e != nil {
			status = "失败"
		}
		u.app.record("repair-pyworkbench", status, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "repair", result: out, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startTask(action string) {
	if !u.app.tryStart() {
		u.log("已有任务执行中；请勿重复提交。")
		return
	}
	{
		if e := u.saveConnection(); e != nil {
			u.app.finish()
			u.log(e.Error())
			return
		}
	}
	if action == "preview" || action == "publish" {
		if e := u.saveEditor(); e != nil {
			u.app.finish()
			u.log(e.Error())
			return
		}
	}
	if action == "publish" {
		if u.previewSig == "" || u.previewIdentity != u.app.previewIdentity() || time.Since(u.previewAt) > 15*time.Minute {
			u.app.finish()
			u.invalidatePreview()
			u.log("发布被阻断：预检不存在、超过15分钟或模板/连接已改变。请重新运行发布前差异检查。")
			return
		}
		_, _, _, tmpl := u.app.snapshot()
		if tmpl["verified_with_real_case"] != true {
			u.app.finish()
			u.log("安全阻断：模板未明确标记 verified_with_real_case=true，请核对原始 Case 后再批准发布。")
			return
		}
		if strings.TrimSpace(getText(u.confirmInput)) != "发布工况" {
			u.app.finish()
			u.log("发布需要在确认框准确输入：发布工况")
			return
		}
		if messageBox("发布到当前 Fluent？", "将修改现有 Fluent 会话中的工况设置，可能影响当前未保存状态。\n此操作不会初始化、迭代或保存 Case。\n\n已保存工程并确认边界映射正确吗？", mbYesNo|mbIconWarning) != idYes {
			u.app.finish()
			return
		}
	}
	u.jobPreview = ""
	if action == "publish" {
		u.jobPreview = u.previewSig
	}
	u.log("正在执行 " + action + " ...\r\n请勿在任务执行期间切换或关闭 Fluent Case。")
	u.setBusy(action, true)
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		out, e := u.app.runWorker(ctx, action, map[bool]string{true: "发布工况", false: ""}[action == "publish"], nil, u.jobPreview)
		if e == nil && action == "extract" {
			_, e = u.app.extractResult(out)
		}
		if e == nil && action == "scan" {
			if v, ok := out["data"].(map[string]any); ok {
				u.app.mu.Lock()
				u.app.lastScan = v
				u.app.mu.Unlock()
			}
		}
		status := "成功"
		details := "Worker 返回成功"
		if e != nil {
			status = "失败"
			details = e.Error()
		}
		u.app.record(action, status, details)
		select {
		case u.done <- completion{action: action, result: out, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

// Capture current Fluent's changed settings through the verified WB port.
// Unlike the old gRPC extraction this requires no PyFluent server-info.
func (u *UI) startWBSettingsExport() {
	current := u.wbContext
	if current.Status != "fluent_connected" {
		u.log("当前还没有验证 Workbench → Fluent。先连接 WB 端口并检查 Fluent。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务执行，禁止重复投递")
		return
	}
	u.setBusy("wb-settings", true)
	u.log("从当前 Fluent 导出原生设置文件；只写入 AutoFluent 本地目录，不覆盖 Case/Data，不初始化或迭代。")
	ctx, cancel := context.WithTimeout(context.Background(), 65*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		result, err := u.app.exportWBSettings(ctx, current)
		status := "settings_exported"
		if err != nil {
			status = "未确认"
		}
		u.app.record("wb-settings", status, fmt.Sprint(err))
		select {
		case u.done <- completion{action: "wb-settings", result: result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

// Capture the current Fluent summary by the verified manual WB port.
func (u *UI) startWBReport() {
	current := u.wbContext
	if current.Status != "fluent_connected" {
		u.log("先输入 WB 端口并连接当前已打开的 Fluent。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务执行，禁止重复发送 Fluent 命令")
		return
	}
	u.setBusy("wb-report", true)
	u.log("正在读取当前 Fluent 的物理模型、边界、材料与求解摘要；不初始化、不计算、不覆盖 Case/Data。")
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		result, err := u.app.captureWBReport(ctx, current)
		state := "report_captured"
		if err != nil {
			state = "未确认"
		}
		u.app.record("wb-report", state, fmt.Sprint(err))
		select {
		case u.done <- completion{action: "wb-report", result: result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

// Launch exactly one bounded batch only after explicit operator confirmation.
func (u *UI) startWBSolve() {
	current := u.wbContext
	if current.Status != "fluent_connected" {
		u.log("需先通过手动 WB 端口连接，并验证已打开的 Fluent 编辑器。")
		return
	}
	n, err := strconv.Atoi(strings.TrimSpace(getText(u.solveIterationsInput)))
	if err != nil || n < 1 || n > 50 {
		u.log("迭代数必须为 1～50 的整数；不会启动求解。")
		return
	}
	if messageBox("授权当前 Fluent 迭代", fmt.Sprintf("将向当前 Fluent 提交一次 /solve/iterate %d。\n\n请确认：工况、网格和初始解已经检查；重要 Case/Data 已自行备份。\n本操作不会初始化、覆盖文件、启动新的 Solver 或自动重复求解。\nWB 命令提交后即使窗口超时，也可能在 Fluent 继续运行。\n\n是否明确授权这 %d 步？", n, n), mbYesNo|mbIconWarning) != idYes {
		u.log("用户取消求解授权，没有向 Fluent 发送迭代。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务，禁止重复投递")
		return
	}
	u.setBusy("wb-solve", true)
	pSetTimer.Call(u.hwnd, 121, 2000, 0)
	u.log(fmt.Sprintf("【求解任务已授权】WB 端口 %d，当前 Fluent，单批最多 %d 次迭代；不执行初始化。", current.Port, n))
	ctx, cancel := context.WithCancel(context.Background())
	u.jobCancel = cancel
	go func() {
		defer cancel()
		result, e := u.app.solveWBOnce(ctx, current, n)
		status := "remote_execution_unconfirmed"
		if e == nil {
			status = "residuals_observed_not_convergence_certified"
		}
		u.app.record("wb-solve", status, fmt.Sprintf("iterations=%d; %v", n, e))
		select {
		case u.done <- completion{action: "wb-solve", result: result, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) inspectSolveStatus() {
	result, e := u.app.recentSolveStatus()
	if e != nil {
		u.log("【求解监控】" + e.Error())
		return
	}
	u.log("【求解监控 · 本机记录】\r\n" + latestSolverSummary(result))
}

func (u *UI) startWBConditionsPreview() {
	wb := u.wbContext
	source := strings.TrimSpace(getText(u.templatePathInput))
	if wb.Status != "fluent_connected" || source == "" {
		u.log("先连接当前 Fluent，并选择原模型导出的 .set 设置文件。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务，禁止重复投递")
		return
	}
	u.wbSetPlanPath = ""
	u.setBusy("wb-conditions-preview", true)
	u.log("正在生成目标 Fluent 设置快照并核对基准文件；不会发布或初始化。")
	ctx, cancel := context.WithTimeout(context.Background(), 95*time.Second)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		r, e := u.app.wbConditions(ctx, wb, "preview", source)
		status := "review_required"
		if e != nil {
			status = "preview_failed"
		}
		u.app.record("wb-conditions-preview", status, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "wb-conditions-preview", result: r, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startWBConditionsPublish() {
	if u.wbSetPlanPath == "" || u.wbSetSourcePath != strings.TrimSpace(getText(u.templatePathInput)) ||
		time.Since(u.wbSetPlanAt) > 15*time.Minute {
		u.log("发布预检已失效，需重新核对源设置与当前 Fluent。")
		return
	}
	if strings.TrimSpace(getText(u.confirmInput)) != "发布工况" {
		u.log("发布确认文字应为『发布工况』，当前未发送任何命令。")
		return
	}
	if messageBox("确认修改当前 Fluent 工况",
		"此操作将执行 /file/read-settings，可能改变当前 Fluent 的边界、材料和求解设置。\n\n请先确认源/目标几何区名匹配，并在 Fluent 中自行备份当前 Case/Data。\n修改后程序会重新导出 .set 逐字节回读核对。\n\n注意：.set 仅含用户修改的设置；默认参数、边界映射和完整工况仍须独立检查。\n\n确认继续？", mbYesNo|mbIconWarning) != idYes {
		return
	}
	if !u.app.tryStart() {
		u.log("任务正在运行，拒绝重复发布")
		return
	}
	wb := u.wbContext
	plan := u.wbSetPlanPath
	u.wbSetPlanPath = ""
	u.setBusy("wb-conditions-publish", true)
	u.log("已明确授权一次设置迁移；正在再次核验目标并发布回读。超时不自动重发。")
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	u.jobCancel = cancel
	go func() {
		defer cancel()
		r, e := u.app.wbConditions(ctx, wb, "publish", plan)
		status := "readback_unconfirmed"
		if e == nil {
			status = "modified_settings_readback_match"
		}
		u.app.record("wb-conditions-publish", status, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "wb-conditions-publish", result: r, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startCommonCutCompare(first, second, config string) {
	if !u.app.tryStart() {
		u.log("其他任务进行中，不能同时重复计算保存解")
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3600*time.Second)
	u.jobCancel = cancel
	u.setBusy("h5-common-compare", true)
	u.log("【真实共同截面复算】将依次读取两份保存解，核对真实晶圆分区；不复用旧报告百分比。")
	go func() {
		defer cancel()
		result, err := u.app.recomputeCommonCut(ctx, first, second, config, u.progressPath)
		status := "候选同口径结果已生成 · 仍须核验工况收敛"
		if err != nil {
			status = "统一截面复算未完成"
		}
		u.app.record("h5-common-compare", status, fmt.Sprint(err))
		select {
		case u.done <- completion{action: "h5-common-compare", result: result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startGeometryPrecheck(selected, configPath string) {
	if !u.app.tryStart() {
		u.log("已有任务执行中，无法同时执行几何预检")
		return
	}
	u.h5Selected = selected
	ctx, cancel := context.WithTimeout(context.Background(), 1200*time.Second)
	u.jobCancel = cancel
	u.setBusy("h5-geometry-precheck", true)
	u.log("【Case 几何预检】扫描实际壁面；不读取 Fluent 流量或压力；不影响上一次物理结果")
	go func() {
		defer cancel()
		result, err := u.app.geometryPrecheckH5(ctx, selected, configPath, u.progressPath)
		status := "几何预检候选已获得"
		if err != nil {
			status = "几何预检执行失败"
		} else if result["status"] == "blocked" {
			status = "几何预检存在歧义 · 需要配置或模型复核"
		}
		u.app.record("h5-geometry-precheck", status, fmt.Sprint(err))
		select {
		case u.done <- completion{action: "h5-geometry-precheck", result: result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) retrySavedReport() {
	if !isApprovedAnalysisJSON(u.app.dataDir, u.lastAnalysisJSON) {
		u.log("当前没有可用于重新导出的有效 JSON 结果")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务执行中")
		return
	}
	source := u.lastAnalysisJSON
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	u.jobCancel = cancel
	u.setBusy("h5-rerender", true)
	u.showTaskProgress(20, "仅重试 HTML 导出", "从已计算 JSON 生成报告，不重读大型 H5")
	go func() {
		defer cancel()
		res, err := u.app.rerenderNativeJSON(ctx, source)
		text := "成功"
		if err != nil {
			text = "失败"
		}
		u.app.record("h5-rerender", text, fmt.Sprint(err))
		select {
		case u.done <- completion{action: "h5-rerender", result: res, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startH5Inspection(selected string) {
	u.startH5InspectionWithConfig(selected, "")
}

func (u *UI) startH5InspectionWithConfig(selected, configPath string) {
	directory := strings.TrimSpace(getText(u.reportDirectory))
	if directory != "" && !filepath.IsAbs(directory) {
		u.log("报告保存目录需要是绝对路径。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有分析任务正在执行")
		return
	}
	u.h5Selected = selected
	u.lastReportHTML = "" // Do not present previous model's report while this H5 is being processed.
	u.lastAnalysisJSON = ""
	setText(u.reportSavedPath, "正在分析，完成后显示实际 HTML 路径。")
	ctx, cancel := context.WithTimeout(context.Background(), 1800*time.Second)
	u.jobCancel = cancel
	u.setBusy("h5-inspect", true)
	if configPath != "" {
		u.log("【离线 H5 · 显式几何配置】" + selected + "；配置：" + configPath + "；无自动/11.9 回退")
	} else {
		u.log("【离线 H5】直接分析 " + selected + "；不连接 WB，不启动 Fluent，不修改 Case/Data")
	}
	go func() {
		defer cancel()
		result, e := u.app.inspectSavedH5ConfigWithProgress(ctx, selected, configPath, u.progressPath)
		if e == nil {
			if report, ok := result["html_file"].(string); ok && report != "" {
				result["visible_report_path"] = report
				if directory != "" && !strings.EqualFold(filepath.Clean(directory), filepath.Join(u.app.dataDir, "native_reports")) {
					if exported, err := exportReportCopy(u.app.dataDir, report, directory); err == nil {
						result["visible_report_path"] = exported
					} else {
						result["custom_report_error"] = err.Error()
					}
				}
			}
		}
		status := "仅完成文件检查，未生成物理结果"
		if e != nil {
			status = "失败"
		} else if result["physical_analysis_status"] == "native_11_9_profile_computed" {
			status = "11.9 原生通量重算完成（仍须核验收敛）"
		} else if result["physical_analysis_status"] == "adaptive_native_candidate_computed" {
			status = "自适应物理候选完成（分区待验证）"
		} else if result["physical_analysis_status"] == "partial_physical_metrics" {
			status = "部分完成 · 晶圆几何待复核"
		}
		u.app.record("h5-inspect", status, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "h5-inspect", result: result, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

// The two batch imports require only saved files; they never contact WB or Fluent.
func (u *UI) startBatchLocal(action, path string) {
	if path == "" {
		u.log("请先选择一个 H5 或统计报告，或在报告路径栏输入现有目录。")
		return
	}
	if !u.app.tryStart() {
		u.log("已有任务正在执行")
		return
	}
	u.setBusy(action, true)
	u.log("【离线批量检查】仅读取现有文件，不操作 Workbench 或 Fluent：" + path)
	go func() {
		var result map[string]any
		var err error
		if action == "h5-batch" {
			result, err = u.app.scanSavedH5Directory(path)
		}
		if action == "report-library" {
			result, err = u.app.scanHistoricalReports(path)
		}
		status := "成功"
		if err != nil {
			status = "失败"
		}
		u.app.record(action, status, fmt.Sprint(err))
		select {
		case u.done <- completion{action: action, result: result, err: err}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}

func (u *UI) startMetrics() {
	if !u.app.tryStart() {
		return
	}
	path := strings.TrimSpace(getText(u.fluxInput))
	u.setBusy("metrics", true)
	u.log("正在读取历史兼容报告（非重新计算 H5）；只用于复核已有数据。")
	go func() {
		result, e := u.app.metrics(path)
		s := "成功"
		if e != nil {
			s = "失败"
		}
		u.app.record("metrics", s, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "metrics", result: result, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}
func (u *UI) startCompare() {
	if !u.app.tryStart() {
		u.log("已有任务执行中")
		return
	}
	a, b := strings.TrimSpace(getText(u.compareAInput)), strings.TrimSpace(getText(u.compareBInput))
	u.log("正在检查输入数据的来源声明、测量定义与工况模板一致性…")
	u.setBusy("compare", true)
	go func() {
		result, e := u.app.compareFiles(a, b)
		status := "完成"
		if e != nil {
			status = "失败"
		}
		u.app.record("compare", status, fmt.Sprint(e))
		select {
		case u.done <- completion{action: "compare", result: result, err: e}:
		default:
		}
		if u.hwnd != 0 {
			pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
		}
	}()
}
func (u *UI) onDone() {
	select {
	case c := <-u.done:
		u.app.finish()
		u.setBusy(c.action, false)
		if c.err != nil {
			u.showTaskProgress(u.progressPercent, "任务失败或未确认", trim(c.err.Error(), 70))
		} else if c.action == "h5-inspect" {
			physical, _ := c.result["physical_analysis_status"].(string)
			if c.result["report_save_status"] == "failed" {
				u.showTaskProgress(100, "计算完成，报告保存失败", "JSON 已保存，可仅重试报告导出")
			} else if physical == "native_11_9_profile_computed" || physical == "adaptive_native_candidate_computed" {
				u.showTaskProgress(100, "原生统计和新报告已输出", "流量已计算；几何、收敛及对比条件仍待独立核验")
			} else if physical == "partial_physical_metrics" {
				u.showTaskProgress(100, "部分物理结果已取得", "端口及可用的压力、挡板结果已保留")
			} else {
				u.showTaskProgress(100, "仅完成 H5 结构诊断", "物理结果未算出；请检查报告中的具体失败原因")
			}
		} else if c.action == "h5-rerender" {
			if c.err != nil {
				u.showTaskProgress(100, "报告重新导出失败", trim(c.err.Error(), 70))
			}
			if c.err == nil {
				u.showTaskProgress(100, "从 JSON 重新导出完成", "没有重新读取 Case/Data")
			}
		} else {
			u.showTaskProgress(100, "本次任务已结束", "已收到执行回执；物理指标与收敛状态须核对报告")
		}
		if u.progressPath != "" {
			_ = os.Remove(u.progressPath)
			u.progressPath = ""
		}
		if c.action == "wb-manual" || c.action == "wb-selected" || c.action == "wb-fluent" {
			wb := c.workbench
			u.scanCompleted = false // TUI transport receipt is not a full Fluent Case scan.
			u.invalidatePreview()
			u.needsRepair = false
			if wb != nil {
				oldWB := u.wbContext
				u.wbContext = mergeWBContext(oldWB, *wb, c.action, c.err)
				u.wbSelectionRequired = wb.Status == "selection_required" && c.err == nil
				if u.wbSelectionRequired {
					winMsg(u.serverList, cbResetContent, 0, 0)
					setText(u.controls[2113].hwnd, "选择当前 Fluent 系统（只需一次，自动验证）")
					for _, system := range wb.Systems {
						winMsg(u.serverList, cbAddString, 0, uintptr(unsafe.Pointer(ptr(system.Name+"  "+system.Label))))
					}
				}
				msg := fmt.Sprintf("Workbench 连接结果：%s\r\n你输入的端口：%d（验证方式：%s）\r\nPyWorkbench Python：%s\r\nFluent 系统数：%d\r\n", wb.Status, wb.Port, wb.Security, wb.Python, len(wb.Systems))
				if len(wb.AllSystems) > 0 {
					msg += fmt.Sprintf("Workbench 工程原始系统数：%d\r\n", len(wb.AllSystems))
					for _, system := range wb.AllSystems {
						msg += fmt.Sprintf("  %s | %s | Setup=%v Solution=%v Fluent候选=%v\r\n", system.Name, system.Label, system.HasSetup, system.HasSolution, system.Fluent)
					}
				}
				for _, system := range wb.Systems {
					msg += fmt.Sprintf("  Fluent候选 %s | %s\r\n", system.Name, system.Label)
				}
				if wb.Detail != "" {
					msg += "详情：" + wb.Detail + "\r\n"
				}
				if c.err != nil {
					msg += "错误：" + c.err.Error() + "\r\n"
				}
				if wb.ProjectFile != "" {
					msg += "Workbench 工程：" + wb.ProjectFile + "\r\n"
				}
				if len(wb.CaseDataCandidates) > 0 {
					msg += fmt.Sprintf("只读发现 Case/Data 候选 %d 个（未认定当前解）：\r\n", len(wb.CaseDataCandidates))
					for n, item := range wb.CaseDataCandidates {
						if n >= 6 {
							break
						}
						msg += "  " + item.Path + "\r\n"
					}
				}
				if wb.ProjectIdentityNote != "" {
					msg += "工程文件诊断：" + wb.ProjectIdentityNote + "\r\n"
				}
				if len(wb.Attempts) > 0 && c.err != nil {
					msg += "连接未验证；完整 Python/认证异常请点击「打开完整连接诊断」。\r\n"
				}
				if (c.action == "wb-fluent" || c.action == "wb-selected") && u.wbContext.Status == "workbench_recheck_required" {
					setText(u.status, "WB 上次握手成功 · Fluent 检查失败")
					setText(u.controls[idAutoConnect].hwnd, "重新核验 WB 并检查 Fluent")
					setText(u.connectionHint, "上一轮 WB 连接成功，但本次 Fluent 检查未获有效回执；不能据此判定 WB 已断线。点击按钮重新核验原端口。")
				} else if wb.Status == "workbench_connected" {
					setText(u.status, "Workbench 已连接 · 未检查 Fluent")
					setText(u.connectionHint, "WB 端口已验证；正在检查 Fluent。没打开 Fluent 也不影响 WB 连接。")
					setText(u.controls[idAutoConnect].hwnd, "检查当前 Fluent")
				} else if wb.Status == "workbench_connected_scan_failed" {
					setText(u.status, "Workbench 已连接 · 系统列表读取失败")
					setText(u.connectionHint, trim(wb.Detail, 135))
					setText(u.controls[idAutoConnect].hwnd, "重新检查 Fluent")
				} else if wb.Status == "fluent_connected" {
					u.wbSelectionRequired = false
					setText(u.status, "Workbench → Fluent 已连接 · TUI 只读验证")
					setText(u.controls[idAutoConnect].hwnd, "重新检查 Fluent")
					setText(u.connectionHint, "已通过你输入的 WB 端口验证 Fluent TUI。完整工况尚未回读，暂不解锁发布。")
					msg += "【已向现有 Fluent 发送只读报告命令】\r\n未初始化、未迭代、未保存 Case/Data。\r\n注意：此回执只证明 TUI 控制通道，不等于完整工况回读。"
				} else if wb.Status == "selection_required" {
					setText(u.status, "指定 WB 端口已连接 · 请选择 Fluent 系统")
					setText(u.controls[idAutoConnect].hwnd, "重新检查 Fluent")
					setText(u.connectionHint, "找到多个 Fluent 系统；当前不猜测目标，避免控制错误的模型。")
				} else if wb.Status == "fluent_probe_failed" {
					setText(u.status, "WB 已连接 · Fluent TUI 未验证")
					setText(u.controls[idAutoConnect].hwnd, "重新检查 Fluent")
					setText(u.connectionHint, "找到 Workbench，但当前 Fluent TUI 报告未获有效回执；请核对目标编辑器是否已打开。")
				} else if wb.Status == "no_fluent_system" {
					setText(u.status, "WB 已连接 · 未识别 Fluent 系统")
					setText(u.controls[idAutoConnect].hwnd, "重新检查 Fluent")
					setText(u.connectionHint, trim(wb.Detail, 150))
				} else if wb.Status == "missing_pyworkbench" {
					u.needsRepair = true
					setText(u.status, "缺少 PyWorkbench · WB 尚未验证")
					setText(u.connectionHint, "本机 Python 尚无法导入 PyWorkbench。可点击「配置 WB 连接依赖」，无需安装 Python 3.11。")
				} else {
					setText(u.status, "指定 WB 端口连接失败")
					reason := wb.Detail
					if reason == "" && c.err != nil {
						reason = c.err.Error()
					}
					setText(u.connectionHint, trim("WB 连接未通过："+reason+"。完整的各认证方式结果见下方操作记录。", 170))
				}
				// Keep full evidence on disk; do not dump massive Python/system
				// tracebacks into the tiny always-visible status log.
				if c.action == "wb-fluent" || c.action == "wb-selected" {
					_ = os.WriteFile(filepath.Join(u.app.dataDir, "wb_last_connection_diagnostic.txt"),
						[]byte("Fluent probe / Workbench system report\r\n"+msg+"\r\n"), 0600)
				}
				u.log(wbResultUIMessage(*wb, c.err))
			}
			u.connectionVisibility()
			u.refreshHistory()
			u.syncActions()
			// The user supplies the WB port exactly once. A successful
			// handshake proceeds to a read-only Fluent system check without
			// extra technical clicks. No server is started or other port probed.
			if c.action == "wb-manual" && wb != nil && wb.Status == "workbench_connected" && c.err == nil {
				u.startWBFluentCheck()
			}
			return
		}
		if c.action == "auto" {
			if c.setup != nil {
				setText(u.pythonInput, c.setup.Python.Executable)
				setText(u.serverInput, c.setup.Server)
				u.serverCandidates = c.setup.Servers
				winMsg(u.serverList, cbResetContent, 0, 0)
				for i, candidate := range u.serverCandidates {
					winMsg(u.serverList, cbAddString, 0, uintptr(unsafe.Pointer(ptr(candidate.Modified+"   "+candidate.Path))))
					if candidate.Path == c.setup.Server {
						winMsg(u.serverList, cbSetCurSel, uintptr(i), 0)
					}
				}
				// A detected Python without PyFluent is still shown for repair.
				if c.setup.Ready && c.err == nil {
					scan, _ := c.result["data"].(map[string]any)
					u.currentScan = scan
					u.scanCompleted = scan != nil && scan["scan_readiness"] == "ready_to_review"
					snapshotPath := ""
					if scan != nil {
						snapshotPath, _ = u.app.archiveScan(scan)
					}
					setText(u.templateInput, u.app.templateJSON())
					_, _, path, _ := u.app.snapshot()
					setText(u.templatePathInput, path)
					u.needsRepair = false
					setText(u.connectionHint, "已自动连接到 Fluent，扫描信息已归档。下一步：标准工况自动对照（需要确认才发布）。")
					if u.scanCompleted {
						setText(u.status, "已连接 · 工况只读扫描完成")
					} else {
						setText(u.status, "已连接 · 扫描字段不完整")
					}
					if plan, ok := scan["auto_preview"].(map[string]any); ok && plan["ok"] == true {
						if sig, ok := plan["plan_signature"].(string); ok && len(sig) == 64 {
							u.previewSig = sig
							u.previewIdentity = u.app.previewIdentity()
							u.previewAt = time.Now()
							// The UI summary is printed below; keep preview in memory only.
						}
					}
					version, _ := scan["fluent_version"].(string)
					u.log("【已与真实 Fluent 建立只读连接】\r\nFluent 版本: " + version + "\r\n服务凭据文件: " + c.setup.Server + "\r\nPython: " + c.setup.Python.Executable + "\r\nPyFluent: " + c.setup.Python.PyFluent + "\r\n" + summarizeScan(scan) + "\r\n只读快照：" + snapshotPath + "\r\n未修改 Case、未启动求解。")
				} else {
					if c.setup.Server == "" && c.setup.Python.PyFluent != "" {
						setText(u.status, "未连接 Fluent · 需在 Fluent 内启动 Server")
					} else if c.setup.Python.PyFluent == "" {
						setText(u.status, "未连接 · PyFluent 环境未就绪")
					} else {
						setText(u.status, "未连接 · 请检查当前 Fluent 会话")
					}
					u.needsRepair = c.setup.Python.Executable != "" && c.setup.Python.PyFluent == ""
					next := connectionNextStep(*c.setup, c.err)
					setText(u.connectionHint, next)
					message := "【自动连接诊断（未修改仿真）】\r\n下一步：" + next + "\r\n"
					if c.setup.Python.Error != "" {
						message += "Python/PyFluent 检查：" + c.setup.Python.Error + "\r\n"
					}
					if c.setup.Python.Version != "" {
						message += "Python 版本：" + c.setup.Python.Version + "\r\n"
					}
					message += fmt.Sprintf("Fluent Server 候选数：%d；已验证可达数：%d\r\n", len(c.setup.Servers), len(c.setup.Servers))
					if c.setup.Python.Executable != "" {
						message += "Python: " + c.setup.Python.Executable + "\r\n"
					}
					if c.err != nil {
						message += "连接失败：" + c.err.Error() + "\r\n"
					}
					message += strings.Join(c.setup.Hints, "\r\n")
					u.log(message)
				}
			}
			u.connectionVisibility()
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "wb-solve" {
			pKillTimer.Call(u.hwnd, 121)
			if c.err != nil {
				setText(u.status, "求解状态未知 · 不得重发")
				u.log("【WB 求解尚未核验】\r\n" + c.err.Error() + "\r\n" + pretty(c.result) + "\r\n请到 Fluent 核实远端是否仍在计算。")
			} else {
				setText(u.status, "Fluent 残差记录已取得 · 未认证收敛")
				u.log("【求解监控】\r\n" + pretty(c.result) + "\r\n残差记录不是收敛证明；请核查流量和压降稳定性。")
			}
			u.inspectSolveStatus()
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "wb-conditions-preview" {
			if c.err != nil {
				u.log("【设置差异审核未完成】\r\n" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				if p, ok := c.result["plan_file"].(string); ok {
					u.wbSetPlanPath = p
				}
				u.wbSetSourcePath = strings.TrimSpace(getText(u.templatePathInput))
				u.wbSetPlanAt = time.Now()
				setText(u.status, "基准 .set 差异已核对 · 待人工确认")
				u.log("【原生 .set 发布预检】\r\n" + pretty(c.result) + "\r\n注意：此审核无法独立证明默认设置及区名映射正确。")
			}
			u.syncActions()
			u.refreshHistory()
			return
		}
		if c.action == "wb-conditions-publish" {
			setText(u.confirmInput, "")
			if c.err != nil {
				setText(u.status, "发布或回读未完成 · 禁止重试")
				u.log("【工况回读未完成】\r\n" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				setText(u.status, "用户修改设置回读一致 · 非完整物理工况认证")
				u.log("【.set 回读匹配】\r\n" + pretty(c.result))
			}
			u.syncActions()
			u.refreshHistory()
			return
		}
		if c.action == "wb-report" {
			if c.err != nil {
				setText(u.status, "Fluent 原生报告采集未确认")
				u.log("【Fluent 报告未完成】\r\n" + c.err.Error() + "\r\n" + pretty(c.result) + "\r\n不得自动重投递。")
			} else {
				setText(u.status, "已采集当前 Fluent 原生汇总 · 待工况审核")
				u.log("【Fluent 原生汇总报告】\r\n" + pretty(c.result) + "\r\n这是当前 Fluent 的原始报告，不含逐通道质量通量；不能单独据此认证全部工况一致。")
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "h5-batch" || c.action == "report-library" {
			if c.err != nil {
				setText(u.status, "离线批量整理未完成")
				u.log("【批量整理失败】" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				// A batch inventory is not a Fluent analysis report.
				// Keep the last actual H5 analysis report unchanged.
				if c.action == "h5-batch" {
					setText(u.status, "H5 文件清单已生成 · 未计算流量")
				} else {
					setText(u.status, "历史兼容统计报告已整理 · 待独立核验")
				}
				if c.action == "h5-batch" {
					u.log("【H5 清单已生成】\r\n" + pretty(c.result) + "\r\n这只是保存解文件清单，没有重新计算任何通道流量。")
				} else {
					u.log("【历史报告已整理】\r\n" + pretty(c.result) + "\r\nHTML 可在浏览器打开。内容来自已有报告，不等于当前 H5 重新计算。")
				}
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "h5-common-compare" {
			if c.err != nil {
				setText(u.status, "共同截面复算未完成 · 未发布差值")
				u.log("【统一截面对比失败】" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				if c.result["same_cut_candidate"] == true {
					setText(u.status, "共同截面候选对比已输出 · 工况与收敛待核验")
				} else {
					setText(u.status, "两份数据已重算 · 片间差值不可直接比较")
				}
				u.log("【共同截面对比】" + pretty(c.result))
				if report, ok := c.result["html_file"].(string); ok && isApprovedReportPath(u.app.dataDir, report) {
					pShellExecute.Call(u.hwnd, uintptr(unsafe.Pointer(ptr("open"))), uintptr(unsafe.Pointer(ptr(report))), 0, 0, 1)
				}
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "h5-geometry-precheck" {
			if c.err != nil {
				setText(u.status, "Case 几何预检执行失败")
				u.log("【几何预检错误】" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				if c.result["status"] == "geometry_candidates_identified_requires_flux_validation" {
					setText(u.status, "已读取真实晶圆候选 · 流量尚未计算")
				} else {
					setText(u.status, "壁面配对待复核 · 文件可能仍有效")
				}
				u.log("【Case 几何预检结果】" + pretty(c.result) + "\r\n此结果只验证物理壁面候选；不代表 Fluent 已完成求解。")
				if artifacts, ok := c.result["artifacts"].(map[string]any); ok {
					if report, ok := artifacts["html_file"].(string); ok && isApprovedReportPath(u.app.dataDir, report) {
						pShellExecute.Call(u.hwnd, uintptr(unsafe.Pointer(ptr("open"))), uintptr(unsafe.Pointer(ptr(report))), 0, 0, 1)
					}
				}
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "h5-rerender" {
			if c.err != nil {
				setText(u.status, "物理结果仍保留 · HTML 导出失败")
				u.log("【只重试报告导出失败】" + c.err.Error())
			} else if report, ok := c.result["html_file"].(string); ok && isApprovedReportPath(u.app.dataDir, report) {
				u.lastReportHTML = report
				setText(u.status, "HTML 已从 JSON 恢复 · 未重新计算")
				u.log("【从已计算 JSON 重新导出报告】" + report)
				pShellExecute.Call(u.hwnd, uintptr(unsafe.Pointer(ptr("open"))), uintptr(unsafe.Pointer(ptr(report))), 0, 0, 1)
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "h5-inspect" {
			if saved, ok := c.result["visible_report_path"].(string); ok {
				setText(u.reportSavedPath, saved)
				u.log("【报告保存路径】" + saved)
			} else {
				setText(u.reportSavedPath, "本次未生成 HTML。默认："+filepath.Join(u.app.dataDir, "native_reports"))
			}
			if reason, ok := c.result["custom_report_error"].(string); ok {
				u.log("【自定义目录导出失败】" + reason + "；已保留默认位置的报告。")
			}
			if c.err != nil {
				u.lastReportHTML = ""
				setText(u.status, "H5 配对/数据检查未完成")
				u.log("【H5 检查失败】" + c.err.Error() + "\r\n" + pretty(c.result))
			} else {
				setText(u.status, "H5 分析已归档 · 片间物理结果待核验")
				if report, ok := c.result["html_file"].(string); ok && report != "" {
					u.lastReportHTML = report
				}
				if saved, ok := c.result["analysis_json"].(string); ok && isApprovedAnalysisJSON(u.app.dataDir, saved) {
					u.lastAnalysisJSON = saved
				}
				if c.result["report_save_status"] == "failed" {
					setText(u.status, "计算已完成，报告保存失败")
					u.log(fmt.Sprintf("【报告保存失败】JSON：%v；错误：%v。使用本页「仅重试 HTML 导出」不重新读取大型 H5。", c.result["analysis_json"], c.result["export_error"]))
				}
				// Make successive saved solutions immediately available to Model Compare.
				// Never compare them automatically: comparable physical cuts are not yet verified.
				if analysis, ok := c.result["analysis_json"].(string); ok && analysis != "" {
					if getText(u.compareAInput) == "" {
						setText(u.compareAInput, analysis)
					} else if getText(u.compareAInput) != analysis {
						setText(u.compareBInput, analysis)
					}
				}
				cf, _ := c.result["case"].(map[string]any)
				df, _ := c.result["data"].(map[string]any)
				py, _ := c.result["pyfluent"].(map[string]any)
				if v, ok := c.result["schema_probe_file"].(string); ok && v != "" {
					u.log("【小型 H5 结构样本】" + v + "（仅本地保存，无自动上传）")
				}
				if v, ok := c.result["geometry_candidate_file"].(string); ok && v != "" {
					u.log("【当前模型几何候选】" + v + "（仅候选壁面站点，须核验后才能划分通道）")
				}
				derived, _ := c.result["derived_metrics"].(map[string]any)
				cff, _ := c.result["cff_topology"].(map[string]any)
				globalFlux, _ := py["global_native_sv_flux"].(map[string]any)
				cellBalance, _ := py["native_cell_flux_balance"].(map[string]any)
				if c.result["physical_analysis_status"] == "native_11_9_profile_computed" {
					setText(u.status, "真实 H5 原生通量已重算 · 11.9 几何规则核验通过")
					if native, ok := c.result["native_direct"].(map[string]any); ok {
						if metrics, ok := native["metrics"].(map[string]any); ok {
							layers, _ := metrics["layers"].(map[string]any)
							up, _ := layers["upper"].(map[string]any)
							lo, _ := layers["lower"].(map[string]any)
							u.log(fmt.Sprintf("【11.9 原生结果】共 %v 条通道\r\n上层片间占比 %v%%，CV %v%%\r\n下层片间占比 %v%%，CV %v%%\r\n整体不平衡 %v%%；压降（低速近似）%v Pa\r\nCSV：%v\r\nHTML：%v\r\n结果已从保存解直接重算；仍需独立核验收敛与其他几何规则。", metrics["channel_count"], up["share_inlet_pct"], up["cv_pct"], lo["share_inlet_pct"], lo["cv_pct"], metrics["mass_imbalance_pct"], metrics["pressure_drop_pa_low_speed_approx"], c.result["native_csv"], c.result["html_file"]))
						}
					}
				} else if c.result["physical_analysis_status"] == "adaptive_native_candidate_computed" {
					setText(u.status, "H5 已自动计算片间/旁路 · 新几何判据待独立核验")
					if native, ok := c.result["native_direct"].(map[string]any); ok {
						if metrics, ok := native["metrics"].(map[string]any); ok {
							layers, _ := metrics["layers"].(map[string]any)
							ports, _ := metrics["port_flux"].(map[string]any)
							// Do not dump hundreds of per-channel objects into a Win32 EDIT log.
							// Show a bounded overview; full data remain in JSON/CSV/HTML.
							brief := []string{}
							for _, name := range []string{"lower", "upper", "single"} {
								if layer, ok := layers[name].(map[string]any); ok {
									reverse := any("未取得")
									if directional, ok := layer["partition_directional_kg_s"].(map[string]any); ok {
										if central, ok := directional["central"].(map[string]any); ok {
											reverse = central["negative_magnitude_kg_s"]
										}
									}
									sensitivityStatus := "未取得"
									if sensitivity, ok := layer["cut_height_sensitivity"].(map[string]any); ok {
										switch sensitivity["status"] {
										case "stable_within_1pp_advisory_only":
											sensitivityStatus = "±1mm 变化≤1pp（仅候选）"
										case "sensitive_candidate_needs_review":
											sensitivityStatus = "±1mm 变化>1pp，需复核"
										case "skipped_resource_budget":
											sensitivityStatus = "数据量大，偏移检查已跳过"
										default:
											sensitivityStatus = "偏移检查未确认"
										}
									}
									brief = append(brief, fmt.Sprintf("%s：%v 条通道，CV %v%%，片间占入口 %v%%，中央回流 %v kg/s；切面：%s",
										name, layer["channel_count"], layer["cv_pct"], layer["share_inlet_pct"], reverse, sensitivityStatus))
								}
							}
							u.log(fmt.Sprintf("【新几何原生计算候选】%d 层；入口 %v kg/s，出口 %v kg/s，质量不平衡 %v%%。\r\n%s\r\nCSV：%v\r\nHTML：%v\r\n完整逐通道和正反向通量请看报告；新几何分区仍需核验。", len(layers), ports["inlet_kg_s"], ports["outlet_kg_s"], ports["mass_imbalance_pct"], strings.Join(brief, "\r\n"), c.result["native_csv"], c.result["html_file"]))
						}
					}
				} else if c.result["physical_analysis_status"] == "partial_physical_metrics" {
					setText(u.status, "部分完成 · 晶圆几何待复核")
					if blocker, ok := c.result["first_full_analysis_blocker"].(map[string]any); ok {
						u.log(fmt.Sprintf("【晶圆几何待复核】阶段：%v；原因：%v。端口和挡板结果仍可打开查看。",
							blocker["stage"], blocker["reason"]))
					}
				} else if derived["status"] == "candidate_only_not_geometry_verified" {
					setText(u.status, "已读取原生测量面通量 · 几何/法向待核验")
				} else if py["status"] == "not_installed" {
					setText(u.status, "只完成 H5 索引 · 未发现 PyFluent 离线读取器")
				} else if py["status"] == "reader_failed" {
					setText(u.status, "H5 结构可读 · PyFluent 解析失败")
				} else if globalFlux["status"] == "complete_original_sv_flux_global_face_id_map" {
					setText(u.status, "H5 全局原生通量已映射 · 片间测量待完成")
				} else {
					setText(u.status, "H5 已读取 · 片间测量面未取得")
				}
				// Report each gate independently, even when other physical metrics succeeded.
				if stages, ok := c.result["stage_statuses"].(map[string]any); ok {
					for _, step := range []struct{ key, title string }{
						{"file_pairing", "Case/Data 配对"},
						{"wafer_geometry", "晶圆几何"},
						{"report_publication", "HTML 报告保存"},
					} {
						if item, ok := stages[step.key].(map[string]any); ok {
							u.log(fmt.Sprintf("【%s】状态：%v；阶段：%v；原因：%v",
								step.title, item["status"], item["stage"], item["reason"]))
						}
					}
				}
				if c.result["report_save_status"] == "failed" {
					// Do not allow the wafer status to overwrite export failure.
					setText(u.status, "计算结果已保留 · HTML 保存失败，可仅重试导出")
				}
				if c.result["physical_analysis_status"] != "native_11_9_profile_computed" && c.result["physical_analysis_status"] != "adaptive_native_candidate_computed" {
					u.log(fmt.Sprintf("【网格/原生通量自检】CFF：%v\r\n全局 Face ID 映射：%v\r\n单元守恒：%v\r\n通过这些检查仍不能证明已完成晶圆片间/旁路分析。", cff["status"], globalFlux["status"], cellBalance["status"]))
				}
				u.log(fmt.Sprintf("【H5 读取与产物】\r\n模型：%v\r\nCase：%v\r\nData：%v\r\nPyFluent：%v\r\n原生测量分析：%v\r\nJSON：%v\r\nHTML：%v\r\n\r\n11.9 严格规则或新几何自动判据通过时读取新生成的 CSV/HTML；新几何结果须独立核验。", c.result["model_id"], cf["path"], df["path"], py["status"], derived["status"], c.result["analysis_json"], c.result["html_file"]))
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "wb-settings" {
			if c.err == nil {
				if file, ok := c.result["file"].(string); ok {
					setText(u.templatePathInput, file)
					_ = u.app.rememberWBStandardSource(file)
					u.wbSetPlanPath = ""
				}
			}
			if c.err != nil {
				setText(u.status, "Fluent 工况导出未确认")
				u.log("【WB → Fluent 工况导出未完成】\r\n" + c.err.Error() + "\r\n不会重复投递。")
			} else {
				setText(u.status, "已获取 Fluent 原生设置文件 · 待严格核验")
				u.log("【真实 Fluent 设置文件已导出】\r\n" + pretty(c.result) + "\r\n注意：只包含用户修改设置，默认项和场数据不在此文件中，不能直接把它当作完整跨模型验收。")
			}
			u.refreshHistory()
			u.syncActions()
			return
		}
		if c.action == "repair" {
			if c.err != nil {
				u.log("【WB 依赖配置失败；仿真未受影响】\r\n" + c.err.Error())
				setText(u.status, "PyWorkbench 配置失败 · WB 尚未连接")
				setText(u.connectionHint, "请查看完整安装错误；不会自动重试安装或修改端口。")
			} else {
				u.needsRepair = false
				u.log("【PyWorkbench 已安装并验证】" + fmt.Sprint(c.result["python"]) + "。现在只重试你填写的 WB 端口，不启动 Server。")
				setText(u.status, "PyWorkbench 已就绪 · 正在连接指定 WB 端口")
			}
			u.connectionVisibility()
			u.refreshHistory()
			u.syncActions()
			if c.err == nil {
				u.startManualWBConnect()
			}
			return
		}
		if c.err != nil {
			if c.action == "preview" || c.action == "publish" {
				u.invalidatePreview()
			}
			u.log(fmt.Sprintf("【%s 失败】\r\n%s\r\n\r\n%s", c.action, c.err, pretty(c.result)))
			setText(u.status, "操作未完成 · 请查看错误原因")
		} else {
			setText(u.status, "完成 · "+c.action)
			if c.action == "publish" {
				setText(u.status, "模板内字段回读通过 · 非完整工况验收")
			}
			if c.action == "extract" {
				setText(u.templateInput, u.app.templateJSON())
				_, _, p, _ := u.app.snapshot()
				setText(u.templatePathInput, p)
			}
			if c.action == "scan" {
				u.currentScan, _ = c.result["data"].(map[string]any)
				u.scanCompleted = u.currentScan != nil && u.currentScan["scan_readiness"] == "ready_to_review"
				if u.currentScan != nil {
					if path, e := u.app.archiveScan(u.currentScan); e == nil {
						u.log("只读快照已归档：" + path + "\r\n" + summarizeScan(u.currentScan))
					}
				}
			}
			if c.action == "preview" {
				plan, ok := c.result["data"].(map[string]any)
				if ok && plan["ok"] == true {
					sig, _ := plan["plan_signature"].(string)
					u.previewSig = sig
					u.previewIdentity = u.app.previewIdentity()
					u.previewAt = time.Now()
				} else {
					u.invalidatePreview()
				}
			}
			if c.action == "publish" {
				u.published = true
				u.invalidatePreview()
				u.published = true
				setText(u.confirmInput, "")
			}
			if c.action == "scan" && !u.scanCompleted {
				setText(u.status, "只读扫描不完整 · 需核对 Fluent Case")
			}
			u.log("【" + c.action + " 完成】\r\n" + pretty(c.result))
		}
		u.refreshHistory()
		u.syncActions()
	default:
	}
}
func (u *UI) doDiscover() {
	if e := u.saveConnection(); e != nil {
		u.log(e.Error())
		return
	}
	u.serverCandidates = u.app.discover()
	winMsg(u.serverList, cbResetContent, 0, 0)
	for _, c := range u.serverCandidates {
		winMsg(u.serverList, cbAddString, 0, uintptr(unsafe.Pointer(ptr(c.Modified+"   "+c.Path))))
	}
	if len(u.serverCandidates) == 1 {
		winMsg(u.serverList, cbSetCurSel, 0, 0)
		setText(u.serverInput, u.serverCandidates[0].Path)
		_ = u.saveConnection()
	}
	if len(u.serverCandidates) == 0 {
		u.log("没有找到 Fluent Server 的连接文件。请在已打开 Fluent 中选择「文件 → 应用程序 → Server → Start」，启动后点击「自动连接并只读扫描」。若未找到可手动选择生成的 server-info 文件。")
	}
	if len(u.serverCandidates) > 1 {
		u.log(fmt.Sprintf("找到 %d 个 server-info 候选，未自动选择目标，防止误连错误 Fluent。请在下拉列表选择当前会话。", len(u.serverCandidates)))
	} else if len(u.serverCandidates) == 1 {
		u.log("找到一个 server-info 文件，已自动填写连接路径；请执行自动连接以验证该会话仍在线。")
	}
}

// Copies a command, never pastes or executes it inside Fluent.
func copyToClipboard(content string) bool {
	data := syscall.StringToUTF16(content)
	n := uintptr(len(data) * 2)
	if r, _, _ := pOpenClipboard.Call(ui.hwnd); r == 0 {
		return false
	}
	defer pCloseClipboard.Call()
	pEmptyClipboard.Call()
	h, _, _ := pGlobalAlloc.Call(0x0002, n) // GMEM_MOVEABLE
	if h == 0 {
		return false
	}
	dst, _, _ := pGlobalLock.Call(h)
	if dst == 0 {
		pGlobalFree.Call(h)
		return false
	}
	pRtlMoveMemory.Call(dst, uintptr(unsafe.Pointer(&data[0])), n)
	pGlobalUnlock.Call(h)
	if r, _, _ := pSetClipboard.Call(13, h); r == 0 {
		pGlobalFree.Call(h)
		return false
	}
	return true
}

func chooseFile(title, filter string) string {
	// Standard Windows native Open File dialog. No browser involved.
	buff := make([]uint16, 32768)
	flt := []uint16{}
	for _, r := range filter {
		flt = append(flt, uint16(r))
	}
	flt = append(flt, 0)
	dlg := openFileName{Size: uint32(unsafe.Sizeof(openFileName{})), Owner: ui.hwnd, Filter: &flt[0], File: &buff[0], MaxFile: uint32(len(buff)), Title: ptr(title), Flags: ofnExplorer | ofnFileMustExist | ofnPathMustExist}
	v, _, _ := pGetOpenFile.Call(uintptr(unsafe.Pointer(&dlg)))
	if v == 0 {
		return ""
	}
	return syscall.UTF16ToString(buff)
}
func (u *UI) onCommand(w uintptr) {
	id := int(uint16(w & 0xffff))
	notification := int(uint16(w >> 16))
	if notification == enChange && id == idWBPortInput {
		u.wbContext = WorkbenchAttach{}
		u.wbSetPlanPath = ""
		u.wbSelectionRequired = false
		u.scanCompleted = false
		u.invalidatePreview()
		if button := u.controls[idAutoConnect]; button != nil {
			setText(button.hwnd, "连接 Workbench")
		}
		if u.status != 0 {
			setText(u.status, "端口已修改 · 尚未连接")
		}
		return
	}
	if notification == enChange && id == 2213 {
		u.syncActions()
		return
	}
	if notification == enChange && id == 2209 {
		u.wbSetPlanPath = ""
		u.syncActions()
		return
	}
	if notification == enChange && (id == 2510 || id == 2511) {
		u.syncActions()
		return
	}
	if notification == enChange && (id == 2110 || id == 2112 || id == 2211) {
		u.invalidatePreview()
		return
	}
	if id >= idNavBase && id < idNavBase+6 {
		u.changePage(navPageOrder[id-idNavBase])
		return
	}
	if u.app.isBusy() && id != idStop && id != idInspectSolve {
		return
	}
	switch id {
	case idToggleLegacy:
		u.showLegacy = !u.showLegacy
		u.legacyVisibility()
	case idToggleAdvanced:
		u.showAdvanced = !u.showAdvanced
		u.connectionVisibility()
	case idAutoConnect:
		port, _ := strconv.Atoi(strings.TrimSpace(getText(u.wbPortInput)))
		if u.wbContext.Port == port && u.wbContext.Security != "" && u.wbContext.Python != "" &&
			(u.wbContext.Status == "workbench_connected" || u.wbContext.Status == "no_fluent_system" ||
				u.wbContext.Status == "fluent_probe_failed" || u.wbContext.Status == "selection_required" ||
				u.wbContext.Status == "fluent_connected" || u.wbContext.Status == "workbench_connected_scan_failed") {
			u.startWBFluentCheck()
		} else {
			u.startManualWBConnect()
		}
	case idRepairPython:
		u.startRepairPython()
	case idWBOpenDiag:
		path := filepath.Join(u.app.dataDir, "wb_last_connection_diagnostic.txt")
		if st, e := os.Stat(path); e != nil || st.IsDir() {
			messageBox("连接诊断", "尚无本次连接诊断。请先输入端口并点击「连接 Workbench」。", mbIconInfo)
		} else {
			r, _, _ := pShellExecute.Call(u.hwnd, uintptr(unsafe.Pointer(ptr("open"))), uintptr(unsafe.Pointer(ptr(path))), 0, 0, 1)
			if r <= 32 {
				u.log(fmt.Sprintf("无法打开完整连接诊断 (ShellExecute %d)：%s", r, path))
			}
		}
	case idCopyServerCmd:
		messageBox("Workbench 端口", "请输入 Workbench 脚本服务 StartServer() 输出的端口，不是 Fluent gRPC 端口。\n\n只连接你填写的端口，绝不自动找端口或启动 Server。\n\n如果 Workbench 脚本服务尚未启动，请在 Workbench 自己的命令窗口执行 StartServer()；这不是 Fluent 控制台的命令。\n\n已经打开 Fluent 并不代表 Workbench 脚本服务已启动。", mbIconInfo)
	case idSaveConnect:
		if e := u.saveConnection(); e != nil {
			u.log(e.Error())
		} else {
			u.log("连接配置已保存。注意：保存配置不等于已经连通 Fluent。")
		}
	case idDiscover:
		u.doDiscover()
	case idSelftest:
		u.startTask("selftest")
	case idScanWork:
		u.changePage(1)
	case idScan:
		u.startTask("scan")
	case idSelectServer:
		if u.wbSelectionRequired {
			index := int(winMsg(u.serverList, cbGetCurSel, 0, 0))
			if index >= 0 && index < len(u.wbContext.Systems) {
				chosen := u.wbContext.Systems[index].Name
				if u.app.tryStart() {
					u.setBusy("验证选定 Fluent", true)
					contextInfo := u.wbContext
					go func() {
						ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
						defer cancel()
						result, e := u.app.probeWorkbenchSelected(ctx, contextInfo, chosen)
						u.app.record("workbench-select", result.Status, fmt.Sprint(e))
						select {
						case u.done <- completion{action: "wb-selected", workbench: &result, err: e}:
						default:
						}
						if u.hwnd != 0 {
							pPostMessage.Call(u.hwnd, wmAppDone, 0, 0)
						}
					}()
				}
			}
			return
		}
		// Legacy independent Fluent server selector, only in Advanced mode.
		index := int(winMsg(u.serverList, cbGetCurSel, 0, 0))
		if index >= 0 && index < len(u.serverCandidates) {
			setText(u.serverInput, u.serverCandidates[index].Path)
			_ = u.saveConnection()
		}
	case idPickServer:
		if s := chooseFile("选择 Fluent server-info 文件", "Text (*.txt)\x00*.txt\x00All files (*.*)\x00*.*\x00"); s != "" {
			setText(u.serverInput, s)
			_ = u.saveConnection()
		}
	case idPickPython:
		if s := chooseFile("选择 Python 解释器", "Python (python.exe)\x00*.exe\x00All files\x00*.*\x00"); s != "" {
			setText(u.pythonInput, s)
			_ = u.saveConnection()
		}
	case idExtract:
		u.startTask("extract")
	case idWBExport:
		u.startWBSettingsExport()
	case idWBReport:
		u.startWBReport()
	case idPreview:
		u.startWBConditionsPreview()
	case idPublish:
		u.startWBConditionsPublish()
	case idSaveTemplate:
		if e := u.saveEditor(); e != nil {
			u.log(e.Error())
		} else {
			u.log("模板已保存到配置目录。提示：保存 JSON 不代表已在 Fluent 验证其物理完整性。")
		}
	case idLoadTemplate:
		if e := u.loadEditor(); e != nil {
			u.log(e.Error())
		} else {
			u.log("已从磁盘载入工况模板。请先执行差异检查，不要直接发布。")
		}
	case idPickTemplate:
		if s := chooseFile("选择已导出的 Fluent 标准 .set", "Fluent settings (*.set)\x00*.set\x00All files\x00*.*\x00"); s != "" {
			setText(u.templatePathInput, s)
			if e := u.app.rememberWBStandardSource(s); e != nil {
				u.log("只能选程序导出的 .set；原因：" + e.Error())
			}
			u.wbSetPlanPath = ""
			u.syncActions()
		}
	case idHomeH5:
		if path := chooseFile("选择已保存的 Fluent Case 或 Data", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00"); path != "" {
			u.changePage(3)
			u.startH5Inspection(path)
		}
	case idPickH5:
		if path := chooseFile("选择已保存的 Fluent Case 或 Data", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00"); path != "" {
			u.startH5Inspection(path)
		}
	case 2326:
		current := strings.TrimSpace(getText(u.reportDirectory))
		if current == "" {
			current = filepath.Join(u.app.dataDir, "native_reports")
		}
		if selected := chooseReportDirectory(u.hwnd, current); selected != "" {
			setText(u.reportDirectory, selected)
		}
	case idPickH5Config:
		selected := u.h5Selected
		if selected == "" {
			selected = chooseFile("先选择需要配置辅助分析的 Fluent H5", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00")
		}
		if selected != "" {
			if cfg := chooseFile("选择此模型的物理几何配置 JSON", "JSON 配置 (*.json)\x00*.json\x00All files\x00*.*\x00"); cfg != "" {
				u.startH5InspectionWithConfig(selected, cfg)
			}
		}
	case idGeometryPrecheck:
		selected := u.h5Selected
		if selected == "" {
			selected = chooseFile("选择需要做真实几何预检的 Fluent Case 或 Data", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00")
		}
		if selected != "" {
			// Cancellation of configuration selection means automatic candidate
			// recognition, not a hidden fallback into an old 11.9 rule.
			cfg := chooseFile("可选：几何模型配置 JSON（取消则自动识别）", "JSON 配置 (*.json)\x00*.json\x00All files\x00*.*\x00")
			u.startGeometryPrecheck(selected, cfg)
		}
	case idBatchH5:
		if u.h5Selected == "" {
			u.log("请先选择一份 H5，软件将扫描它所在目录内的其他保存解。")
		} else {
			u.startBatchLocal("h5-batch", u.h5Selected)
		}
	case idScanLibrary:
		u.startBatchLocal("report-library", strings.TrimSpace(getText(u.fluxInput)))
	case idOpenReport:
		path := u.lastReportHTML
		if !isApprovedReportPath(u.app.dataDir, path) && isApprovedAnalysisJSON(u.app.dataDir, u.lastAnalysisJSON) {
			u.retrySavedReport()
			return
		}
		if !isApprovedReportPath(u.app.dataDir, path) {
			u.log("没有可打开的本次分析报告，或报告路径不在 AutoFluent 数据目录内。请先选择 H5 并完成分析。")
			return
		}
		r, _, _ := pShellExecute.Call(u.hwnd, uintptr(unsafe.Pointer(ptr("open"))), uintptr(unsafe.Pointer(ptr(path))), 0, 0, 1)
		if r <= 32 {
			u.log(fmt.Sprintf("无法打开报告 (ShellExecute %d): %s", r, path))
		}

	case idPickFlux:
		if s := chooseFile("选择历史兼容报告 HTML / JSON", "分析 JSON / 历史报告 (*.json;*.html;*.htm)\x00*.html;*.htm;*.json\x00All files\x00*.*\x00"); s != "" {
			setText(u.fluxInput, s)
			u.startMetrics()
		}
	case idMetrics:
		u.startMetrics()
	case idPickCompareA:
		if path := chooseFile("选择模型 A 的 AutoFluent 分析 JSON（兼容历史 HTML）", "分析 JSON / 历史报告 (*.json;*.html;*.htm)\x00*.html;*.htm;*.json\x00All files\x00*.*\x00"); path != "" {
			setText(u.compareAInput, path)
		}
	case idPickCompareB:
		if path := chooseFile("选择模型 B 的 AutoFluent 分析 JSON（兼容历史 HTML）", "分析 JSON / 历史报告 (*.json;*.html;*.htm)\x00*.html;*.htm;*.json\x00All files\x00*.*\x00"); path != "" {
			setText(u.compareBInput, path)
		}
	case idCompare:
		u.startCompare()
	case idCommonCut:
		first := chooseFile("选择优化前 Fluent Case/Data 保存解", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00")
		if first == "" {
			return
		}
		second := chooseFile("选择优化后 Fluent Case/Data 保存解", "Fluent H5 (*.cas.h5;*.dat.h5)\x00*.cas.h5;*.dat.h5\x00All files\x00*.*\x00")
		if second == "" {
			return
		}
		config := chooseFile("选择两份保存解共同的真实截面配置 JSON", "JSON 配置 (*.json)\x00*.json\x00All files\x00*.*\x00")
		if config == "" {
			u.log("统一截面对比必须指定相同的几何配置与物理截面，不执行默认猜测")
			return
		}
		u.startCommonCutCompare(first, second, config)
	case idRefreshHistory:
		u.refreshHistory()
	case idSolveWB:
		u.startWBSolve()
	case idInspectSolve:
		u.inspectSolveStatus()
	case idStop:
		if u.jobAction != "publish" && u.jobAction != "wb-solve" && u.jobAction != "wb-conditions-publish" && u.jobCancel != nil {
			u.jobCancel()
			u.log("已请求取消当前任务。外部 Worker 的实际停止状态仍需核验。")
		} else {
			u.log("发布期间禁止强制中止。请先确保 Fluent 设置完整后再操作。")
		}
	}
}
func (u *UI) paintButton(raw uintptr) {
	var di drawItem
	pRtlMoveMemory.Call(uintptr(unsafe.Pointer(&di)), raw, uintptr(unsafe.Sizeof(di)))
	if di.DC == 0 {
		return
	}
	id := int(di.CtlID)
	bg, fg, border := color(237, 243, 248), color(35, 58, 75), color(224, 232, 240)
	if id >= idNavBase && id < idNavBase+6 {
		bg, fg, border = color(241, 245, 249), color(69, 89, 108), color(241, 245, 249)
		if navPageOrder[id-idNavBase] == u.page {
			bg, fg, border = color(222, 244, 241), color(10, 107, 97), color(199, 231, 226)
		}
	} else if id == idPublish || id == idPreview || id == idHomeH5 || id == idPickH5 || id == idScan {
		bg, fg, border = color(12, 112, 107), color(255, 255, 255), color(12, 112, 107)
	} else if id == idStop {
		bg, fg, border = color(255, 242, 242), color(160, 66, 66), color(250, 222, 222)
	}
	if di.ItemState&odsDisabled != 0 {
		bg, fg, border = color(239, 242, 245), color(154, 164, 172), color(229, 233, 237)
	}
	brush, _, _ := pCreateBrush.Call(bg)
	pen, _, _ := pCreatePen.Call(0, 1, border)
	oldbrush, _, _ := pSelectObject.Call(di.DC, brush)
	oldpen, _, _ := pSelectObject.Call(di.DC, pen)
	rc := di.RcItem
	pRoundRect.Call(di.DC, uintptr(rc.Left+1), uintptr(rc.Top+1), uintptr(rc.Right-1), uintptr(rc.Bottom-1), 10, 10)
	pSelectObject.Call(di.DC, oldbrush)
	pSelectObject.Call(di.DC, oldpen)
	pDeleteObject.Call(brush)
	pDeleteObject.Call(pen)
	pSetBkMode.Call(di.DC, 1)
	pSetTextColor.Call(di.DC, fg)
	oldfont, _, _ := pSelectObject.Call(di.DC, u.font[fontStrong])
	label := getText(di.HwndItem)
	pDrawText.Call(di.DC, uintptr(unsafe.Pointer(ptr(label))), ^uintptr(0), uintptr(unsafe.Pointer(&rc)), dtCenter|dtVCenter|dtSingleLine|dtEndEllipsis)
	pSelectObject.Call(di.DC, oldfont)
}
func (u *UI) paint(hwnd uintptr) {
	var ps paintStruct
	dc, _, _ := pBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
	if dc != 0 {
		var rc rect
		pGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))
		light, _, _ := pCreateBrush.Call(color(247, 249, 252))
		pFillRect.Call(dc, uintptr(unsafe.Pointer(&rc)), light)
		pDeleteObject.Call(light)
		side := rect{0, 0, int32(u.scale(210)), rc.Bottom}
		pFillRect.Call(dc, uintptr(unsafe.Pointer(&side)), u.brushSide)
		top := rect{int32(u.scale(211)), 0, rc.Right, int32(u.scale(98))}
		pFillRect.Call(dc, uintptr(unsafe.Pointer(&top)), u.brushWhite)
		// Content and output cards give the same calm, functional hierarchy as AutoMesh.
		bounds := makeUILayout(u.width*96/u.dpi, u.height*96/u.dpi)
		logY := bounds[9005].Y
		panel := rect{int32(u.scale(226)), int32(u.scale(106)), rc.Right - int32(u.scale(19)), int32(u.scale(logY - 18))}
		pFillRect.Call(dc, uintptr(unsafe.Pointer(&panel)), u.brushWhite)
		output := rect{int32(u.scale(226)), int32(u.scale(logY - 10)), rc.Right - int32(u.scale(19)), rc.Bottom - int32(u.scale(33))}
		pFillRect.Call(dc, uintptr(unsafe.Pointer(&output)), u.brushWhite)
	}
	pEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
}
func makeFont(height, weight int) uintptr {
	// GDI Segoe UI; default UI font size in 96 DPI logical pixels.
	args := []uintptr{uintptr(int32(-height)), 0, 0, 0, uintptr(weight), 0, 0, 0, 1, 0, 0, 5, 0, uintptr(unsafe.Pointer(ptr("Microsoft YaHei UI")))}
	h, _, _ := pCreateFont.Call(args...)
	return h
}
func wndProc(hwnd uintptr, msg uint32, w, l uintptr) uintptr {
	u := ui
	if u == nil {
		r, _, _ := pDefWnd.Call(hwnd, uintptr(msg), w, l)
		return r
	}
	switch msg {
	case 0x0001: // WM_CREATE
		u.hwnd = hwnd
		u.dpi = 96
		if pGetDpi.Find() == nil {
			if v, _, _ := pGetDpi.Call(hwnd); v >= 96 && v <= 384 {
				u.dpi = int(v)
			}
		}
		// At high Windows scaling, a 150%% UI may require more vertical pixels
		// than the monitor has. Reduce only the app's effective layout scaling;
		// never render text underneath another control or outside the viewport.
		if pGetSystemMetric.Find() == nil {
			sw, _, _ := pGetSystemMetric.Call(0)
			sh, _, _ := pGetSystemMetric.Call(1)
			if sw > 0 && sh > 0 {
				limitW := int(sw-80) * 96 / 1030
				limitH := int(sh-100) * 96 / 785
				u.dpi = uiMax(96, uiMin(u.dpi, uiMin(limitW, limitH)))
			}
		}
		u.font = [4]uintptr{makeFont(u.scale(15), 400), makeFont(u.scale(15), 600), makeFont(u.scale(13), 400), makeFont(u.scale(27), 700)}
		u.brushBg, _, _ = pCreateBrush.Call(color(255, 255, 255))
		u.brushSide, _, _ = pCreateBrush.Call(color(241, 245, 249))
		u.brushWhite, _, _ = pCreateBrush.Call(color(255, 255, 255))
		pInitCommonControls.Call()
		u.buildControls()
		u.updateLayout()
		path := filepath.Join(u.app.base, "assets", "AutoFluent.ico")
		hSmall, _, _ := pLoadImage.Call(0, uintptr(unsafe.Pointer(ptr(path))), imageIcon, uintptr(u.scale(24)), uintptr(u.scale(24)), lrLoadFromFile)
		hBig, _, _ := pLoadImage.Call(0, uintptr(unsafe.Pointer(ptr(path))), imageIcon, uintptr(u.scale(64)), uintptr(u.scale(64)), lrLoadFromFile)
		if hSmall != 0 {
			winMsg(hwnd, wmSetIcon, 0, hSmall)
			winMsg(u.controls[9008].hwnd, stmSetIcon, hSmall, 0)
		}
		if hBig != 0 {
			winMsg(hwnd, wmSetIcon, 1, hBig)
		}
		return 0
	case wmClose:
		if u.jobAction == "wb-conditions-publish" {
			messageBox("工况发布进行中", "已将 .set 发布请求提交给当前 Fluent，禁止在回读完成前关闭窗口。", mbIconWarning)
			return 0
		}
		if u.jobAction == "wb-solve" {
			messageBox("Fluent 迭代已提交", "当前 Fluent 可能仍在迭代，关闭界面会使远端状态无法确认。请等命令返回，或直接在 Fluent 中核对执行状态。", mbIconWarning)
			return 0
		}
		if u.jobAction == "publish" {
			messageBox("正在发布工况", "发布进行中，禁止关闭窗口，以免中断后无法确认 Fluent 状态。", mbIconWarning)
			return 0
		}
		if u.app.isBusy() {
			if u.jobCancel != nil {
				u.jobCancel()
			}
		}
		r, _, _ := pDefWnd.Call(hwnd, uintptr(msg), w, l)
		return r
	case wmTimer:
		if w == 122 {
			u.pollTaskProgress()
			return 0
		}
		if w == 121 && u.jobAction == "wb-solve" {
			if state, e := u.app.recentSolveStatus(); e == nil {
				message := "WB 求解中 · 执行状态待核验"
				if monitor, ok := state["monitor"].(map[string]any); ok {
					if last, ok := monitor["latest"].(map[string]any); ok {
						message = fmt.Sprintf("当前残差观察：迭代 %v · 非收敛证明", last["iteration"])
					}
				}
				setText(u.busyText, message)
			}
		}
		return 0
	case wmCommand:
		u.onCommand(w)
		return 0
	case wmSize:
		u.updateLayout()
		pInvalidateRect.Call(hwnd, 0, 1)
		return 0
	case wmGetMinMaxInfo:
		if l != 0 {
			var p minMax
			pRtlMoveMemory.Call(uintptr(unsafe.Pointer(&p)), l, uintptr(unsafe.Sizeof(p)))
			p.MinTrack = point{int32(u.scale(1030)), int32(u.scale(785))}
			pRtlMoveMemory.Call(l, uintptr(unsafe.Pointer(&p)), uintptr(unsafe.Sizeof(p)))
		}
		return 0
	case wmDrawItem:
		if l != 0 {
			u.paintButton(l)
			return 1
		}
		return 0
	case wmPaint:
		u.paint(hwnd)
		return 0
	case wmEraseBkgnd:
		return 1
	case wmCtlColorStatic:
		// Windows sends WM_CTLCOLORSTATIC for READ-ONLY EDIT controls as well.
		// Those controls must NOT use TRANSPARENT background: otherwise every
		// SetWindowText/scroll repaint leaves old glyphs underneath new ones.
		if l == u.output || u.isReadOnlyEdit(l) {
			pSetBkMode.Call(w, opaqueBkMode)
			pSetBkColor.Call(w, color(255, 255, 255))
			pSetTextColor.Call(w, color(29, 45, 61))
			return u.brushWhite
		}
		pSetBkMode.Call(w, 1)
		pSetTextColor.Call(w, color(39, 55, 70))
		if l == u.header || l == u.subheader || l == u.status {
			return u.brushWhite
		}
		if c := u.controls[9009]; c != nil && l == c.hwnd {
			return u.brushSide
		}
		return u.brushBg
	case wmCtlColorEdit:
		pSetBkMode.Call(w, opaqueBkMode)
		pSetBkColor.Call(w, color(255, 255, 255))
		pSetTextColor.Call(w, color(29, 45, 61))
		return u.brushWhite
	case wmCtlColorBtn:
		pSetBkColor.Call(w, color(246, 248, 251))
		return u.brushBg
	case wmAppDone:
		u.onDone()
		return 0
	case wmDestroy:
		u.closed = true
		if u.jobCancel != nil && u.jobAction != "publish" {
			u.jobCancel()
		}
		for _, f := range u.font {
			if f != 0 {
				pDeleteObject.Call(f)
			}
		}
		for _, b := range []uintptr{u.brushBg, u.brushSide, u.brushWhite} {
			if b != 0 {
				pDeleteObject.Call(b)
			}
		}
		pPostQuit.Call(0)
		return 0
	}
	r, _, _ := pDefWnd.Call(hwnd, uintptr(msg), w, l)
	return r
}
func main() {
	runtime.LockOSThread() // Win32 message queue must stay on one OS thread.
	_ = os.Setenv("PYTHONIOENCODING", "utf-8")
	exe, e := os.Executable()
	if e != nil {
		panic(e)
	}
	base := filepath.Dir(exe)
	if _, e = os.Stat(filepath.Join(base, "worker", "af_worker.py")); e != nil {
		if wd, e := os.Getwd(); e == nil {
			base = wd
		}
	}
	ui = &UI{app: newApp(base), controls: map[int]*control{}, done: make(chan completion, 2), dpi: 96}
	_, _, _ = pSetProcessDpi.Call()
	inst, _, _ := pGetModuleHandle.Call(0)
	className := ptr("AutoFluent.Native.Window.1")
	cursor, _, _ := pLoadCursor.Call(0, 32512)
	wc := windowClass{Size: uint32(unsafe.Sizeof(windowClass{})), Style: 0x0003, WndProc: syscall.NewCallback(wndProc), Instance: inst, Cursor: cursor, ClassName: className}
	r, _, _ := pRegisterClass.Call(uintptr(unsafe.Pointer(&wc)))
	if r == 0 {
		panic("RegisterClassExW failed")
	}
	title := ptr("AutoFluent · ALD 标准仿真与模型对比")
	initialW, initialH := 1200, 805
	if pGetDpiSystem.Find() == nil && pGetSystemMetric.Find() == nil {
		if d, _, _ := pGetDpiSystem.Call(); d >= 96 && d <= 384 {
			effectiveDPI := int(d)
			if sw, _, _ := pGetSystemMetric.Call(0); sw > 0 {
				if sh, _, _ := pGetSystemMetric.Call(1); sh > 0 {
					effectiveDPI = uiMax(96, uiMin(effectiveDPI, uiMin(int(sw-80)*96/1030, int(sh-100)*96/785)))
				}
			}
			initialW = 1200 * effectiveDPI / 96
			initialH = 805 * effectiveDPI / 96
		}
	}
	hwnd, _, _ := pCreateWindow.Call(0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(title)), wsOverlapped|wsVisible|wsClipChildren,
		80, 45, uintptr(initialW), uintptr(initialH), 0, 0, inst, 0)
	if hwnd == 0 {
		panic("CreateWindowExW failed")
	}
	pShowWindow.Call(hwnd, swShow)
	pUpdateWindow.Call(hwnd)
	// Explicit user action required: no startup connection, discovery, or StartServer().
	var m msg
	for {
		r, _, _ := pGetMessage.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if r == 0 || r == ^uintptr(0) {
			break
		}
		if r, _, _ := pIsDialogMessage.Call(hwnd, uintptr(unsafe.Pointer(&m))); r != 0 {
			continue
		}
		pTranslate.Call(uintptr(unsafe.Pointer(&m)))
		pDispatch.Call(uintptr(unsafe.Pointer(&m)))
	}
}

// Avoid flashing a Python console when launched by the GUI-subsystem EXE.
func configureChildProcess(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
