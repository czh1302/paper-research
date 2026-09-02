import Editor, { DiffEditor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import { useEffect, useMemo } from "react";
import { useLanguage } from "../lib/language";
import { useTheme } from "../lib/theme";

loader.config({ monaco });

function languageForPath(path: string) {
  const extension = path.split(".").pop()?.toLowerCase();
  return ({
    css: "css", go: "go", html: "html", java: "java", js: "javascript", json: "json",
    jsx: "javascript", md: "markdown", py: "python", rs: "rust", sh: "shell", sql: "sql",
    ts: "typescript", tsx: "typescript", yaml: "yaml", yml: "yaml",
  } as Record<string, string>)[extension ?? ""] ?? "plaintext";
}

export function ExperimentEditor({
  path,
  value,
  originalValue,
  readOnly,
  diffMode,
  onChange,
}: {
  path: string;
  value: string;
  originalValue?: string;
  readOnly: boolean;
  diffMode: boolean;
  onChange: (value: string) => void;
}) {
  const { text } = useLanguage();
  const { theme } = useTheme();
  const language = useMemo(() => languageForPath(path), [path]);
  useEffect(() => {
    monaco.editor.defineTheme("research-atlas-light", {
      base: "vs",
      inherit: true,
      rules: [],
      colors: {
        "focusBorder": "#0F8F80",
        "foreground": "#142235",
        "descriptionForeground": "#66758A",
        "input.background": "#F6F8FB",
        "input.border": "#DCE5EC",
        "input.foreground": "#142235",
        "input.placeholderForeground": "#8090A2",
        "inputOption.activeBorder": "#0F8F80",
        "list.activeSelectionBackground": "#C8EAE5",
        "list.activeSelectionForeground": "#142235",
        "list.focusBackground": "#E4F3F0",
        "list.focusForeground": "#142235",
        "list.focusOutline": "#0F8F80",
        "list.hoverBackground": "#EEF3F6",
        "list.hoverForeground": "#142235",
        "scrollbar.shadow": "#173B5718",
        "scrollbarSlider.background": "#8090A240",
        "scrollbarSlider.hoverBackground": "#66758A66",
        "scrollbarSlider.activeBackground": "#0F8F8080",
        "editor.background": "#FFFFFF",
        "editor.foreground": "#142235",
        "editorLineNumber.foreground": "#8090A2",
        "editorLineNumber.activeForeground": "#0B766E",
        "editor.selectionBackground": "#C8EAE5",
        "editor.inactiveSelectionBackground": "#E4F3F0",
        "editorCursor.foreground": "#0F8F80",
        "editorIndentGuide.background1": "#DCE5EC",
        "editorIndentGuide.activeBackground1": "#8090A2",
        "editor.findMatchBackground": "#F4B86066",
        "editor.findMatchBorder": "#A85B0B",
        "editor.findMatchHighlightBackground": "#F4B86033",
        "editor.findMatchHighlightBorder": "#A85B0B66",
        "editorBracketMatch.background": "#C8EAE580",
        "editorBracketMatch.border": "#0F8F80",
        "editorWidget.background": "#FFFFFF",
        "editorWidget.border": "#DCE5EC",
        "editorWidget.foreground": "#142235",
        "editorHoverWidget.background": "#FFFFFF",
        "editorHoverWidget.border": "#DCE5EC",
        "editorHoverWidget.foreground": "#142235",
        "editorHoverWidget.highlightForeground": "#0B766E",
        "editorSuggestWidget.background": "#FFFFFF",
        "editorSuggestWidget.border": "#DCE5EC",
        "editorSuggestWidget.foreground": "#142235",
        "editorSuggestWidget.highlightForeground": "#0B766E",
        "editorSuggestWidget.selectedBackground": "#E4F3F0",
        "diffEditor.insertedLineBackground": "#0F8F8014",
        "diffEditor.insertedTextBackground": "#0F8F8033",
        "diffEditor.removedLineBackground": "#B91C1C0F",
        "diffEditor.removedTextBackground": "#B91C1C2B",
      },
    });
    monaco.editor.defineTheme("research-atlas-dark", {
      base: "vs-dark",
      inherit: true,
      rules: [],
      colors: {
        "focusBorder": "#49C5B3",
        "foreground": "#ECF3F7",
        "descriptionForeground": "#A9B8C5",
        "input.background": "#152131",
        "input.border": "#354B61",
        "input.foreground": "#ECF3F7",
        "input.placeholderForeground": "#7D93A5",
        "inputOption.activeBorder": "#49C5B3",
        "list.activeSelectionBackground": "#2B5362",
        "list.activeSelectionForeground": "#ECF3F7",
        "list.focusBackground": "#25364A",
        "list.focusForeground": "#ECF3F7",
        "list.focusOutline": "#49C5B3",
        "list.hoverBackground": "#25364A",
        "list.hoverForeground": "#ECF3F7",
        "scrollbar.shadow": "#040B143D",
        "scrollbarSlider.background": "#7D93A547",
        "scrollbarSlider.hoverBackground": "#A9B8C566",
        "scrollbarSlider.activeBackground": "#49C5B380",
        "editor.background": "#1D2C3F",
        "editor.foreground": "#ECF3F7",
        "editorLineNumber.foreground": "#7D93A5",
        "editorLineNumber.activeForeground": "#49C5B3",
        "editor.selectionBackground": "#2B5362",
        "editor.inactiveSelectionBackground": "#25364A",
        "editorCursor.foreground": "#49C5B3",
        "editorIndentGuide.background1": "#354B61",
        "editorIndentGuide.activeBackground1": "#7D93A5",
        "editor.findMatchBackground": "#F4B86059",
        "editor.findMatchBorder": "#F4B860",
        "editor.findMatchHighlightBackground": "#F4B8602E",
        "editor.findMatchHighlightBorder": "#F4B86080",
        "editorBracketMatch.background": "#2B536280",
        "editorBracketMatch.border": "#49C5B3",
        "editorWidget.background": "#1D2C3F",
        "editorWidget.border": "#354B61",
        "editorWidget.foreground": "#ECF3F7",
        "editorHoverWidget.background": "#1D2C3F",
        "editorHoverWidget.border": "#354B61",
        "editorHoverWidget.foreground": "#ECF3F7",
        "editorHoverWidget.highlightForeground": "#49C5B3",
        "editorSuggestWidget.background": "#1D2C3F",
        "editorSuggestWidget.border": "#354B61",
        "editorSuggestWidget.foreground": "#ECF3F7",
        "editorSuggestWidget.highlightForeground": "#49C5B3",
        "editorSuggestWidget.selectedBackground": "#2B5362",
        "diffEditor.insertedLineBackground": "#49C5B314",
        "diffEditor.insertedTextBackground": "#49C5B32E",
        "diffEditor.removedLineBackground": "#F8717114",
        "diffEditor.removedTextBackground": "#F8717133",
      },
    });
  }, []);
  const editorTheme = theme === "dark" ? "research-atlas-dark" : "research-atlas-light";
  const common = {
    automaticLayout: true,
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    fontSize: 14,
    lineHeight: 22,
    minimap: { enabled: true },
    padding: { top: 12 },
    readOnly,
    renderWhitespace: "selection" as const,
    scrollBeyondLastLine: false,
    smoothScrolling: true,
    wordWrap: "on" as const,
  };
  if (diffMode && originalValue !== undefined) {
    return <DiffEditor
      height="100%"
      language={language}
      original={originalValue}
      modified={value}
      originalModelPath={`file:///baseline/${path}`}
      modifiedModelPath={`file:///workspace/${path}`}
      theme={editorTheme}
      loading={<div className="experiment-empty">{text("正在载入差异…", "Loading diff…")}</div>}
      options={{ ...common, originalEditable: false, renderSideBySide: true }}
    />;
  }
  return <Editor
    height="100%"
    language={language}
    path={`file:///workspace/${path}`}
    value={value}
    theme={editorTheme}
    loading={<div className="experiment-empty">{text("正在载入编辑器…", "Loading editor…")}</div>}
    onChange={(next) => onChange(next ?? "")}
    options={common}
  />;
}
