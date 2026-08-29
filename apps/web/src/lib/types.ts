export type JobStatus = "queued" | "parsing" | "problem_ready" | "searching" | "analyzing" | "rendering" | "completed" | "cancelled" | "failed" | "budget_blocked";

export interface JobRecord {
  id: string;
  mode: "single" | "multi";
  max_rounds: number;
  current_round: number;
  status: JobStatus;
  stage: string;
  progress: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface JobEvent { id: number; kind: string; message: string; data: Record<string, unknown>; created_at: string; }
export interface Quota { allocation: number; used: number; reserved: number; }

export interface Evidence { id: string; paper_id: string; page?: number; section?: string; text: string; source_url?: string; }
export interface ProblemElement { name: string; symbol?: string; domain?: string; description_zh: string; description_en: string; evidence_ids: string[]; }
export interface ProblemStatement {
  paper_id: string; title: string; task_zh: string; task_en: string; background_zh: string; background_en: string;
  is_computer_science: boolean; computer_science_confidence: number;
  background_evidence_ids: string[]; task_evidence_ids: string[]; algorithm_evidence_ids: string[]; formalization_evidence_ids: string[];
  inputs: ProblemElement[]; outputs: ProblemElement[]; objectives: ProblemElement[]; constraints: ProblemElement[];
  assumptions: ProblemElement[]; metrics: ProblemElement[]; algorithm_zh: string; algorithm_en: string;
  formalization?: string; confidence: number; evidence: Evidence[];
}
export interface CandidatePaper { canonical_id: string; title: string; year?: number; venue?: string; url: string; sources: string[]; relevance_score: number; reference_ids?: string[]; }
export interface ComparisonCell { paper_id: string; axis: string; value_zh: string; value_en: string; evidence_urls: string[]; confidence: number; }
export interface Opportunity { title_zh: string; title_en: string; rationale_zh: string; rationale_en: string; proposed_experiment_zh: string; proposed_experiment_en: string; novelty_evidence: string[]; feasibility: number; impact: number; uncertainty: number; }
export interface RoundAnalysis { summary_zh: string; summary_en: string; comparison_cells: ComparisonCell[]; opportunities: Opportunity[]; covered_axes: string[]; uncovered_axes: string[]; }
export interface JointProblemStatement { common_problem_zh: string; common_problem_en: string; aligned_concepts: Record<string, unknown>[]; differences: Record<string, unknown>[]; compatible_assumptions: string[]; conflicting_assumptions: string[]; formalization?: string; }
export interface GraphNode { id: string; name: string; year?: number; }
export interface GraphLink { source: string; target: string; }
export interface VisualizationData { timeline: {year: number; count: number}[]; sources: {source: string; count: number}[]; opportunities: {name_zh: string; name_en: string; feasibility: number; impact: number; uncertainty: number}[]; graph: {nodes: GraphNode[]; links: GraphLink[]}; }
export interface AnalysisReport { job_id: string; generated_at: string; problem_statements: ProblemStatement[]; joint_problem_statement?: JointProblemStatement; related_papers: CandidatePaper[]; rounds: RoundAnalysis[]; search_audit: Record<string, unknown>[]; parser_audit: {paper_id: string; parser: string; degraded?: boolean; page_count?: number}[]; source_coverage: { counts: Record<string, number>; rounds_completed: number; queries: number; visualizations: VisualizationData }; limitations_zh: string; limitations_en: string; }
export interface ReportRecord { id: string; job_id: string; content: AnalysisReport; markdown: string; created_at: string; }

export interface AdminUserRow {
  total_count: number;
  user_id: string;
  email: string;
  created_at: string;
  last_sign_in_at: string | null;
  job_count: number;
  active_job_count: number;
  completed_job_count: number;
  allocation: number;
  used: number;
  reserved: number;
}

export interface AdminJobRow {
  total_count: number;
  job_id: string;
  user_id: string;
  user_email: string;
  mode: "single" | "multi";
  status: JobStatus;
  stage: string;
  progress: number;
  max_rounds: number;
  current_round: number;
  reserved_units: number;
  charged_units: number;
  cancellation_requested: boolean;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  file_names: string[];
  report_id: string | null;
}
