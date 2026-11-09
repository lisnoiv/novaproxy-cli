package tui

import (
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/glamour/ansi"
	"github.com/charmbracelet/lipgloss"
)

func NewMarkdownRenderer(width int) (*glamour.TermRenderer, error) {
	style := glamourStyles()
	return glamour.NewTermRenderer(
		glamour.WithStyles(style),
		glamour.WithWordWrap(width),
		glamour.WithPreservedNewLines(),
	)
}

// glamourStyles returns a style config close to the OpenCode dark theme.
func glamourStyles() ansi.StyleConfig {
	trueColor := func(c lipgloss.Color) *string { s := string(c); return &s }
	return ansi.StyleConfig{
		Document: ansi.StyleBlock{
			StylePrimitive: ansi.StylePrimitive{Color: trueColor(TextColor)},
			Margin:         uintPtr(0),
		},
		Heading: ansi.StyleBlock{
			StylePrimitive: ansi.StylePrimitive{Color: trueColor(Secondary), Bold: boolPtr(true)},
		},
		H1: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Prefix: "# ", Color: trueColor(Secondary), Bold: boolPtr(true)}},
		H2: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Prefix: "## ", Color: trueColor(Secondary), Bold: boolPtr(true)}},
		H3: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Prefix: "### ", Color: trueColor(Secondary), Bold: boolPtr(true)}},
		Strong:     ansi.StylePrimitive{Color: trueColor(Accent), Bold: boolPtr(true)},
		Emph:       ansi.StylePrimitive{Color: trueColor(Emph), Italic: boolPtr(true)},
		Code:       ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: trueColor(OKColor)}},
		CodeBlock:  ansi.StyleCodeBlock{StyleBlock: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: trueColor(TextColor)}, Margin: uintPtr(1)}},
		BlockQuote: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: trueColor(Emph), Italic: boolPtr(true)}, Indent: uintPtr(1)},
		List:       ansi.StyleList{StyleBlock: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: trueColor(TextColor)}}, LevelIndent: 2},
		Item:       ansi.StylePrimitive{BlockPrefix: "• "},
		Link:       ansi.StylePrimitive{Color: trueColor(InfoColor), Underline: boolPtr(true)},
		LinkText:   ansi.StylePrimitive{Color: trueColor(Primary), Bold: boolPtr(true)},
		HorizontalRule: ansi.StylePrimitive{Color: trueColor(Muted), Format: "\n─────────────────────────────────────────\n"},
		Table:      ansi.StyleTable{CenterSeparator: stringPtr("┼"), ColumnSeparator: stringPtr("│"), RowSeparator: stringPtr("─")},
	}
}

func uintPtr(u uint) *uint    { return &u }
func boolPtr(b bool) *bool    { return &b }
func stringPtr(s string) *string { return &s }
