package tui

import "github.com/charmbracelet/lipgloss"

// OpenCode dark palette.
var (
	Primary   = lipgloss.Color("#fab283")
	Secondary = lipgloss.Color("#5c9cf5")
	Accent    = lipgloss.Color("#9d7cd8")
	ErrColor  = lipgloss.Color("#e06c75")
	WarnColor = lipgloss.Color("#f5a742")
	OKColor   = lipgloss.Color("#7fd88f")
	InfoColor = lipgloss.Color("#56b6c2")
	TextColor = lipgloss.Color("#e0e0e0")
	Muted     = lipgloss.Color("#6a6a6a")
	Emph      = lipgloss.Color("#e5c07b")
	Bg        = lipgloss.Color("#212121")
	Bg2       = lipgloss.Color("#252525")
	Darker    = lipgloss.Color("#121212")
	BorderN   = lipgloss.Color("#4b4c5c")
)

var (
	StPrimary   = lipgloss.NewStyle().Foreground(Primary)
	StPrimaryB  = lipgloss.NewStyle().Foreground(Primary).Bold(true)
	StSecondary = lipgloss.NewStyle().Foreground(Secondary)
	StMuted     = lipgloss.NewStyle().Foreground(Muted)
	StText      = lipgloss.NewStyle().Foreground(TextColor)
	StErr       = lipgloss.NewStyle().Foreground(ErrColor)
	StOK        = lipgloss.NewStyle().Foreground(OKColor)
	StWarn      = lipgloss.NewStyle().Foreground(WarnColor)
	StInfo      = lipgloss.NewStyle().Foreground(InfoColor)

	// message borders
	BorderAI   = lipgloss.NewStyle().BorderStyle(lipgloss.ThickBorder()).BorderLeft(true).BorderForeground(Primary).PaddingLeft(1)
	BorderUser = lipgloss.NewStyle().BorderStyle(lipgloss.ThickBorder()).BorderLeft(true).BorderForeground(Secondary).PaddingLeft(1)
	BorderTool = lipgloss.NewStyle().BorderStyle(lipgloss.ThickBorder()).BorderLeft(true).BorderForeground(Muted).PaddingLeft(1)

	// status bar chips
	ChipHelp  = lipgloss.NewStyle().Background(Muted).Foreground(Darker).Bold(true).Padding(0, 1)
	ChipTok   = lipgloss.NewStyle().Background(TextColor).Foreground(Bg2).Padding(0, 1)
	ChipFill  = lipgloss.NewStyle().Background(Bg2)
	ChipModel = lipgloss.NewStyle().Background(Secondary).Foreground(Bg).Bold(true).Padding(0, 1)
	ChipInfo  = lipgloss.NewStyle().Background(InfoColor).Foreground(Bg).Padding(0, 1)
	ChipErr   = lipgloss.NewStyle().Background(ErrColor).Foreground(Bg).Padding(0, 1)

	// dialog
	DialogBorder = lipgloss.NewStyle().BorderStyle(lipgloss.RoundedBorder()).BorderForeground(Muted).Padding(1, 2).Background(Bg)
	DialogTitle  = lipgloss.NewStyle().Foreground(Primary).Bold(true)
	DialogSel    = lipgloss.NewStyle().Background(Primary).Foreground(Bg).Bold(true).Padding(0, 1)
	DialogItem   = lipgloss.NewStyle().Foreground(TextColor).Padding(0, 1)
	DialogDesc   = lipgloss.NewStyle().Foreground(Muted)

	// prompt
	PromptGlyph = lipgloss.NewStyle().Foreground(Primary).Bold(true).Render(" >")

	// slash completion popup
	StCompSel = lipgloss.NewStyle().Background(Primary).Foreground(Bg).Bold(true)
	StCompN   = lipgloss.NewStyle().Foreground(TextColor)
)

// Themes cycle Primary accent.
var Themes = []struct {
	Name    string
	Primary lipgloss.Color
}{
	{"opencode", lipgloss.Color("#fab283")},
	{"emerald", lipgloss.Color("#7fd88f")},
	{"violet", lipgloss.Color("#9d7cd8")},
	{"cyan", lipgloss.Color("#56b6c2")},
	{"amber", lipgloss.Color("#f5a742")},
}

func ApplyTheme(name string) {
	for _, t := range Themes {
		if t.Name == name {
			Primary = t.Primary
			StPrimary = lipgloss.NewStyle().Foreground(Primary)
			StPrimaryB = lipgloss.NewStyle().Foreground(Primary).Bold(true)
			BorderAI = lipgloss.NewStyle().BorderStyle(lipgloss.ThickBorder()).BorderLeft(true).BorderForeground(Primary).PaddingLeft(1)
			DialogTitle = lipgloss.NewStyle().Foreground(Primary).Bold(true)
			DialogSel = lipgloss.NewStyle().Background(Primary).Foreground(Bg).Bold(true).Padding(0, 1)
			PromptGlyph = lipgloss.NewStyle().Foreground(Primary).Bold(true).Render(" >")
			return
		}
	}
}
