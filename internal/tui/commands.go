package tui

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/lisnoiv/novaproxy-cli/internal/api"
	"github.com/lisnoiv/novaproxy-cli/internal/config"
	"github.com/lisnoiv/novaproxy-cli/internal/tools"
)

var helpItems = []dialogItem{
	{"/help", "список команд"},
	{"/init", "создать/обновить NOVA.md"},
	{"/compact", "сжать сессию в сводку"},
	{"/new", "новая сессия"},
	{"/clear", "сброс контекста"},
	{"/model", "сменить модель"},
	{"/models", "список моделей"},
	{"/mode", "режим code/chat/reasoning"},
	{"/system", "системный промпт"},
	{"/mcp", "MCP-серверы"},
	{"/tools", "инструменты"},
	{"/permissions", "разрешения"},
	{"/key", "сменить API-ключ"},
	{"/balance", "баланс ключа"},
	{"/cost", "токены сессии"},
	{"/status", "инфо о сессии"},
	{"/history", "все запросы"},
	{"/sessions", "сохранённые сессии"},
	{"/resume", "вернуться в сессию"},
	{"/theme", "сменить тему"},
	{"/copy", "ответ в буфер"},
	{"/copycode", "код в буфер"},
	{"/diff", "git diff --stat"},
	{"/doctor", "диагностика"},
	{"/export", "экспорт чата"},
	{"/exit", "выход"},
}

var commandNames = func() []string {
	out := []string{}
	for _, h := range helpItems {
		out = append(out, h.Title)
	}
	return out
}()

func (m Model) handleCommand(p string) (Model, tea.Cmd) {
	parts := strings.SplitN(p, " ", 2)
	c := strings.ToLower(parts[0])
	arg := ""
	if len(parts) > 1 {
		arg = parts[1]
	}
	say := func(lines ...string) {
		for _, l := range lines {
			m.entries = append(m.entries, chatEntry{Kind: "sys", Text: l})
		}
		m.refreshViewport()
	}

	switch c {
	case "/exit", "/quit":
		m.quitting = true
		return m, tea.Quit

	case "/help":
		m.openDialog("Commands", helpItems, func(idx int) {
			if idx >= 0 && idx < len(helpItems) {
				m.input.SetValue(helpItems[idx].Title + " ")
			}
		})

	case "/init":
		m.entries = append(m.entries, chatEntry{Kind: "user", Text: "/init"})
		prompt := "Analyze this project and create a NOVA.md file (~20 lines) with build/lint/test commands, code style guidelines, project structure. If NOVA.md exists, improve it. Use write_file."
		m.messages = append(m.messages, apiMessageUser(prompt))
		m.refreshViewport()
		mm, cmd := m.startStream()
		return mm.(Model), cmd

	case "/compact":
		m.compact()
		say("контекст сжат")

	case "/new":
		m.saved[m.sessionID] = savedSession{m.sessName, m.entries, m.messages}
		maxID := m.sessionID
		for k := range m.saved {
			if k > maxID {
				maxID = k
			}
		}
		m.sessionID = maxID + 1
		m.sessName = "untitled"
		m.entries = nil
		m.resetMessages()
		m.refreshViewport()
		say("новая сессия #" + strconv.Itoa(m.sessionID))

	case "/clear", "/reset":
		m.entries = nil
		m.tin, m.tout = 0, 0
		m.resetMessages()
		m.refreshViewport()
		say("контекст сброшен")

	case "/model":
		if arg != "" {
			m.cfg.Model = arg
			m.cfg.Save()
			say("модель: " + arg)
			return m, nil
		}
		models := m.models
		if len(models) == 0 {
			models = []string{m.cfg.Model}
		}
		items := []dialogItem{}
		for _, md := range models {
			desc := ""
			if md == m.cfg.Model {
				desc = "current"
			}
			items = append(items, dialogItem{md, desc})
		}
		m.openDialog("Select Model", items, func(idx int) {
			if idx >= 0 && idx < len(models) {
				m.cfg.Model = models[idx]
				m.cfg.Save()
				m.setStatus("модель: "+models[idx], false)
			}
		})

	case "/models":
		models := m.models
		if len(models) == 0 {
			models = []string{m.cfg.Model}
		}
		items := []dialogItem{}
		for _, md := range models {
			mark := "  "
			if md == m.cfg.Model {
				mark = "● "
			}
			items = append(items, dialogItem{mark + md, ""})
		}
		m.openDialog("Models", items, nil)

	case "/mode":
		if arg != "" {
			mode := normalizeMode(arg)
			m.cfg.Mode = mode
			m.cfg.Save()
			m.resetMessagesKeepHistory()
			say("режим: " + mode)
			return m, nil
		}
		m.openDialog("Mode", []dialogItem{
			{"code", "рабочий код, минимум пояснений"},
			{"chat", "точные развёрнутые ответы"},
			{"reasoning", "цепочки рассуждений"},
		}, func(idx int) {
			modes := []string{"code", "chat", "reasoning"}
			if idx >= 0 && idx < 3 {
				m.cfg.Mode = modes[idx]
				m.cfg.Save()
				m.resetMessagesKeepHistory()
				m.setStatus("режим: "+modes[idx], false)
			}
		})

	case "/system":
		if arg == "reset" {
			m.cfg.SystemPrompt = ""
			m.cfg.Save()
			m.resetMessagesKeepHistory()
			say("системный промпт сброшен")
		} else if arg != "" {
			m.cfg.SystemPrompt = arg
			m.cfg.Save()
			m.resetMessagesKeepHistory()
			say("системный промпт обновлён")
		} else {
			cur := m.cfg.SystemPrompt
			if cur == "" {
				cur = SystemPromptFor(m.cfg.Mode, "")
			}
			say("system: "+cur, "", "изменить: /system <текст> · сброс: /system reset")
		}

	case "/mcp":
		return m.handleMCP(arg)

	case "/tools":
		return m.handleTools(arg)

	case "/permissions":
		cur := m.cfg.Permissions
		bash, write_ := cur["bash"], cur["write"]
		m.openDialog("Permissions", []dialogItem{
			{"bash: " + bash, "выполнение shell-команд"},
			{"write: " + write_, "запись и правка файлов"},
			{"сбросить", "вернуть ask для всего"},
		}, func(idx int) {
			switch idx {
			case 0:
				cur["bash"] = nextPerm(cur["bash"])
			case 1:
				cur["write"] = nextPerm(cur["write"])
			case 2:
				cur = map[string]string{"bash": "ask", "write": "ask"}
			}
			m.cfg.Permissions = cur
			m.cfg.Save()
		})

	case "/key", "/auth":
		m.entries = append(m.entries, chatEntry{Kind: "sys", Text: "смена ключа: выйди и запусти novaproxy --auth, или используй /key px_live_..."})
		m.refreshViewport()
		if arg != "" && config.KeyFormatOK(arg) {
			tmp := api.New(arg)
			ok, errStr := tmp.ValidateKey()
			if ok {
				m.cfg.APIKey = arg
				m.cfg.Save()
				m.client.Key = arg
				say("ключ обновлён")
			} else {
				say("ключ отклонён: " + errStr)
			}
		}

	case "/balance":
		info, err := m.client.GetKeyInfo()
		if err != nil {
			say("ошибка: " + err.Error())
			return m, nil
		}
		say("баланс: "+fmt.Sprintf("%.0f %s", info.Balance, info.Currency),
			fmt.Sprintf("запросов: %d · активен: %v", info.RequestCount, info.Active))

	case "/cost":
		say(fmt.Sprintf("in %d · out %d", m.tin, m.tout))

	case "/status":
		say(fmt.Sprintf("сессия #%d · %s · %s · режим %s · запросов %d",
			m.sessionID, m.sessName, m.currentModel(), m.cfg.Mode, m.reqs))

	case "/history":
		items := []dialogItem{}
		for i, e := range m.entries {
			if e.Kind == "user" {
				items = append(items, dialogItem{fmt.Sprintf("%2d %s", i+1, truncStr(e.Text, 50)), ""})
			}
		}
		m.openDialog("History", items, nil)

	case "/sessions":
		items := []dialogItem{}
		ids := []int{}
		for k := range m.saved {
			ids = append(ids, k)
		}
		sort.Ints(ids)
		for _, k := range ids {
			v := m.saved[k]
			items = append(items, dialogItem{fmt.Sprintf("#%d %s", k, v.name), fmt.Sprintf("%d сообщ.", len(v.entries))})
		}
		m.openDialog("Sessions", items, func(idx int) {
			if idx >= 0 && idx < len(ids) {
				m.resumeSession(ids[idx])
			}
		})

	case "/resume":
		if arg != "" {
			if k, err := strconv.Atoi(arg); err == nil {
				m.resumeSession(k)
			}
			return m, nil
		}
		mm, _ := m.handleCommand("/sessions")
		return mm, nil

	case "/theme":
		names := []string{}
		for _, t := range Themes {
			names = append(names, t.Name)
		}
		m.openDialog("Select Theme", []dialogItem{
			{"opencode", ""}, {"emerald", ""}, {"violet", ""}, {"cyan", ""}, {"amber", ""},
		}, func(idx int) {
			if idx >= 0 && idx < len(names) {
				ApplyTheme(names[idx])
				m.cfg.Theme = names[idx]
				m.cfg.Save()
				m.refreshViewport()
			}
		})

	case "/copy":
		last := ""
		for i := len(m.entries) - 1; i >= 0; i-- {
			if m.entries[i].Kind == "ai" {
				last = m.entries[i].Text
				break
			}
		}
		if last == "" {
			say("нет ответа")
		} else if err := toolsCopy(last); err != nil {
			say("буфер недоступен")
		} else {
			say("скопировано")
		}

	case "/copycode":
		last := ""
		for i := len(m.entries) - 1; i >= 0; i-- {
			if m.entries[i].Kind == "ai" {
				last = m.entries[i].Text
				break
			}
		}
		code := extractCodeBlock(last)
		if code == "" {
			say("блок кода не найден")
		} else if err := toolsCopy(code); err != nil {
			say("буфер недоступен")
		} else {
			say("код скопирован")
		}

	case "/diff":
		say(strings.Split(tools.GitDiffStat(), "\n")...)

	case "/doctor":
		ok, err := m.client.ValidateKey()
		keySt := "ok"
		if !ok {
			keySt = "ошибка: " + err
		}
		say("python   n/a (go)",
			"platform "+platform(),
			"key      "+keySt,
			"model    "+m.currentModel(),
			"mode     "+m.cfg.Mode,
			fmt.Sprintf("mcp      %d серверов", len(m.mcp.Servers)))

	case "/export":
		fn := arg
		if fn == "" {
			fn = "nova-chat.md"
		}
		var b strings.Builder
		for _, e := range m.entries {
			b.WriteString("**" + e.Kind + "**: " + e.Text + "\n\n")
		}
		os.WriteFile(fn, []byte(b.String()), 0o644)
		say("сохранено в " + fn)

	default:
		// user-defined commands: ~/.novaproxy/commands/<name>.md
		name := strings.TrimPrefix(c, "/")
		if cf := customCommandFile(name); cf != "" {
			body, err := os.ReadFile(cf)
			if err == nil {
				prompt := strings.TrimSpace(string(body))
				if arg != "" {
					prompt += "\n\n" + arg
				}
				m.entries = append(m.entries, chatEntry{Kind: "user", Text: "/" + name + " " + arg})
				m.messages = append(m.messages, apiMessageUser(prompt))
				m.refreshViewport()
				nm, cmd := m.startStream()
				return nm.(Model), cmd
			}
		}
		say("неизвестная команда: " + c + " (/help)")
	}
	return m, nil
}

// customCommandFile returns the path to ~/.novaproxy/commands/<name>.md if it exists.
func customCommandFile(name string) string {
	if name == "" || strings.ContainsAny(name, "/\\.") {
		return ""
	}
	p := filepath.Join(config.Dir(), "commands", name+".md")
	if st, err := os.Stat(p); err == nil && !st.IsDir() {
		return p
	}
	return ""
}

// helpers -------------------------------------------------------------------

func apiMessageUser(s string) api.Message { return api.Message{Role: "user", Content: &s} }

func normalizeMode(s string) string {
	switch strings.ToLower(s) {
	case "chat", "talk", "ask":
		return "chat"
	case "reasoning", "reason", "think":
		return "reasoning"
	default:
		return "code"
	}
}

func nextPerm(cur string) string {
	switch cur {
	case "ask":
		return "allow"
	case "allow":
		return "deny"
	default:
		return "ask"
	}
}

func (m *Model) openDialog(title string, items []dialogItem, cb func(int)) {
	m.dlg = dlgCommands
	m.dlgTitle = title
	m.dlgItems = items
	m.dlgSel = 0
	m.dlgCb = cb
}

func (m *Model) compact() {
	hist := []string{}
	msgs := m.messages
	for _, mm := range msgs {
		if mm.Role == "system" {
			continue
		}
		c := ""
		if mm.Content != nil {
			c = *mm.Content
		}
		if len(c) > 500 {
			c = c[:500]
		}
		hist = append(hist, mm.Role+": "+c)
	}
	summary := strings.Join(hist, "\n")
	if len(summary) > 6000 {
		summary = summary[:6000]
	}
	m.resetMessages()
	m.messages = append(m.messages, api.Message{Role: "user", Content: strPtr("Сводка предыдущего диалога:\n" + summary)})
	m.tin = int(float64(m.tin) * 0.3)
	m.tout = int(float64(m.tout) * 0.3)
}

func (m *Model) resetMessagesKeepHistory() {
	hist := []api.Message{}
	for _, mm := range m.messages {
		if mm.Role != "system" {
			hist = append(hist, mm)
		}
	}
	m.resetMessages()
	m.messages = append(m.messages, hist...)
}

func (m *Model) resumeSession(k int) {
	v, ok := m.saved[k]
	if !ok {
		return
	}
	m.saved[m.sessionID] = savedSession{m.sessName, m.entries, m.messages}
	delete(m.saved, k)
	m.sessionID = k
	m.sessName = v.name
	m.entries = v.entries
	m.messages = v.messages
	m.refreshViewport()
}

func (m Model) handleMCP(arg string) (Model, tea.Cmd) {
	say := func(lines ...string) {
		for _, l := range lines {
			m.entries = append(m.entries, chatEntry{Kind: "sys", Text: l})
		}
		m.refreshViewport()
	}
	parts := strings.SplitN(arg, " ", 2)
	sub := parts[0]
	rest := ""
	if len(parts) > 1 {
		rest = parts[1]
	}
	switch sub {
	case "":
		lines := m.mcp.StatusLines()
		items := []dialogItem{}
		for _, l := range lines {
			items = append(items, dialogItem{l[0], l[1]})
		}
		if len(items) == 0 {
			items = append(items, dialogItem{"нет серверов", "/mcp add <имя> <cmd>"})
		}
		m.openDialog("MCP Servers", items, nil)
	case "add":
		name, cmdline, _ := strings.Cut(rest, " ")
		if cmdline == "" {
			say("формат: /mcp add <имя> <команда> [args...]")
			return m, nil
		}
		fields := strings.Fields(cmdline)
		spec := config.MCPServer{Command: fields[0], Args: fields[1:]}
		ok, msg := m.mcp.Add(name, spec)
		m.cfg.MCPServers = m.mcp.Specs
		m.cfg.Save()
		if ok {
			say("✓ " + name + ": " + msg)
		} else {
			say("✗ " + name + ": " + msg)
		}
	case "del":
		if m.mcp.Remove(rest) {
			m.cfg.MCPServers = m.mcp.Specs
			m.cfg.Save()
			say("сервер " + rest + " удалён")
		} else {
			say("сервер " + rest + " не найден")
		}
	case "tools":
		toolsList := m.mcp.OpenAITools()
		items := []dialogItem{}
		for _, t := range toolsList {
			fn := t["function"].(map[string]interface{})
			items = append(items, dialogItem{fn["name"].(string), ""})
		}
		m.openDialog("MCP Tools", items, nil)
	default:
		say("/mcp · /mcp add <имя> <cmd> · /mcp del <имя> · /mcp tools")
	}
	return m, nil
}

func (m Model) handleTools(arg string) (Model, tea.Cmd) {
	say := func(lines ...string) {
		for _, l := range lines {
			m.entries = append(m.entries, chatEntry{Kind: "sys", Text: l})
		}
		m.refreshViewport()
	}
	disabled := map[string]bool{}
	for _, d := range m.cfg.DisabledTools {
		disabled[d] = true
	}
	switch {
	case arg == "":
		m.toolsEnabledFlag = !m.toolsEnabledFlag
		if m.toolsEnabledFlag {
			say("инструменты: вкл")
		} else {
			say("инструменты: выкл")
		}
	case arg == "list":
		items := []dialogItem{}
		for _, s := range tools.Schemas() {
			fn := s["function"].(map[string]interface{})
			n := fn["name"].(string)
			mark := "✓ "
			if disabled[n] {
				mark = "✗ "
			}
			items = append(items, dialogItem{mark + n, ""})
		}
		m.openDialog("Tools", items, nil)
	case strings.HasPrefix(arg, "disable "):
		n := strings.TrimPrefix(arg, "disable ")
		found := false
		for _, d := range m.cfg.DisabledTools {
			if d == n {
				found = true
			}
		}
		if !found {
			m.cfg.DisabledTools = append(m.cfg.DisabledTools, n)
			m.cfg.Save()
		}
		say("отключён: " + n)
	case strings.HasPrefix(arg, "enable "):
		n := strings.TrimPrefix(arg, "enable ")
		out := []string{}
		for _, d := range m.cfg.DisabledTools {
			if d != n {
				out = append(out, d)
			}
		}
		m.cfg.DisabledTools = out
		m.cfg.Save()
		say("включён: " + n)
	default:
		say("/tools · /tools list · /tools disable <имя> · /tools enable <имя>")
	}
	return m, nil
}

func extractCodeBlock(s string) string {
	re := regexpCodeBlock
	m := re.FindAllStringSubmatch(s, -1)
	if len(m) == 0 {
		return ""
	}
	return m[len(m)-1][1]
}

var regexpCodeBlock = regexp.MustCompile("```(?:\\w+)?\\n(.*?)```")

func toolsCopy(s string) error {
	cmds := [][]string{
		{"termux-clipboard-set"},
		{"pbcopy"},
		{"wl-copy"},
		{"xclip", "-selection", "clipboard"},
		{"xsel", "--clipboard", "--input"},
		{"clip"},
	}
	for _, c := range cmds {
		if err := runCopy(c, s); err == nil {
			return nil
		}
	}
	return fmt.Errorf("no clipboard tool")
}

func runCopy(cmd []string, input string) error {
	c := exec.Command(cmd[0], cmd[1:]...)
	c.Stdin = strings.NewReader(input)
	return c.Run()
}

func platform() string {
	if isTermux() {
		return "termux"
	}
	return runtime.GOOS
}

func isTermux() bool {
	return strings.Contains(os.Getenv("PREFIX"), "com.termux")
}

// SystemPromptFor picks the default system prompt by mode.
func SystemPromptFor(mode, override string) string {
	if override != "" {
		return override
	}
	switch mode {
	case "chat":
		return "You are NovaProxy Assistant. Give accurate, well-structured, complete answers. Use Markdown: headings, lists, tables, fenced code blocks. If unsure, say so plainly."
	case "reasoning":
		return "You are NovaProxy Reasoner. Think step by step, keep the chain of logic explicit: state assumptions, derive conclusions, then give the final answer clearly marked as the result."
	default:
		return "You are NovaProxy Code, an expert pair-programmer. Answer with working, production-ready code first; keep explanations minimal. When modifying code, output unified diffs or clearly delimited file blocks. Never use placeholders or TODOs. Match the project's style and dependencies."
	}
}
