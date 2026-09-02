import {
  ArrowLeft,
  BarChart3,
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  Download,
  File,
  FilePlus2,
  Folder,
  FolderOpen,
  LoaderCircle,
  Play,
  RotateCcw,
  Send,
  Square,
  SquareTerminal,
  Trash2,
  X,
} from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ExperimentEditor } from "../components/ExperimentEditor";
import { ExperimentTerminal } from "../components/ExperimentTerminal";
import {
  cancelExperiment,
  deleteExperiment,
  deleteExperimentFile,
  downloadExperimentRepository,
  getExperimentArtifact,
  getExperimentWorkspace,
  moveExperimentFile,
  readExperimentFile,
  saveExperimentFile,
  submitExperimentAction,
  subscribeToExperiment,
} from "../lib/api";
import { useLanguage } from "../lib/language";
import type {
  ExperimentAction,
  ExperimentArtifact,
  ExperimentFileContent,
  ExperimentFileEntry,
  ExperimentOutcome,
  ExperimentStage,
  ExperimentStatus,
  ExperimentWorkspace,
} from "../lib/types";

type MobilePane = "files" | "editor" | "terminal" | "assistant";
type BottomPane = "terminal" | "results";
type OpenDocument = ExperimentFileContent & { savedContent: string; error?: string };
type FileTreeNode = { name: string; path: string; type: "file" | "directory"; children: FileTreeNode[] };

const activeStatuses: ExperimentStatus[] = ["queued", "running", "recovering", "waiting_resources"];
const LOCAL_DRAFT_DELAY_MS = 800;
const REVISION_IDLE_DELAY_MS = 5000;

function workspaceDraftKey(experimentId: string, path: string) {
  return `research-atlas:experiment-draft:${experimentId}:${encodeURIComponent(path)}`;
}

function storeWorkspaceDraft(experimentId: string, document: OpenDocument) {
  try {
    window.localStorage.setItem(workspaceDraftKey(experimentId, document.path), JSON.stringify({
      content: document.content,
      sha256: document.sha256,
    }));
  } catch {
    // The editor remains usable when private browsing or storage quotas block drafts.
  }
}

function clearWorkspaceDraft(experimentId: string, path: string) {
  try { window.localStorage.removeItem(workspaceDraftKey(experimentId, path)); } catch { /* no-op */ }
}

function restoreWorkspaceDraft(experimentId: string, file: ExperimentFileContent): string | undefined {
  try {
    const raw = window.localStorage.getItem(workspaceDraftKey(experimentId, file.path));
    if (!raw) return undefined;
    const draft = JSON.parse(raw) as { content?: unknown; sha256?: unknown };
    if (draft.sha256 === file.sha256 && typeof draft.content === "string") return draft.content;
    clearWorkspaceDraft(experimentId, file.path);
  } catch {
    clearWorkspaceDraft(experimentId, file.path);
  }
  return undefined;
}

function useCompactWorkspace() {
  const [compact, setCompact] = useState(() => typeof window !== "undefined" && window.matchMedia?.("(max-width: 900px)").matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 900px)");
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return compact;
}

function buildTree(entries: ExperimentFileEntry[]): FileTreeNode[] {
  const root: FileTreeNode = { name: "", path: "", type: "directory", children: [] };
  const nodes = new Map<string, FileTreeNode>([["", root]]);
  const ensureDirectory = (path: string) => {
    if (nodes.has(path)) return nodes.get(path)!;
    const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const parent = ensureDirectory(parentPath);
    const node: FileTreeNode = { name: path.split("/").pop() ?? path, path, type: "directory", children: [] };
    parent.children.push(node); nodes.set(path, node); return node;
  };
  for (const entry of [...entries].sort((left, right) => left.path.localeCompare(right.path))) {
    const cleanPath = entry.path.replace(/^\/+|\/+$/g, "");
    if (!cleanPath) continue;
    if (entry.type === "directory") { ensureDirectory(cleanPath); continue; }
    const parentPath = cleanPath.includes("/") ? cleanPath.slice(0, cleanPath.lastIndexOf("/")) : "";
    const parent = ensureDirectory(parentPath);
    if (!nodes.has(cleanPath)) {
      const node: FileTreeNode = { name: cleanPath.split("/").pop() ?? cleanPath, path: cleanPath, type: "file", children: [] };
      parent.children.push(node); nodes.set(cleanPath, node);
    }
  }
  const sort = (items: FileTreeNode[]) => items.sort((left, right) => left.type === right.type ? left.name.localeCompare(right.name) : left.type === "directory" ? -1 : 1).forEach((item) => sort(item.children));
  sort(root.children);
  return root.children;
}

function FileTree({ nodes, selectedPath, expanded, onToggle, onOpen }: { nodes: FileTreeNode[]; selectedPath: string; expanded: Set<string>; onToggle: (path: string) => void; onOpen: (path: string) => void }) {
  return <div className="experiment-file-tree">{nodes.map((node) => node.type === "directory" ? <div key={node.path}>
    <button className="experiment-tree-row" type="button" onClick={() => onToggle(node.path)}><span className="experiment-tree-chevron">{expanded.has(node.path) ? <ChevronDown/> : <ChevronRight/>}</span>{expanded.has(node.path) ? <FolderOpen/> : <Folder/>}<span>{node.name}</span></button>
    {expanded.has(node.path) && <div className="experiment-tree-children"><FileTree nodes={node.children} selectedPath={selectedPath} expanded={expanded} onToggle={onToggle} onOpen={onOpen}/></div>}
  </div> : <button className={`experiment-tree-row is-file ${selectedPath === node.path ? "active" : ""}`} type="button" key={node.path} onClick={() => onOpen(node.path)}><span className="experiment-tree-chevron"/><File/><span>{node.name}</span></button>)}</div>;
}

function statusLabel(status: ExperimentStatus, text: (zh: string, en: string) => string) {
  return ({ queued: text("等待实验资源", "Queued"), running: text("实验进行中", "Running"), recovering: text("自动恢复中", "Recovering"), waiting_resources: text("等待资源恢复", "Waiting for resources"), ready: text("工作区可用", "Workspace ready"), cancelled: text("已取消", "Cancelled") } as Record<ExperimentStatus, string>)[status];
}

function stageLabel(stage: ExperimentStage, text: (zh: string, en: string) => string) {
  return ({ spec_freeze: text("冻结实验规范", "Freezing specification"), repo_generation: text("生成代码仓库", "Generating repository"), environment_setup: text("配置实验环境", "Preparing environment"), baseline: text("运行 Baseline", "Running baseline"), intervention: text("运行核心改动", "Running intervention"), evaluation: text("计算实验指标", "Evaluating metrics"), repair: text("自动修复实现", "Repairing implementation"), archive: text("归档代码与结果", "Archiving results"), interactive: text("交互工作区", "Interactive workspace") } as Record<ExperimentStage, string>)[stage];
}

function outcomeLabel(outcome: ExperimentOutcome, text: (zh: string, en: string) => string) {
  return ({ pending: text("等待结论", "Pending"), initial_support: text("初步支持", "Initially supported"), not_support: text("暂不支持", "Not supported"), inconclusive: text("暂无法判断", "Inconclusive"), environment_blocked: text("环境受阻", "Environment blocked"), resource_limited: text("资源受限", "Resource limited"), budget_blocked: text("预算受限", "Budget limited"), cancelled: text("已取消", "Cancelled") } as Record<ExperimentOutcome, string>)[outcome];
}

function RepositoryPane({ workspace, selectedPath, onOpen, onRefresh, onBeforeMutation, onMove, onDelete }: {
  workspace: ExperimentWorkspace;
  selectedPath: string;
  onOpen: (path: string) => void;
  onRefresh: () => Promise<void>;
  onBeforeMutation: () => Promise<boolean>;
  onMove: (fromPath: string, toPath: string, files: ExperimentFileEntry[], revisionId?: string) => void;
  onDelete: (path: string, files: ExperimentFileEntry[], revisionId?: string) => void;
}) {
  const { text } = useLanguage();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(workspace.files.filter((item) => item.type === "directory").map((item) => item.path)));
  const [mutating, setMutating] = useState(false);
  const [mutationMessage, setMutationMessage] = useState("");
  const tree = useMemo(() => buildTree(workspace.files), [workspace.files]);
  const permissions = workspace.permissions;
  async function runMutation(operation: () => Promise<void>) {
    if (mutating) return;
    setMutating(true); setMutationMessage("");
    try { await operation(); }
    catch { setMutationMessage(text("文件操作尚未完成；当前代码已保留，可以稍后安全重试。", "The file operation has not completed. Your code is preserved and you can retry safely.")); }
    finally { setMutating(false); }
  }
  async function createFile() {
    const path = window.prompt(text("新文件路径", "New file path"), "src/new_file.py")?.trim().replace(/^\/+/, "");
    if (!path) return;
    if (!await onBeforeMutation()) return;
    await saveExperimentFile(workspace.experiment.id, path, "");
    onOpen(path); await onRefresh();
  }
  async function renameFile() {
    if (!selectedPath) return;
    const toPath = window.prompt(text("新的文件路径", "New file path"), selectedPath)?.trim().replace(/^\/+/, "");
    if (!toPath || toPath === selectedPath) return;
    if (!await onBeforeMutation()) return;
    const result = await moveExperimentFile(workspace.experiment.id, selectedPath, toPath);
    onMove(selectedPath, toPath, result.files, result.revision?.id);
    await onRefresh();
  }
  async function removeFile() {
    if (!selectedPath || !window.confirm(text(`永久删除 ${selectedPath}？`, `Permanently delete ${selectedPath}?`))) return;
    if (!await onBeforeMutation()) return;
    const result = await deleteExperimentFile(workspace.experiment.id, selectedPath);
    onDelete(selectedPath, result.files, result.revision?.id);
    await onRefresh();
  }
  return <section className="experiment-pane experiment-repository" aria-label={text("代码仓库", "Code repository")}>
    <header className="experiment-pane-heading"><div><strong>{text("代码仓库", "Repository")}</strong><span>{workspace.files.filter((item) => item.type === "file").length} {text("个文件", "files")}</span></div>{permissions.editCode && <div className="experiment-icon-actions"><button disabled={mutating} onClick={() => void runMutation(createFile)} title={text("新建文件", "New file")} aria-label={text("新建文件", "New file")}><FilePlus2/></button><button disabled={mutating || !selectedPath} onClick={() => void runMutation(renameFile)} title={text("重命名", "Rename")} aria-label={text("重命名文件", "Rename file")}><Code2/></button><button disabled={mutating || !selectedPath} onClick={() => void runMutation(removeFile)} title={text("删除", "Delete")} aria-label={text("删除文件", "Delete file")}><Trash2/></button></div>}</header>
    {mutationMessage && <div className="experiment-readonly-note" role="status">{mutationMessage}</div>}
    <div className="experiment-pane-scroll">{tree.length ? <FileTree nodes={tree} selectedPath={selectedPath} expanded={expanded} onToggle={(path) => setExpanded((current) => { const next = new Set(current); next.has(path) ? next.delete(path) : next.add(path); return next; })} onOpen={onOpen}/> : <div className="experiment-empty">{text("代码仓库正在生成，文件会自动出现在这里。", "The repository is being generated. Files will appear here automatically.")}</div>}</div>
  </section>;
}

function ResultsPane({ workspace }: { workspace: ExperimentWorkspace }) {
  const { language, text, formatDate } = useLanguage();
  const [artifactMessage, setArtifactMessage] = useState("");
  const manualRuns = workspace.runs.filter((run) => run.kind === "manual");
  const manualRunNumbers = new Map(manualRuns.map((run, index) => [run.id, manualRuns.length - index]));
  async function openArtifact(artifact: ExperimentArtifact) {
    // Reserve the tab during the click gesture. Opening only after the signed-URL
    // request resolves is blocked by several browsers as an unsolicited popup.
    const target = window.open("about:blank", "_blank");
    if (target) target.opener = null;
    setArtifactMessage("");
    try {
      const result = await getExperimentArtifact(workspace.experiment.id, artifact.id);
      if (target) target.location.replace(result.signedUrl);
      else window.open(result.signedUrl, "_blank", "noopener,noreferrer");
    } catch {
      target?.close();
      setArtifactMessage(text("实验产物正在准备中，请稍后重试。", "The experiment artifact is being prepared. Try again shortly."));
    }
  }
  return <section className="experiment-results experiment-pane-scroll" aria-label={text("实验结果", "Experiment results")}>
    {workspace.runs.length === 0 ? <div className="experiment-empty"><BarChart3/><strong>{text("实验结果尚未生成", "No experiment result yet")}</strong><span>{text("运行进度和最终指标会自动显示在这里。", "Progress and final metrics will appear here automatically.")}</span></div> : workspace.runs.map((run) => <article className="experiment-run" key={run.id}>
      <div className="experiment-run-heading"><div><strong>{run.kind === "automatic" ? text("自动首轮验证", "Automatic baseline validation") : text(`手动验证 ${manualRunNumbers.get(run.id) ?? 1}`, `Manual validation ${manualRunNumbers.get(run.id) ?? 1}`)}</strong><span>{formatDate(run.createdAt)}</span></div><span className={`experiment-outcome is-${run.outcome}`}>{outcomeLabel(run.outcome, text)}</span></div>
      {(language === "zh" ? run.summaryZh : run.summaryEn) && <p>{language === "zh" ? run.summaryZh : run.summaryEn}</p>}
      <dl className="experiment-metrics">{Object.entries(run.metrics ?? {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>
    </article>)}
    {workspace.artifacts.length > 0 && <div className="experiment-artifacts"><strong>{text("实验产物", "Artifacts")}</strong>{artifactMessage && <div className="experiment-readonly-note" role="status">{artifactMessage}</div>}{workspace.artifacts.map((artifact) => <button type="button" key={artifact.id} onClick={() => void openArtifact(artifact)}><File/><span>{artifact.name}</span><small>{artifact.kind}</small></button>)}</div>}
  </section>;
}

function OutputPane({
  workspace,
  activePane,
  onPaneChange,
  terminal,
}: {
  workspace: ExperimentWorkspace;
  activePane: BottomPane;
  onPaneChange: (pane: BottomPane) => void;
  terminal: ReactNode;
}) {
  const { text } = useLanguage();
  return <section className="experiment-pane experiment-output">
    <nav aria-label={text("运行输出", "Run output")}>
      <button className={activePane === "terminal" ? "active" : ""} onClick={() => onPaneChange("terminal")}><SquareTerminal/>{text("终端", "Terminal")}</button>
      <button className={activePane === "results" ? "active" : ""} onClick={() => onPaneChange("results")}><BarChart3/>{text("实验结果", "Results")}</button>
    </nav>
    <div className="experiment-output-content">
      <div className="experiment-output-view" hidden={activePane !== "terminal"}>{terminal}</div>
      <div className="experiment-output-view" hidden={activePane !== "results"}><ResultsPane workspace={workspace}/></div>
    </div>
  </section>;
}

function assistantFeed(actions: ExperimentAction[]): ExperimentAction[] {
  return actions
    .filter((item) => item.kind === "assistant" || (item.role && item.kind === "system"))
    .flatMap((action): ExperimentAction[] => {
      if (action.kind !== "assistant" || action.role !== "user") return [action];
      const request = action.prompt ? [{
        ...action,
        id: action.id.endsWith(":request") ? action.id : `${action.id}:request`,
        content: null,
        role: "user" as const,
      }] : [];
      const response = action.content || action.state === "running" ? [{
        ...action,
        content: action.content ?? null,
        prompt: null,
        role: "assistant" as const,
      }] : [];
      return [...request, ...response];
    });
}

function AssistantActionDetails({ action, canRollback, onOpenFile, onRollback }: {
  action: ExperimentAction;
  canRollback: boolean;
  onOpenFile: (path: string) => void;
  onRollback: (revisionId: string) => Promise<void>;
}) {
  const { text } = useLanguage();
  const [rollingBack, setRollingBack] = useState(false);
  const modifiedFiles = action.modifiedFiles ?? [];
  const deletedFiles = action.deletedFiles ?? [];
  const commandResults = action.commandResults ?? [];
  const hasAudit = modifiedFiles.length > 0 || deletedFiles.length > 0 || commandResults.length > 0 || Boolean(action.revisionIdAfter);
  if (!hasAudit || action.role !== "assistant") return null;
  async function rollback() {
    if (!action.revisionIdAfter || rollingBack || !canRollback) return;
    setRollingBack(true);
    try { await onRollback(action.revisionIdAfter); }
    finally { setRollingBack(false); }
  }
  return <div className="experiment-action-audit" aria-label={text("本次操作摘要", "Action summary")}>
    {modifiedFiles.length > 0 && <section>
      <strong><File/>{text("修改文件", "Changed files")}</strong>
      <ul>{modifiedFiles.map((path) => <li key={path}><button type="button" onClick={() => onOpenFile(path)}>{path}</button></li>)}</ul>
    </section>}
    {deletedFiles.length > 0 && <section>
      <strong><Trash2/>{text("删除文件", "Deleted files")}</strong>
      <ul>{deletedFiles.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
    </section>}
    {commandResults.length > 0 && <section>
      <strong><SquareTerminal/>{text("命令与结果", "Commands and results")}</strong>
      <div className="experiment-action-commands">{commandResults.map((result, index) => {
        const succeeded = result.exitCode === 0;
        const status = result.exitCode === null || result.exitCode === undefined
          ? text("已执行", "Executed") : succeeded ? text("运行成功", "Succeeded") : text(`退出码 ${result.exitCode}`, `Exit ${result.exitCode}`);
        return <div key={`${result.command}-${index}`}>
          <code>{result.command}</code>
          <span data-result={succeeded ? "success" : result.exitCode === null || result.exitCode === undefined ? "neutral" : "error"}>{status}{typeof result.elapsedSeconds === "number" ? ` · ${result.elapsedSeconds.toFixed(1)}s` : ""}</span>
          {result.resultSummary && <pre>{result.resultSummary}</pre>}
        </div>;
      })}</div>
    </section>}
    {action.revisionIdAfter && <footer>
      <span>{text("已保存为独立版本", "Saved as a revision")}</span>
      {canRollback && <button type="button" disabled={rollingBack} onClick={() => void rollback()}><RotateCcw/>{rollingBack ? text("正在回滚…", "Rolling back…") : text("回滚到此版本", "Roll back to this revision")}</button>}
    </footer>}
  </div>;
}

function AssistantPane({ workspace, onSubmit, onAction, onOpenFile, onRollback, rollbackBusy }: {
  workspace: ExperimentWorkspace;
  onSubmit: (message: string) => Promise<ExperimentAction>;
  onAction: (action: ExperimentAction) => void;
  onOpenFile: (path: string) => void;
  onRollback: (revisionId: string) => Promise<void>;
  rollbackBusy: boolean;
}) {
  const { text } = useLanguage();
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const adminReadOnly = workspace.accessMode === "admin";
  const actions = adminReadOnly ? [] : assistantFeed(workspace.actions);
  async function submit() {
    const message = prompt.trim();
    if (!message || submitting || !workspace.permissions.chat) return;
    setSubmitting(true); setSubmitError("");
    try {
      const action = await onSubmit(message);
      setPrompt(""); onAction(action);
    } catch {
      setSubmitError(text("修改已保留在编辑器中；保存完成后可重新发送。", "Your edits remain in the editor. Send again after they are saved."));
    } finally { setSubmitting(false); }
  }
  return <section className="experiment-pane experiment-assistant" aria-label={text("Flash 编程助手", "Flash coding assistant")}>
    <header className="experiment-pane-heading"><div><strong className="flex items-center gap-2"><Bot/>{text("Flash 编程助手", "Flash coding assistant")}</strong><span>{text("通过 Claude Code 安全执行", "Safely executed through Claude Code")}</span></div></header>
    <div className="experiment-chat experiment-pane-scroll">{adminReadOnly ? <div className="experiment-empty"><Bot/><strong>{text("助手对话保持私密", "Assistant conversations stay private")}</strong><span>{text("管理员可以查看代码、终端输出和实验结果，但不能查看实验所有者与助手的对话。", "Administrators can inspect code, terminal output, and experiment results, but cannot read the owner's assistant conversations.")}</span></div> : actions.length ? actions.map((action) => <article className={`experiment-message is-${action.role ?? "assistant"}`} key={action.id}><span>{action.role === "user" ? text("你", "You") : "Flash"}</span><p>{action.content || action.prompt || (action.state === "running" ? text("正在处理并验证修改…", "Applying and validating changes…") : text("请求已加入队列", "Request queued"))}</p>{action.command && <code>{action.command}</code>}<AssistantActionDetails action={action} canRollback={workspace.permissions.rollback && !rollbackBusy && action.state === "completed"} onOpenFile={onOpenFile} onRollback={onRollback}/></article>) : <div className="experiment-empty"><Bot/><strong>{text("从一个明确目标开始", "Start with a clear goal")}</strong><span>{text("让 Flash 修改代码、运行测试或解释实验结果。每次操作都会保留 Diff 和回滚点。", "Ask Flash to edit code, run tests, or explain results. Every action keeps a diff and rollback point.")}</span></div>}</div>
    {workspace.permissions.chat ? <>{submitError && <div className="experiment-readonly-note" role="status">{submitError}</div>}<div className="experiment-composer"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={text("描述你想修改或验证的内容…", "Describe what you want to change or validate…")} aria-label={text("发送给 Flash", "Message Flash")}/><button disabled={!prompt.trim() || submitting} onClick={() => void submit()} aria-label={text("发送", "Send")}>{submitting ? <LoaderCircle className="animate-spin"/> : <Send/>}</button></div></> : <div className="experiment-readonly-note">{text("管理员只能查看代码、终端输出和实验结果；助手对话仅对实验所有者可见。", "Administrators can inspect code, terminal output, and results; assistant conversations are visible only to the experiment owner.")}</div>}
  </section>;
}

function EditorPane({ path, openPaths, document, canEdit, saving, diffMode, setDiffMode, onChange, onSelect, onClose }: { path: string; openPaths: string[]; document?: OpenDocument; canEdit: boolean; saving: boolean; diffMode: boolean; setDiffMode: (value: boolean) => void; onChange: (value: string) => void; onSelect: (path: string) => void; onClose: (path: string) => void }) {
  const { text } = useLanguage();
  if (!path) return <div className="experiment-empty"><Code2/><strong>{text("选择一个文件开始阅读", "Choose a file to begin")}</strong><span>{text("代码生成后，可在左侧仓库中打开文件。", "Open a file from the repository once code has been generated.")}</span></div>;
  return <section className="experiment-editor-shell">
    <header className="experiment-editor-tabs">
      <div className="experiment-editor-tab-list" role="tablist" aria-label={text("已打开文件", "Open files")}>{openPaths.map((openPath) => {
        const openDocument = openPath === path ? document : undefined;
        const dirty = openDocument && openDocument.content !== openDocument.savedContent;
        return <div className={`experiment-editor-tab ${openPath === path ? "active" : ""}`} key={openPath}>
          <button type="button" role="tab" aria-selected={openPath === path} onClick={() => onSelect(openPath)}><File/><span>{openPath.split("/").pop()}</span>{dirty && <i aria-label={text("未保存", "Unsaved")}/>}</button>
          <button type="button" onClick={() => onClose(openPath)} aria-label={text(`关闭 ${openPath}`, `Close ${openPath}`)}><X/></button>
        </div>;
      })}</div>
      <div className="experiment-editor-tab-actions">{saving ? <small>{text("保存中…", "Saving…")}</small> : document && document.content !== document.savedContent ? <small>{text("未保存", "Unsaved")}</small> : <small>{text("已保存", "Saved")}</small>}{document?.originalContent !== undefined && <button className={diffMode ? "active" : ""} onClick={() => setDiffMode(!diffMode)}>{diffMode ? text("返回代码", "Code") : "Diff"}</button>}</div>
    </header>
    {document?.error ? <div className="experiment-empty"><strong>{text("无法打开文件", "Could not open file")}</strong><span>{document.error}</span></div> : document ? <ExperimentEditor path={path} value={document.content} originalValue={document.originalContent} readOnly={!canEdit} diffMode={diffMode} onChange={onChange}/> : <div className="experiment-empty"><LoaderCircle className="animate-spin"/><span>{text("正在读取文件…", "Reading file…")}</span></div>}
  </section>;
}

export function ExperimentWorkspacePage({ adminMode = false }: { adminMode?: boolean }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { language, text } = useLanguage();
  const compact = useCompactWorkspace();
  const [workspace, setWorkspace] = useState<ExperimentWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [openPaths, setOpenPaths] = useState<string[]>([]);
  const [documents, setDocuments] = useState<Record<string, OpenDocument>>({});
  const documentsRef = useRef(documents);
  const workspaceRef = useRef<ExperimentWorkspace | null>(null);
  const saveQueueRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const [savingPaths, setSavingPaths] = useState<Set<string>>(new Set());
  const [diffMode, setDiffMode] = useState(false);
  const [bottomPane, setBottomPane] = useState<BottomPane>("terminal");
  const [mobilePane, setMobilePane] = useState<MobilePane>("editor");
  const [selectedRevision, setSelectedRevision] = useState("");
  const [working, setWorking] = useState("");
  const [pageMessage, setPageMessage] = useState("");
  const refresh = useCallback(async () => {
    const next = await getExperimentWorkspace(id);
    workspaceRef.current = next; setWorkspace(next); setError("");
    setSelectedRevision((current) => current || next.experiment.currentRevisionId || next.revisions[0]?.id || "");
  }, [id]);
  useEffect(() => { documentsRef.current = documents; }, [documents]);
  useEffect(() => { workspaceRef.current = workspace; }, [workspace]);
  useEffect(() => { let active = true; setLoading(true); void refresh().catch(() => active && setError(text("实验工作区正在从最近检查点恢复。", "The experiment workspace is recovering from its latest checkpoint."))).finally(() => active && setLoading(false)); return () => { active = false; }; }, [refresh, text]);
  useEffect(() => {
    if (!error || workspace) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [error, refresh, workspace]);
  useEffect(() => {
    if (!workspace) return;
    const unsubscribe = subscribeToExperiment(id, () => void refresh().catch(() => undefined));
    const timer = activeStatuses.includes(workspace.experiment.status) ? window.setInterval(() => void refresh().catch(() => undefined), 5000) : undefined;
    return () => { unsubscribe(); if (timer) window.clearInterval(timer); };
  }, [id, refresh, workspace?.experiment.status]);

  const persistDocument = useCallback((path: string): Promise<boolean> => {
    const operation = saveQueueRef.current.then(async () => {
      const current = documentsRef.current[path];
      if (!current || current.content === current.savedContent) return true;
      const currentWorkspace = workspaceRef.current;
      if (adminMode || !currentWorkspace?.permissions.editCode) return false;
      const sentContent = current.content;
      setSavingPaths((paths) => new Set(paths).add(path));
      try {
        const result = await saveExperimentFile(id, path, sentContent, {
          expectedSha256: current.sha256,
          baseRevisionId: current.revisionId ?? currentWorkspace.experiment.currentRevisionId,
        });
        const revisionId = result.revision?.id ?? current.revisionId ?? currentWorkspace.experiment.currentRevisionId ?? null;
        const nextDocuments = Object.fromEntries(Object.entries(documentsRef.current).map(([itemPath, item]) => [itemPath, {
          ...item,
          revisionId,
          ...(itemPath === path ? {
            sha256: result.file.sha256,
            savedContent: item.content === sentContent ? sentContent : item.savedContent,
            error: undefined,
          } : {}),
        }])) as Record<string, OpenDocument>;
        documentsRef.current = nextDocuments;
        setDocuments(nextDocuments);
        clearWorkspaceDraft(id, path);
        if (revisionId) {
          setWorkspace((value) => {
            if (!value) return value;
            const next = {
              ...value,
              experiment: { ...value.experiment, currentRevisionId: revisionId },
              files: value.files.map((file) => file.path === path ? { ...file, sha256: result.file.sha256 } : file),
            };
            workspaceRef.current = next;
            return next;
          });
        }
        return true;
      } catch {
        const nextDocuments = {
          ...documentsRef.current,
          [path]: { ...documentsRef.current[path], error: text("当前修改已在浏览器中保留，连接恢复后可以再次保存。", "Your changes remain in this browser and can be saved when the connection recovers.") },
        };
        documentsRef.current = nextDocuments;
        setDocuments(nextDocuments);
        return false;
      } finally {
        setSavingPaths((paths) => { const next = new Set(paths); next.delete(path); return next; });
      }
    });
    saveQueueRef.current = operation.catch(() => false);
    return operation;
  }, [adminMode, id, text]);

  const flushDocuments = useCallback(async () => {
    for (const path of Object.keys(documentsRef.current)) {
      if (!await persistDocument(path)) return false;
    }
    return true;
  }, [persistDocument]);

  useEffect(() => {
    const current = documents[selectedPath];
    if (!current || current.content === current.savedContent || adminMode || !workspace?.permissions.editCode) return;
    const timer = window.setTimeout(() => storeWorkspaceDraft(id, current), LOCAL_DRAFT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [adminMode, documents, id, selectedPath, workspace?.permissions.editCode]);

  useEffect(() => {
    const current = documents[selectedPath];
    if (!current || current.content === current.savedContent || adminMode || !workspace?.permissions.editCode) return;
    // One server save creates one immutable Git revision. Waiting for five
    // idle seconds consolidates a typing burst instead of archiving every key.
    const timer = window.setTimeout(() => void persistDocument(selectedPath).catch(() => undefined), REVISION_IDLE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [adminMode, documents, persistDocument, selectedPath, workspace?.permissions.editCode]);

  const openFile = useCallback(async (path: string) => {
    if (!path) return;
    if (selectedPath && selectedPath !== path) await persistDocument(selectedPath);
    setSelectedPath(path); setOpenPaths((items) => items.includes(path) ? items : [...items, path]); setDiffMode(false); setMobilePane("editor");
    if (documentsRef.current[path]) return;
    try {
      const file = await readExperimentFile(id, path);
      const draft = restoreWorkspaceDraft(id, file);
      setDocuments((items) => ({ ...items, [path]: { ...file, content: draft ?? file.content, savedContent: file.content } }));
    } catch {
      setDocuments((items) => ({ ...items, [path]: { path, content: "", savedContent: "", sha256: "", error: text("文件正在自动恢复，请稍后重新打开。", "The file is recovering. Open it again shortly.") } }));
    }
  }, [id, persistDocument, selectedPath, text]);

  useEffect(() => {
    if (!workspace || selectedPath) return;
    const files = workspace.files.filter((item) => item.type === "file");
    const preferred = files.find((item) => /(^|\/)README\.md$/i.test(item.path)) ?? files[0];
    if (preferred) void openFile(preferred.path);
  }, [openFile, selectedPath, workspace]);

  useEffect(() => {
    const revisionId = workspace?.experiment.currentRevisionId;
    if (!revisionId) return;
    for (const [path, document] of Object.entries(documentsRef.current)) {
      if (document.revisionId === revisionId) continue;
      if (document.content !== document.savedContent) {
        const nextDocuments = { ...documentsRef.current, [path]: { ...document, error: text("仓库已有新版本；请先复制当前修改，再重新打开该文件。", "The repository has a newer revision. Copy your edits, then reopen this file.") } };
        documentsRef.current = nextDocuments; setDocuments(nextDocuments);
        continue;
      }
      void readExperimentFile(id, path).then((file) => {
        const latest = documentsRef.current[path];
        if (!latest || latest.content !== latest.savedContent || latest.revisionId === file.revisionId) return;
        const nextDocuments = { ...documentsRef.current, [path]: { ...file, savedContent: file.content } };
        documentsRef.current = nextDocuments; setDocuments(nextDocuments);
      }).catch(() => undefined);
    }
  }, [id, text, workspace?.experiment.currentRevisionId]);

  if (loading) return <div className="experiment-loading"><LoaderCircle className="animate-spin"/><strong>{text("正在恢复实验工作区…", "Restoring experiment workspace…")}</strong><span>{text("代码和实验状态会从最近的检查点载入。", "Code and experiment state will load from the latest checkpoint.")}</span></div>;
  if (error || !workspace) return <div className="experiment-loading"><strong>{text("工作区正在自动恢复", "Workspace is recovering")}</strong><span>{error || text("请稍后重试。", "Try again shortly.")}</span><button className="button button-secondary" onClick={() => { setLoading(true); void refresh().finally(() => setLoading(false)); }}>{text("立即重试", "Retry now")}</button></div>;

  const experiment = workspace.experiment;
  const permissions = adminMode ? { ...workspace.permissions, editCode: false, chat: false, terminalWrite: false, runValidation: false, rollback: false } : workspace.permissions;
  const visibleWorkspace = permissions === workspace.permissions ? workspace : { ...workspace, permissions };
  const reportRoute = adminMode ? `/admin/reports/${experiment.reportId}` : `/reports/${experiment.reportId}`;
  const currentDocument = documents[selectedPath];
  const currentSummary = language === "zh" ? experiment.summaryZh : experiment.summaryEn;
  const canCancel = permissions.cancel && activeStatuses.includes(experiment.status);
  const canValidate = permissions.runValidation && experiment.status === "ready" && (experiment.userValidationCount ?? Math.max(0, experiment.runCount - 1)) < experiment.maxUserValidations;
  const hasRepositoryArchive = workspace.artifacts.some((artifact) => artifact.kind === "archive" && /\.zip$/i.test(artifact.name));
  const activeAction = workspace.actions.some((item) => item.state === "queued" || item.state === "running");
  async function perform(label: string, action: () => Promise<unknown>, refreshAfter = true) {
    setWorking(label); setPageMessage("");
    try {
      if (!await flushDocuments()) {
        setPageMessage(text("代码尚未保存，验证或操作不会使用旧版本。连接恢复后请重试。", "Code is not saved, so the action was not run against an older revision. Retry after the connection recovers."));
        return;
      }
      await action();
      if (refreshAfter) await refresh();
    }
    catch { setPageMessage(text("操作已保留；服务恢复后可以安全重试。", "The action is preserved and can be retried safely when the service recovers.")); }
    finally { setWorking(""); }
  }
  async function beginRepositoryDownload() {
    // Keep this synchronous with the click so popup blockers allow the signed
    // download to open after unsaved files have been flushed.
    const target = window.open("about:blank", "_blank");
    if (target) target.opener = null;
    let navigated = false;
    await perform("download", async () => {
      const result = await downloadExperimentRepository(id);
      if (target) target.location.replace(result.signedUrl);
      else window.open(result.signedUrl, "_blank", "noopener,noreferrer");
      navigated = true;
    });
    if (!navigated) target?.close();
  }
  function applyMovedFile(fromPath: string, toPath: string, files: ExperimentFileEntry[], revisionId?: string) {
    const nextDocuments = { ...documentsRef.current };
    const moved = nextDocuments[fromPath];
    delete nextDocuments[fromPath];
    if (moved) nextDocuments[toPath] = { ...moved, path: toPath, revisionId: revisionId ?? moved.revisionId };
    if (revisionId) for (const document of Object.values(nextDocuments)) document.revisionId = revisionId;
    documentsRef.current = nextDocuments; setDocuments(nextDocuments);
    setOpenPaths((paths) => paths.map((path) => path === fromPath ? toPath : path));
    setSelectedPath((path) => path === fromPath ? toPath : path);
    setWorkspace((value) => value ? { ...value, files, experiment: { ...value.experiment, currentRevisionId: revisionId ?? value.experiment.currentRevisionId } } : value);
  }
  function applyDeletedFile(path: string, files: ExperimentFileEntry[], revisionId?: string) {
    const nextDocuments = { ...documentsRef.current };
    delete nextDocuments[path];
    if (revisionId) for (const document of Object.values(nextDocuments)) document.revisionId = revisionId;
    documentsRef.current = nextDocuments; setDocuments(nextDocuments);
    const remaining = openPaths.filter((item) => item !== path);
    setOpenPaths(remaining);
    setSelectedPath((current) => current === path ? remaining.at(-1) ?? "" : current);
    setWorkspace((value) => value ? { ...value, files, experiment: { ...value.experiment, currentRevisionId: revisionId ?? value.experiment.currentRevisionId } } : value);
  }
  const repository = <RepositoryPane workspace={visibleWorkspace} selectedPath={selectedPath} onOpen={(path) => void openFile(path)} onRefresh={refresh} onBeforeMutation={flushDocuments} onMove={applyMovedFile} onDelete={applyDeletedFile}/>;
  const editor = <EditorPane path={selectedPath} openPaths={openPaths} document={currentDocument} canEdit={permissions.editCode && experiment.status === "ready"} saving={savingPaths.has(selectedPath)} diffMode={diffMode} setDiffMode={setDiffMode} onChange={(content) => setDocuments((items) => ({ ...items, [selectedPath]: { ...items[selectedPath], content, error: undefined } }))} onSelect={(path) => { void persistDocument(selectedPath); setSelectedPath(path); setDiffMode(false); }} onClose={(path) => { void persistDocument(path); const next = openPaths.filter((item) => item !== path); setOpenPaths(next); if (path === selectedPath) setSelectedPath(next.at(-1) ?? ""); setDiffMode(false); }}/>
  const terminal = <ExperimentTerminal experimentId={id} canRead={permissions.terminalRead} canWrite={permissions.terminalWrite && experiment.status === "ready"} active={compact ? mobilePane === "terminal" : true}/>;
  const assistant = <AssistantPane workspace={visibleWorkspace} onSubmit={async (message) => {
    if (!await flushDocuments()) throw new Error("unsaved workspace");
    return submitExperimentAction(id, { kind: "assistant", prompt: message });
  }} onAction={(action) => setWorkspace((current) => current ? { ...current, actions: [...current.actions, action] } : current)} onOpenFile={(path) => void openFile(path)} onRollback={(revisionId) => perform("rollback", () => submitExperimentAction(id, { kind: "rollback", revisionId }))} rollbackBusy={Boolean(working) || activeAction}/>;

  return <div className="experiment-workspace">
    <header className="experiment-toolbar">
      <div className="experiment-toolbar-identity"><Link className="button button-secondary experiment-back-button" to={reportRoute} aria-label={text("返回报告", "Back to report")}><ArrowLeft/></Link><div><span>{experiment.ideaRank === 1 ? text("主 Idea 实验", "Primary idea experiment") : text(`备选 Idea ${experiment.ideaRank}`, `Alternative idea ${experiment.ideaRank}`)}</span><h1>{language === "zh" ? experiment.ideaTitleZh : experiment.ideaTitleEn}</h1></div></div>
      <div className="experiment-toolbar-progress"><div><span className={`experiment-status is-${experiment.status}`}>{statusLabel(experiment.status, text)}</span>{experiment.outcome !== "pending" && <span className={`experiment-outcome is-${experiment.outcome}`}>{outcomeLabel(experiment.outcome, text)}</span>}</div><span>{stageLabel(experiment.stage, text)} · {Math.round(experiment.progress)}%</span><div className="experiment-progress"><i style={{ width: `${Math.max(0, Math.min(100, experiment.progress))}%` }}/></div></div>
      <div className="experiment-toolbar-actions">
        <span className="experiment-cost"><strong>{text("费用", "Cost")}</strong> ${experiment.e2bCostUsd.toFixed(2)} · ¥{experiment.llmCostCny.toFixed(2)}</span>
        {permissions.rollback && workspace.revisions.length > 0 && <><select className="experiment-revision-select" value={selectedRevision} onChange={(event) => setSelectedRevision(event.target.value)} aria-label={text("选择版本", "Select revision")}>{workspace.revisions.map((revision) => <option value={revision.id} key={revision.id}>{revision.label}</option>)}</select><button className="button button-secondary experiment-toolbar-icon-action" aria-label={text("回滚到选定版本", "Roll back to selected revision")} disabled={!selectedRevision || Boolean(working)} onClick={() => void perform("rollback", () => submitExperimentAction(id, { kind: "rollback", revisionId: selectedRevision }))} title={text("回滚到选定版本", "Roll back to selected revision")}><RotateCcw/></button></>}
        {canValidate && <button className="button button-primary experiment-validate-action" disabled={Boolean(working) || activeAction} onClick={() => void perform("validation", () => submitExperimentAction(id, { kind: "validation" }))}><Play/>{text("重新验证", "Validate")}</button>}
        {permissions.download && <button className="button button-secondary experiment-toolbar-icon-action" aria-label={text("下载仓库 ZIP", "Download repository ZIP")} disabled={Boolean(working) || !hasRepositoryArchive} onClick={() => void beginRepositoryDownload()} title={hasRepositoryArchive ? text("下载仓库 ZIP", "Download repository ZIP") : text("仓库归档完成后可下载", "Available after the repository is archived")}><Download/></button>}
        {canCancel && <button className="button button-secondary experiment-toolbar-icon-action" aria-label={text("取消实验", "Cancel experiment")} disabled={Boolean(working)} onClick={() => window.confirm(text("取消当前实验？已经完成的检查点会被保留。", "Cancel the current experiment? Completed checkpoints will be retained.")) && void perform("cancel", () => cancelExperiment(id))} title={text("取消实验", "Cancel experiment")}><Square/></button>}
        {permissions.delete && <button aria-label={text("删除实验", "Delete experiment")} className="button button-danger experiment-toolbar-icon-action" disabled={Boolean(working)} onClick={() => window.confirm(text("永久删除该实验、代码与全部产物？", "Permanently delete this experiment, its code, and all artifacts?")) && void perform("delete", async () => { await deleteExperiment(id); navigate(reportRoute); }, false)} title={text("删除实验", "Delete experiment")}><Trash2/></button>}
      </div>
    </header>
    {(currentSummary || pageMessage || adminMode) && <div className="experiment-notice" data-admin={adminMode || undefined}>{adminMode ? text("管理员审计模式：可以查看代码、终端输出、结果和费用，但不能编辑或向助手发送指令。", "Administrator audit mode: code, terminal output, results, and costs are visible, but editing and assistant instructions are disabled.") : pageMessage || currentSummary}</div>}
    {compact ? <div className="experiment-mobile-layout">
      <nav className="experiment-mobile-tabs" aria-label={text("工作区面板", "Workspace panels")}>{(["files", "editor", "terminal", "assistant"] as MobilePane[]).map((pane) => <button className={mobilePane === pane ? "active" : ""} key={pane} onClick={() => setMobilePane(pane)}>{pane === "files" ? <Folder/> : pane === "editor" ? <Code2/> : pane === "terminal" ? <SquareTerminal/> : <Bot/>}<span>{pane === "files" ? text("文件", "Files") : pane === "editor" ? text("编辑", "Editor") : pane === "terminal" ? text("终端", "Terminal") : text("助手", "Assistant")}</span></button>)}</nav>
      <div className="experiment-mobile-content">{mobilePane === "files" ? repository : mobilePane === "editor" ? editor : mobilePane === "terminal" ? <OutputPane workspace={visibleWorkspace} activePane={bottomPane} onPaneChange={setBottomPane} terminal={terminal}/> : assistant}</div>
    </div> : <Group className="experiment-desktop-layout" orientation="horizontal" id={`experiment-layout-${id}`}>
      <Panel id="repository" defaultSize="18%" minSize={190} maxSize="34%" collapsible collapsedSize={0}>{repository}</Panel><Separator className="experiment-resize-handle"/>
      <Panel id="center" defaultSize="58%" minSize="38%"><Group orientation="vertical" id={`experiment-center-${id}`}><Panel id="editor" defaultSize="66%" minSize="32%">{editor}</Panel><Separator className="experiment-resize-handle is-horizontal"/><Panel id="output" defaultSize="34%" minSize={150} collapsible collapsedSize={0}><OutputPane workspace={visibleWorkspace} activePane={bottomPane} onPaneChange={setBottomPane} terminal={terminal}/></Panel></Group></Panel><Separator className="experiment-resize-handle"/>
      <Panel id="assistant" defaultSize="24%" minSize={260} maxSize="40%" collapsible collapsedSize={0}>{assistant}</Panel>
    </Group>}
  </div>;
}
