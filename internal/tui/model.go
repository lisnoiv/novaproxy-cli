// Package tui implements the OpenCode-style terminal UI.
package tui

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lisnoiv/novaproxy-cli/internal/api"
	"github.com/lisnoiv/novaproxy-cli/internal/config"
	"github.com/lisnoiv/novaproxy-cli/internal/mcp"
	"github.com/lisnoiv/novaproxy-cli/internal/tools"
)

const Version = "1.0.0"

// ---------------------------------------------------------------------------
// messages
// ---------------------------------------------------------------------------

type chatEntry struct {
	Kind  string // user | ai | tool | sys
	Text  string
	Model string
	Took  time.Duration
	Note  string
	Tool  string
	Args  string
	Err   bool
}

// events pumped from the streaming goroutine into the bubbletea loop
type streamStartMsg struct{}
type streamDeltaMsg struct{ text string }
type streamToolCallMsg struct{ toolIdx int; id, name, args string }
type streamDoneMsg struct {
	usage      *api.Usage
	toolCalls  []api.ToolCall
	finish     string
	err        error
}
type toolResultMsg struct {
	callID string
	name   string
	args   string
	result string
	risk   string
}
type mcpEventMsg struct{ name, status string }
type modelsLoadedMsg struct{ models []string }
type statusMsgMsg struct{ text string; isErr bool }
type clearStatusMsg struct{}

// pending tool call waiting for permission decision
type pendingCall struct {
	id, name, args, risk string
}

// dialog kinds
const (
	dlgNone = iota
	dlgCommands
	dlgModels
	dlgThemes
	dlgSessions
	dlgPermission
	dlgHelp
	dlgMode
	dlgConfirm
)

type dialogItem struct {
	Title string
	Desc  string
}

// ---------------------------------------------------------------------------
// model
// ---------------------------------------------------------------------------

type Model struct {
	cfg    *config.Config
	client *api.Client
	args   Args

	viewport viewport.Model
	input    textarea.Model
	spin     spinner.Model

	entries   []chatEntry
	messages  []api.Message
	models    []string
	mcp       *mcp.Manager
	sessionID int
	sessName  string
	saved     map[int]savedSession

	reqs      int
	tin, tout int
	t0        time.Time

	streaming   bool
	eventCh     chan tea.Msg
	streamText  *strings.Builder
	streamTools map[int]*api.ToolCall
	streamStart time.Time
	toolPending []pendingCall
	permCb      func(allow bool)

	dlg       int
	dlgTitle  string
	dlgItems  []dialogItem
	dlgSel    int
	dlgCb     func(idx int)
	dlgScroll int

	statusMsg    string
	statusIsErr  bool
	statusExpiry time.Time

	compItems []dialogItem // slash-completion candidates
	compSel   int

	width, height   int
	ready           bool
	quitting        bool
	err             error
	toolsEnabledFlag bool
}

type Args struct {
	Model           string
	Mode            string
	System          string
	ReasoningEffort string
	Quiet           bool
}

type savedSession struct {
	name     string
	entries  []chatEntry
	messages []api.Message
}

func New(cfg *config.Config, client *api.Client, args Args) Model {
	ta := textarea.New()
	ta.Placeholder = ""
	ta.Prompt = ""
	ta.ShowLineNumbers = false
	ta.SetHeight(1)
	ta.SetWidth(80)
	ta.FocusedStyle.CursorLine = lipgloss.NewStyle()
	ta.Focus()

	sp := spinner.New()
	sp.Spinner = spinner.Pulse
	sp.Style = StPrimaryB

	vp := viewport.New(80, 20)

	m := Model{
		streamText: &strings.Builder{},
		cfg:       cfg,
		client:    client,
		args:      args,
		input:     ta,
		spin:      sp,
		viewport:  vp,
		mcp:       mcp.NewManager(cfg.MCPServers),
		sessionID: 1,
		sessName:  "untitled",
		saved:     map[int]savedSession{},
		t0:        time.Now(),
		toolsEnabledFlag: true,
	}
	m.resetMessages()
	return m
}

func (m *Model) resetMessages() {
	sp := SystemPromptFor(m.cfg.Mode, m.cfg.SystemPrompt)
	m.messages = []api.Message{{Role: "system", Content: strPtr(sp)}}
}

func strPtr(s string) *string { return &s }

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------

func (m Model) Init() tea.Cmd {
	return tea.Batch(
		m.spin.Tick,
		textarea.Blink,
		m.loadModels,
		m.connectMCP,
	)
}

func (m Model) loadModels() tea.Msg {
	models, err := m.client.ListModels()
	if err != nil {
		return statusMsgMsg{"модели: " + err.Error(), true}
	}
	return modelsLoadedMsg{models}
}

func (m Model) connectMCP() tea.Msg {
	if len(m.cfg.MCPServers) == 0 {
		return nil
	}
	m.mcp.ConnectAll(nil)
	return statusMsgMsg{fmt.Sprintf("mcp: %d серверов", len(m.mcp.Servers)), false}
}

// ---------------------------------------------------------------------------
// update
// ---------------------------------------------------------------------------

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.input.SetWidth(msg.Width - 4)
		m.viewport.Width = msg.Width
		m.viewport.Height = msg.Height - 8
		if !m.ready {
			m.ready = true
			m.refreshViewport()
		}
		return m, nil

	case tea.KeyMsg:
		if m.dlg != dlgNone {
			return m.updateDialog(msg)
		}
		return m.updateKey(msg)

	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spin, cmd = m.spin.Update(msg)
		return m, cmd

	case streamDeltaMsg:
		m.streamText.WriteString(msg.text)
		m.refreshViewport()
		return m, m.readNextChunk()

	case streamToolCallMsg:
		tc := m.streamTools[msg.toolIdx]
		if tc == nil {
			tc = &api.ToolCall{Type: "function"}
			m.streamTools[msg.toolIdx] = tc
		}
		if msg.id != "" {
			tc.ID = msg.id
		}
		tc.Function.Name += msg.name
		tc.Function.Arguments += msg.args
		return m, m.readNextChunk()

	case streamDoneMsg:
		return m.onStreamDone(msg)

	case toolResultMsg:
		m.messages = append(m.messages, api.Message{
			Role: "tool", ToolCallID: msg.callID, Content: strPtr(msg.result),
		})
		m.entries = append(m.entries, chatEntry{Kind: "tool", Tool: msg.name, Args: msg.args, Text: msg.result, Err: strings.HasPrefix(msg.result, "error")})
		m.refreshViewport()
		return m.continueAfterTools()

	case modelsLoadedMsg:
		m.models = msg.models
		return m, nil

	case mcpEventMsg:
		m.setStatus("mcp "+msg.name+": "+msg.status, false)
		return m, nil

	case statusMsgMsg:
		m.setStatus(msg.text, msg.isErr)
		return m, tea.Tick(10*time.Second, func(time.Time) tea.Msg { return clearStatusMsg{} })

	case clearStatusMsg:
		m.statusMsg = ""
		return m, nil
	}

	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	cmds = append(cmds, cmd)
	return m, tea.Batch(cmds...)
}

// ---------------------------------------------------------------------------
// keys
// ---------------------------------------------------------------------------

func (m Model) updateKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// completion popup navigation
	if len(m.compItems) > 0 {
		switch msg.Type {
		case tea.KeyUp:
			if m.compSel > 0 {
				m.compSel--
			}
			return m, nil
		case tea.KeyDown:
			if m.compSel < len(m.compItems)-1 {
				m.compSel++
			}
			return m, nil
		case tea.KeyTab:
			m.input.SetValue(m.compItems[m.compSel].Title + " ")
			m.compItems = nil
			return m, nil
		case tea.KeyEsc:
			m.compItems = nil
			return m, nil
		case tea.KeyEnter:
			m.input.SetValue(m.compItems[m.compSel].Title)
			m.compItems = nil
			v := strings.TrimSpace(m.input.Value())
			m.input.Reset()
			if v == "" {
				return m, nil
			}
			return m.submit(v)
		}
	}

	switch msg.Type {
	case tea.KeyCtrlC:
		if m.streaming {
			m.stopStream()
			return m, nil
		}
		m.quitting = true
		return m, tea.Quit

	case tea.KeyEsc:
		if m.streaming {
			m.stopStream()
			return m, nil
		}

	case tea.KeyEnter:
		if msg.Alt {
			break
		}
		v := m.input.Value()
		if strings.HasSuffix(v, "\\") {
			m.input.SetValue(strings.TrimSuffix(v, "\\") + "\n")
			return m, nil
		}
		v = strings.TrimSpace(v)
		m.input.Reset()
		m.compItems = nil
		if v == "" {
			return m, nil
		}
		return m.submit(v)
	}

	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	m.updateCompletions()
	return m, cmd
}

// updateCompletion recomputes slash-command candidates from the input value.
func (m *Model) updateCompletions() {
	v := m.input.Value()
	if !strings.HasPrefix(v, "/") || strings.Contains(v, " ") || strings.Contains(v, "\n") {
		m.compItems = nil
		return
	}
	matches := []dialogItem{}
	for _, h := range helpItems {
		if strings.HasPrefix(h.Title, v) {
			matches = append(matches, h)
		}
	}
	if len(matches) == 1 && matches[0].Title == v {
		matches = nil
	}
	m.compItems = matches
	if m.compSel >= len(matches) {
		m.compSel = 0
	}
}

// stopStream cancels the in-flight stream by dropping the event channel.
// The HTTP goroutine keeps running until the server closes, but its events
// are discarded; the UI immediately returns to idle.
func (m *Model) stopStream() {
	m.streaming = false
	m.eventCh = nil
	m.entries = append(m.entries, chatEntry{Kind: "sys", Text: "генерация отменена"})
	m.refreshViewport()
}

// ---------------------------------------------------------------------------
// submit
// ---------------------------------------------------------------------------

func (m Model) submit(p string) (tea.Model, tea.Cmd) {
	if strings.HasPrefix(p, "/") {
		return m.handleCommand(p)
	}
	if strings.HasPrefix(p, "!") {
		return m.runShell(p[1:])
	}
	m.reqs++
	m.SessNameFromPrompt(p)
	expanded, attached := tools.ExpandAtRefs(p)
	for _, a := range attached {
		m.entries = append(m.entries, chatEntry{Kind: "tool", Tool: "read_file", Args: a[0], Text: a[1] + " строк прикреплено"})
	}
	m.entries = append(m.entries, chatEntry{Kind: "user", Text: p})
	m.messages = append(m.messages, api.Message{Role: "user", Content: strPtr(expanded)})
	m.refreshViewport()
	return m.startStream()
}

func (m *Model) SessNameFromPrompt(p string) {
	if m.sessName == "untitled" {
		r := []rune(p)
		if len(r) > 20 {
			m.sessName = string(r[:20])
		} else {
			m.sessName = p
		}
	}
}

// ---------------------------------------------------------------------------
// streaming
// ---------------------------------------------------------------------------

func (m Model) startStream() (tea.Model, tea.Cmd) {
	m.streaming = true
	m.streamText.Reset()
	m.streamTools = map[int]*api.ToolCall{}
	m.streamStart = time.Now()
	m.eventCh = make(chan tea.Msg, 256)
	ch := m.eventCh
	toolsList := buildTools(m)
	effort := m.args.ReasoningEffort
	msgs := m.messages
	model := m.currentModel()
	client := m.client
	go func() {
		_, err := client.ChatStream(msgs, model, toolsList, effort, func(ev api.StreamEvent) {
			if ev.Done {
				ch <- streamDoneMsg{finish: "stop"}
				return
			}
			if ev.Content != "" {
				ch <- streamDeltaMsg{text: ev.Content}
			}
			for _, d := range ev.ToolCalls {
				ch <- streamToolCallMsg{toolIdx: d.Index, id: d.ID, name: d.Name, args: d.Arguments}
			}
			if ev.Usage != nil || ev.FinishReason != "" {
				ch <- streamDoneMsg{usage: ev.Usage, finish: ev.FinishReason}
			}
		})
		if err != nil {
			ch <- streamDoneMsg{err: err}
		}
		close(ch)
	}()
	return m, m.readNextChunk()
}

func (m Model) readNextChunk() tea.Cmd {
	ch := m.eventCh
	return func() tea.Msg {
		ev, ok := <-ch
		if !ok {
			return streamDoneMsg{finish: "stop"}
		}
		return ev
	}
}

func buildTools(m Model) []api.Tool {
	if !m.toolsEnabled() {
		return nil
	}
	disabled := map[string]bool{}
	for _, d := range m.cfg.DisabledTools {
		disabled[d] = true
	}
	out := []api.Tool{}
	for _, s := range tools.Schemas() {
		fn := s["function"].(map[string]interface{})
		name := fn["name"].(string)
		if disabled[name] {
			continue
		}
		t := api.Tool{Type: "function"}
		t.Function.Name = name
		t.Function.Description = fn["description"].(string)
		t.Function.Parameters = fn["parameters"].(map[string]interface{})
		out = append(out, t)
	}
	for _, s := range m.mcp.OpenAITools() {
		fn := s["function"].(map[string]interface{})
		t := api.Tool{Type: "function"}
		t.Function.Name = fn["name"].(string)
		t.Function.Description = fn["description"].(string)
		t.Function.Parameters = fn["parameters"].(map[string]interface{})
		out = append(out, t)
	}
	return out
}

func (m Model) toolsEnabled() bool { return m.toolsEnabledFlag }

func (m Model) currentModel() string {
	if m.args.Model != "" {
		return m.args.Model
	}
	return m.cfg.Model
}

// onStreamDone assembles tool calls and either finishes or runs tools.
func (m Model) onStreamDone(msg streamDoneMsg) (tea.Model, tea.Cmd) {
	m.streaming = false
	if msg.err != nil {
		m.entries = append(m.entries, chatEntry{Kind: "sys", Text: "ошибка API: " + msg.err.Error(), Err: true})
		// drop unanswered user message
		if len(m.messages) > 0 && m.messages[len(m.messages)-1].Role == "user" {
			m.messages = m.messages[:len(m.messages)-1]
		}
		m.refreshViewport()
		m.eventCh = nil
		return m, nil
	}
	if msg.usage != nil {
		m.tin = msg.usage.PromptTokens
		m.tout = msg.usage.CompletionTokens
	}
	took := time.Since(m.streamStart)
	text := strings.TrimSpace(m.streamText.String())
	if len(m.streamTools) == 0 {
		m.entries = append(m.entries, chatEntry{Kind: "ai", Text: text, Model: m.currentModel(), Took: took})
		m.messages = append(m.messages, api.Message{Role: "assistant", Content: strPtr(text)})
		m.refreshViewport()
		m.eventCh = nil
		return m, nil
	}
	// assistant message with tool calls
	calls := []api.ToolCall{}
	for i := 0; i < len(m.streamTools); i++ {
		if tc, ok := m.streamTools[i]; ok {
			calls = append(calls, *tc)
		}
	}
	m.messages = append(m.messages, api.Message{Role: "assistant", Content: strPtr(text), ToolCalls: calls})
	m.eventCh = nil
	m.toolPending = nil
	for _, c := range calls {
		m.toolPending = append(m.toolPending, pendingCall{id: c.ID, name: c.Function.Name, args: c.Function.Arguments, risk: toolRisk(c.Function.Name)})
	}
	return m.runNextTool()
}

func toolRisk(name string) string {
	switch name {
	case "write_file", "edit_file":
		return "write"
	case "bash":
		return "bash"
	}
	return "read"
}

// runNextTool executes pending tool calls one by one, asking permission.
// Pointer receiver: it mutates toolPending/dlg on the live model.
func (m *Model) runNextTool() (tea.Model, tea.Cmd) {
	if len(m.toolPending) == 0 {
		m.streaming = true
		m.streamText.Reset()
		m.streamTools = map[int]*api.ToolCall{}
		m.streamStart = time.Now()
		nm, cmd := m.startStream()
		return nm, cmd
	}
	pc := m.toolPending[0]
	m.toolPending = m.toolPending[1:]

	mode := m.cfg.Permissions[pc.risk]
	if mode == "" {
		// read-only tools are safe: auto-allow by default
		if pc.risk == "read" {
			mode = "allow"
		} else {
			mode = "ask"
		}
	}
	if mode == "deny" {
		p := pc
		return m, func() tea.Msg {
			return toolResultMsg{callID: p.id, name: p.name, args: p.args, result: "error: permission denied", risk: p.risk}
		}
	}
	if mode == "ask" && !sessionAllowed[pc.risk] {
		m.openPermission(pc)
		return m, nil
	}
	return m, m.execTool(pc)
}

var sessionAllowed = map[string]bool{}

func (m *Model) openPermission(pc pendingCall) {
	m.dlg = dlgPermission
	m.dlgTitle = "Permission Required"
	m.dlgItems = []dialogItem{
		{"Allow (a)", ""},
		{"Allow for session (s)", ""},
		{"Deny (d)", ""},
	}
	m.dlgSel = 2
	m.dlgCb = func(idx int) {
		switch idx {
		case 0:
			m.execToolAsync(pc)
		case 1:
			sessionAllowed[pc.risk] = true
			m.execToolAsync(pc)
		default:
			toolCh <- toolResultMsg{callID: pc.id, name: pc.name, args: pc.args, result: "error: permission denied", risk: pc.risk}
		}
	}
}

func (m *Model) execTool(pc pendingCall) tea.Cmd {
	return func() tea.Msg {
		var res string
		if strings.HasPrefix(pc.name, "mcp__") {
			res = m.mcp.Dispatch(pc.name, pc.args)
		} else {
			res, _ = tools.Dispatch(pc.name, pc.args)
		}
		return toolResultMsg{callID: pc.id, name: pc.name, args: pc.args, result: res, risk: pc.risk}
	}
}

func (m *Model) execToolAsync(pc pendingCall) {
	go func() {
		toolCh <- m.execTool(pc)()
	}()
}

var toolCh = make(chan tea.Msg, 64)

// continueAfterTools restarts streaming when all tool results are in.
func (m *Model) continueAfterTools() (tea.Model, tea.Cmd) {
	if len(m.toolPending) > 0 {
		return m.runNextTool()
	}
	m.streaming = true
	m.streamText.Reset()
	m.streamTools = map[int]*api.ToolCall{}
	m.streamStart = time.Now()
	nm, cmd := m.startStream()
	return nm, cmd
}

// ---------------------------------------------------------------------------
// dialog handling
// ---------------------------------------------------------------------------

func (m Model) updateDialog(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "up", "k":
		if m.dlgSel > 0 {
			m.dlgSel--
		}
	case "down", "j":
		if m.dlgSel < len(m.dlgItems)-1 {
			m.dlgSel++
		}
	case "enter":
		cb, sel := m.dlgCb, m.dlgSel
		m.closeDialog()
		if cb != nil {
			cb(sel)
		}
		return m, m.runNextToolCmd()
	case "esc", "q", "ctrl+c":
		m.closeDialog()
		return m, nil
	default:
		// direct hotkeys in permission dialog
		if m.dlg == dlgPermission {
			switch msg.String() {
			case "a":
				m.dlgSel = 0
			case "s":
				m.dlgSel = 1
			case "d":
				m.dlgSel = 2
			}
		}
	}
	return m, nil
}

func (m *Model) closeDialog() {
	m.dlg = dlgNone
	m.dlgItems = nil
	m.dlgCb = nil
	m.dlgSel = 0
}

func (m *Model) runNextToolCmd() tea.Cmd {
	// pick up a tool result queued by a permission dialog, or continue
	select {
	case msg := <-toolCh:
		return func() tea.Msg { return msg }
	default:
	}
	if len(m.toolPending) > 0 && m.dlg == dlgNone {
		_, cmd := m.runNextTool()
		return cmd
	}
	return nil
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------

func (m Model) runShell(cmd string) (tea.Model, tea.Cmd) {
	m.reqs++
	m.entries = append(m.entries, chatEntry{Kind: "user", Text: "!" + cmd})
	out := tools.Bash(cmd, 15)
	m.entries = append(m.entries, chatEntry{Kind: "tool", Tool: "Bash", Args: cmd, Text: out})
	m.refreshViewport()
	return m, nil
}

// ---------------------------------------------------------------------------
// viewport refresh
// ---------------------------------------------------------------------------

func (m *Model) refreshViewport() {
	var b strings.Builder
	for i, e := range m.entries {
		b.WriteString(m.renderEntry(e))
		if i < len(m.entries)-1 {
			b.WriteString("\n")
		}
	}
	if m.streaming {
		b.WriteString("\n")
		b.WriteString(StPrimaryB.Render(m.spin.View()+" "+m.workingLabel()) + "\n")
		if m.streamText.Len() > 0 {
			b.WriteString(m.renderEntry(chatEntry{Kind: "ai", Text: m.streamText.String()}))
		}
	}
	m.viewport.SetContent(b.String())
	m.viewport.GotoBottom()
}

func (m Model) workingLabel() string {
	if m.streamText.Len() > 0 {
		return "Generating..."
	}
	if len(m.toolPending) > 0 {
		return "Waiting for tool response..."
	}
	return "Thinking..."
}

func (m Model) renderEntry(e chatEntry) string {
	w := m.viewport.Width - 2
	if w < 20 {
		w = 20
	}
	switch e.Kind {
	case "user":
		return BorderUser.Width(w).Render(StText.Render(e.Text)) + "\n"
	case "ai":
		body := RenderMarkdown(e.Text, w-3)
		footer := ""
		if e.Model != "" {
			footer = "\n" + StMuted.Render(" "+e.Model+" ("+fmtDur(e.Took)+")")
		}
		return BorderAI.Width(w).Render(body+footer) + "\n"
	case "tool":
		name := ToolDisplayName(e.Tool)
		header := StMuted.Render(name+": ") + StMuted.Render(truncStr(e.Args, 60))
		body := ""
		if e.Err {
			body = StErr.Render("Error: " + truncStr(strings.ReplaceAll(e.Text, "\n", " "), w-10))
		} else {
			lines := strings.Split(e.Text, "\n")
			max := 10
			if len(lines) > max {
				lines = append(lines[:max], fmt.Sprintf("… (%d more lines)", len(lines)-max))
			}
			body = StMuted.Render(strings.Join(lines, "\n"))
		}
		return BorderTool.Width(w).Render(header+"\n"+body) + "\n"
	default:
		return StMuted.Render("  "+e.Text) + "\n"
	}
}

func ToolDisplayName(name string) string {
	switch name {
	case "read_file":
		return "View"
	case "write_file":
		return "Write"
	case "edit_file":
		return "Edit"
	case "bash":
		return "Bash"
	case "search":
		return "Grep"
	case "list_dir":
		return "List"
	case "web_search":
		return "Fetch"
	}
	if strings.HasPrefix(name, "mcp__") {
		parts := strings.SplitN(name, "__", 3)
		if len(parts) == 3 {
			return parts[2]
		}
	}
	return name
}

func fmtDur(d time.Duration) string {
	if d < time.Second {
		return fmt.Sprintf("%dms", d.Milliseconds())
	}
	if d < time.Minute {
		return fmt.Sprintf("%.1fs", d.Seconds())
	}
	return fmt.Sprintf("%.1fm", d.Minutes())
}

func truncStr(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-3]) + "..."
}

// ---------------------------------------------------------------------------
// view
// ---------------------------------------------------------------------------

func (m Model) View() string {
	if m.quitting {
		return ""
	}
	if !m.ready {
		return "loading..."
	}

	var main string
	if len(m.entries) == 0 && !m.streaming {
		main = m.bannerView()
	} else {
		main = m.viewport.View()
	}

	// working line + hint
	var indicator string
	if m.streaming {
		indicator = StPrimaryB.Render(m.spin.View()+" "+m.workingLabel()) + "\n" +
			StMuted.Render("press ") + StText.Render("esc") + StMuted.Render(" to cancel")
	} else {
		indicator = StMuted.Render("enter — отправить") + StMuted.Render(" · ") +
			StText.Render("/") + StMuted.Render(" — команды · ") +
			StText.Render("@file") + StMuted.Render(" — прикрепить")
	}

	// input box
	inputBox := lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).BorderTop(true).BorderForeground(BorderN).
		Width(m.width - 1).
		Render(PromptGlyph + " " + m.input.View())

	// slash-completion popup above input
	var comp string
	if len(m.compItems) > 0 {
		var rows []string
		max := len(m.compItems)
		if max > 10 {
			max = 10
		}
		for i := 0; i < max; i++ {
			it := m.compItems[i]
			line := "  " + it.Title
			if it.Desc != "" {
				line += "  " + it.Desc
			}
			if i == m.compSel {
				rows = append(rows, StCompSel.Width(m.width-4).Render(line))
			} else {
				rows = append(rows, StCompN.Width(m.width-4).Render(line))
			}
		}
		comp = lipgloss.NewStyle().
			BorderStyle(lipgloss.NormalBorder()).BorderBottom(true).BorderForeground(BorderN).
			Width(m.width - 1).
			Render(strings.Join(rows, "\n"))
	}

	// status bar
	status := m.statusBar()

	parts := []string{main, indicator}
	if comp != "" {
		parts = append(parts, comp)
	}
	parts = append(parts, inputBox, status)

	out := lipgloss.JoinVertical(lipgloss.Left, parts...)

	// dialog overlay
	if m.dlg != dlgNone {
		dlg := m.dialogView()
		out = overlayCenter(m.width, m.height, out, dlg)
	}
	return out
}

func (m Model) bannerView() string {
	art := []string{
		"███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ ██████╗ ██████╗  ██████╗ ██╗  ██╗██╗   ██╗",
		"████╗  ██║██╔═══██╗██║   ██║██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚██╗██╔╝╚██╗ ██╔╝",
		"██╔██╗ ██║██║   ██║██║   ██║███████║██████╔╝██████╔╝██║   ██║ ╚███╔╝  ╚████╔╝ ",
		"██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║██╔═══╝ ██╔══██╗██║   ██║ ██╔██╗   ╚██╔╝  ",
		"██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║██║     ██║  ██║╚██████╔╝██╔╝ ██╗   ██║   ",
		"╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
	}
	w := m.width
	var lines []string
	lines = append(lines, "")
	for _, ln := range art {
		lines = append(lines, center(StPrimaryB.Render(ln), w))
	}
	lines = append(lines, "")
	lines = append(lines, center(StMuted.Render("NovaProxy CLI "+Version), w))
	lines = append(lines, center(StMuted.Render("https://novaproxy.duckdns.org"), w))
	lines = append(lines, center(StMuted.Render("cwd: "+cwd()), w))
	lines = append(lines, center(StMuted.Render("model: "+m.currentModel()), w))
	lines = append(lines, "")
	lines = append(lines, center(
		StMuted.Render("enter — отправить · ")+
			StText.Render("/")+StMuted.Render(" — команды · ")+
			StText.Render("@file")+StMuted.Render(" — прикрепить · ")+
			StText.Render("esc")+StMuted.Render(" — отмена"), w))
	body := strings.Join(lines, "\n")
	// vertically center in available space
	avail := m.height - 8
	padTop := (avail - len(lines)) / 2
	if padTop > 0 {
		body = strings.Repeat("\n", padTop) + body
	}
	return body
}

func center(s string, w int) string {
	sw := lipgloss.Width(s)
	if sw >= w {
		return s
	}
	return strings.Repeat(" ", (w-sw)/2) + s
}

func cwd() string {
	d, _ := os.Getwd()
	return d
}

func (m Model) statusBar() string {
	help := ChipHelp.Render("ctrl+c выход · / меню")
	toks := ChipTok.Render(fmt.Sprintf("Tokens: %d/%d", m.tin, m.tout))
	name := ChipFill.Render(" " + m.sessName + " ")
	model := ChipModel.Render(" " + m.currentModel() + " ")
	fixed := lipgloss.Width(help) + lipgloss.Width(toks) + lipgloss.Width(name) + lipgloss.Width(model)

	var mid string
	if m.statusMsg != "" && time.Now().Before(m.statusExpiry) {
		chip := ChipInfo
		if m.statusIsErr {
			chip = ChipErr
		}
		mid = chip.Render(" " + truncStr(m.statusMsg, maxInt(10, m.width-fixed-4)) + " ")
	} else {
		mid = ChipFill.Width(maxInt(1, m.width-fixed)).Render("")
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, help, toks, mid, name, model)
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// ---------------------------------------------------------------------------
// dialog view
// ---------------------------------------------------------------------------

func (m Model) dialogView() string {
	var b strings.Builder
	b.WriteString(DialogTitle.Render(m.dlgTitle) + "\n\n")
	max := 10
	start := 0
	if m.dlgSel >= max {
		start = m.dlgSel - max + 1
	}
	for i := start; i < len(m.dlgItems) && i < start+max; i++ {
		it := m.dlgItems[i]
		line := it.Title
		if it.Desc != "" {
			line += "  " + DialogDesc.Render(it.Desc)
		}
		if i == m.dlgSel {
			b.WriteString(DialogSel.Render(it.Title+"  "+it.Desc) + "\n")
		} else {
			b.WriteString(DialogItem.Render(it.Title) + renderDesc(it.Desc) + "\n")
		}
		_ = line
	}
	if len(m.dlgItems) > max {
		b.WriteString(StPrimary.Render("↑ ↓") + "\n")
	}
	b.WriteString("\n" + StMuted.Render("↑↓ / j k · enter · esc"))
	return DialogBorder.Render(b.String())
}

func renderDesc(d string) string {
	if d == "" {
		return ""
	}
	return "  " + DialogDesc.Render(d)
}

// overlayCenter draws dlg centered over the base screen.
func overlayCenter(w, h int, base, dlg string) string {
	dw := lipgloss.Width(dlg)
	dh := lipgloss.Height(dlg)
	x := (w - dw) / 2
	y := (h - dh) / 2
	if x < 0 {
		x = 0
	}
	if y < 0 {
		y = 0
	}
	baseLines := strings.Split(base, "\n")
	dlgLines := strings.Split(dlg, "\n")
	for i, dl := range dlgLines {
		row := y + i
		if row >= len(baseLines) {
			break
		}
		bl := baseLines[row]
		bw := lipgloss.Width(bl)
		left := ""
		if x > 0 {
			left = sliceANSI(bl, 0, x)
		}
		right := ""
		if bw > x+dw {
			right = sliceANSI(bl, x+dw, bw)
		}
		baseLines[row] = left + dl + right
	}
	return strings.Join(baseLines, "\n")
}

// sliceANSI returns visible cells [from:to) of an ANSI string (approximate:
// strips codes, slices, loses styling past the cut — acceptable for overlay).
func sliceANSI(s string, from, to int) string {
	plain := stripANSI(s)
	r := []rune(plain)
	if from >= len(r) {
		return strings.Repeat(" ", maxInt(0, to-from))
	}
	if to > len(r) {
		to = len(r)
	}
	if from >= to {
		return ""
	}
	return string(r[from:to])
}

func stripANSI(s string) string {
	var b strings.Builder
	inEsc := false
	for _, r := range s {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
				inEsc = false
			}
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// status helper
// ---------------------------------------------------------------------------

func (m *Model) setStatus(text string, isErr bool) {
	m.statusMsg = text
	m.statusIsErr = isErr
	m.statusExpiry = time.Now().Add(10 * time.Second)
}

// ---------------------------------------------------------------------------
// markdown
// ---------------------------------------------------------------------------

func RenderMarkdown(text string, width int) string {
	r, err := NewMarkdownRenderer(width)
	if err != nil {
		return text
	}
	out, err := r.Render(text)
	if err != nil {
		return text
	}
	return strings.TrimSpace(out)
}
