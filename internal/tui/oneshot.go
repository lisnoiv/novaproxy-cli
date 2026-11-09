package tui

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/lisnoiv/novaproxy-cli/internal/api"
	"github.com/lisnoiv/novaproxy-cli/internal/config"
	"github.com/lisnoiv/novaproxy-cli/internal/mcp"
	"github.com/lisnoiv/novaproxy-cli/internal/tools"
)

// RunOneshot executes a single prompt without the interactive UI.
func RunOneshot(cfg *config.Config, client *api.Client, args Args, prompt string) int {
	mcpMgr := mcp.NewManager(cfg.MCPServers)
	if len(cfg.MCPServers) > 0 {
		mcpMgr.ConnectAll(nil)
		defer mcpMgr.CloseAll()
	}

	messages := []api.Message{{Role: "system", Content: strPtr(SystemPromptFor(cfg.Mode, cfg.SystemPrompt))}}
	expanded, _ := tools.ExpandAtRefs(prompt)
	messages = append(messages, api.Message{Role: "user", Content: strPtr(expanded)})

	toolsList := []api.Tool{}
	if true {
		disabled := map[string]bool{}
		for _, d := range cfg.DisabledTools {
			disabled[d] = true
		}
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
			toolsList = append(toolsList, t)
		}
		for _, s := range mcpMgr.OpenAITools() {
			fn := s["function"].(map[string]interface{})
			t := api.Tool{Type: "function"}
			t.Function.Name = fn["name"].(string)
			t.Function.Description = fn["description"].(string)
			t.Function.Parameters = fn["parameters"].(map[string]interface{})
			toolsList = append(toolsList, t)
		}
	}

	totalIn, totalOut := 0, 0
	start := time.Now()
	for round := 0; round < 8; round++ {
		var text strings.Builder
		toolCalls := map[int]*api.ToolCall{}
		var usage *api.Usage
		var streamErr error

		_, err := client.ChatStream(messages, currentModelOneshot(cfg, args), toolsList, args.ReasoningEffort, func(ev api.StreamEvent) {
			if ev.Done {
				return
			}
			if ev.Content != "" {
				text.WriteString(ev.Content)
				os.Stdout.WriteString(ev.Content)
			}
			for _, d := range ev.ToolCalls {
				tc := toolCalls[d.Index]
				if tc == nil {
					tc = &api.ToolCall{Type: "function"}
					toolCalls[d.Index] = tc
				}
				if d.ID != "" {
					tc.ID = d.ID
				}
				tc.Function.Name += d.Name
				tc.Function.Arguments += d.Arguments
			}
			if ev.Usage != nil {
				usage = ev.Usage
			}
		})
		if err != nil {
			fmt.Fprintln(os.Stderr, "ошибка API: "+err.Error())
			return 1
		}
		if streamErr != nil {
			fmt.Fprintln(os.Stderr, "ошибка API: "+streamErr.Error())
			return 1
		}
		if usage != nil {
			totalIn = usage.PromptTokens
			totalOut = usage.CompletionTokens
		}

		if len(toolCalls) == 0 {
			os.Stdout.WriteString("\n")
			if !args.Quiet {
				fmt.Fprintf(os.Stderr, "\n\x1b[38;2;106;106;106m— %s · %.1fs · in/out %d/%d\x1b[0m\n",
					currentModelOneshot(cfg, args), time.Since(start).Seconds(), totalIn, totalOut)
			}
			return 0
		}

		calls := []api.ToolCall{}
		for i := 0; i < len(toolCalls); i++ {
			if tc, ok := toolCalls[i]; ok {
				calls = append(calls, *tc)
			}
		}
		content := text.String()
		messages = append(messages, api.Message{Role: "assistant", Content: &content, ToolCalls: calls})
		for _, c := range calls {
			var res string
			if strings.HasPrefix(c.Function.Name, "mcp__") {
				res = mcpMgr.Dispatch(c.Function.Name, c.Function.Arguments)
			} else {
				res, _ = tools.Dispatch(c.Function.Name, c.Function.Arguments)
			}
			messages = append(messages, api.Message{Role: "tool", ToolCallID: c.ID, Content: &res})
		}
	}
	return 0
}

func currentModelOneshot(cfg *config.Config, args Args) string {
	if args.Model != "" {
		return args.Model
	}
	return cfg.Model
}
