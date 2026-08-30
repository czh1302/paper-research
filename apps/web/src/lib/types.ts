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
  file_names?: string[];
}

export interface JobEvent { id: number; kind: string; message: string; data: Record<string, unknown>; created_at: string; }

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
export interface CandidatePaper {
  canonical_id: string; title: string; abstract?: string; year?: number; authors?: string[]; venue?: string;
  url: string; pdf_url?: string; doi?: string; arxiv_id?: string; openreview_id?: string; openalex_id?: string;
  sources: string[]; relevance_score: number; reference_ids?: string[]; citation_count?: number; evidence_grade?: string;
}
export interface ComparisonCell { paper_id: string; axis: string; value_zh: string; value_en: string; evidence_urls: string[]; confidence: number; }
export interface Opportunity { title_zh: string; title_en: string; rationale_zh: string; rationale_en: string; proposed_experiment_zh: string; proposed_experiment_en: string; novelty_evidence: string[]; feasibility: number; impact: number; uncertainty: number; }
export interface RoundAnalysis { summary_zh: string; summary_en: string; comparison_cells: ComparisonCell[]; opportunities: Opportunity[]; covered_axes: string[]; uncovered_axes: string[]; }
export interface PresentationFinding { title_zh: string; title_en: string; statement_zh: string; statement_en: string; implication_zh: string; implication_en: string; pdf_evidence_ids: string[]; source_urls: string[]; }
export interface ResearchTheme { title_zh: string; title_en: string; summary_zh: string; summary_en: string; paper_ids: string[]; }
export interface PresentationIdea {
  key: string; priority: number; title_zh: string; title_en: string; idea_zh: string; idea_en: string;
  gap_zh: string; gap_en: string; approach_zh: string; approach_en: string;
  first_experiment_zh: string; first_experiment_en: string; expected_outcome_zh: string; expected_outcome_en: string;
  main_risk_zh: string; main_risk_en: string; recommendation_reason_zh: string; recommendation_reason_en: string;
  feasibility_reason_zh: string; feasibility_reason_en: string; impact_reason_zh: string; impact_reason_en: string;
  uncertainty_reason_zh: string; uncertainty_reason_en: string; feasibility: number; impact: number; uncertainty: number; evidence_urls: string[];
}
export interface ReportPresentation { version: 2; headline_zh: string; headline_en: string; executive_summary_zh: string; executive_summary_en: string; key_findings: PresentationFinding[]; themes: ResearchTheme[]; ideas: PresentationIdea[]; }
export interface ProblemBriefItem { label_zh: string; label_en: string; explanation_zh: string; explanation_en: string; evidence_ids: string[]; }
export interface AlgorithmStep { order: number; title_zh: string; title_en: string; explanation_zh: string; explanation_en: string; evidence_ids: string[]; }
export interface ProblemBrief {
  paper_id: string; title: string; research_question_zh: string; research_question_en: string; research_question_evidence_ids: string[];
  inputs: ProblemBriefItem[]; outputs: ProblemBriefItem[]; algorithm_steps: AlgorithmStep[]; constraints: ProblemBriefItem[];
}
export interface ExperimentPlan {
  inputs_zh: string; inputs_en: string; baseline_zh: string; baseline_en: string; intervention_zh: string; intervention_en: string;
  metrics_zh: string; metrics_en: string; success_criterion_zh: string; success_criterion_en: string; resources_zh: string; resources_en: string;
}
export interface IdeaEvidence { paper_id: string; relationship: "support" | "overlap" | "counterevidence"; claim_zh: string; claim_en: string; evidence_urls: string[]; }
export interface IdeaAssessment {
  idea_key: string; axis: string; title_zh: string; title_en: string; hypothesis_zh: string; hypothesis_en: string;
  change_from_target_zh: string; change_from_target_en: string; recommendation_reason_zh: string; recommendation_reason_en: string;
  feasibility_conditions_zh: string; feasibility_conditions_en: string; unresolved_questions_zh: string[]; unresolved_questions_en: string[];
  evidence: IdeaEvidence[]; experiment: ExperimentPlan; feasibility: number; impact: number; evidence_confidence: number;
  collision_risk: "low" | "medium" | "high"; verdict: "viable" | "conditional" | "rejected"; rejection_reason_zh: string; rejection_reason_en: string;
}
export interface RejectedIdea { idea_key: string; title_zh: string; title_en: string; reason_zh: string; reason_en: string; }
export interface IdeaComparisonRow {
  paper_role: "input" | "external"; paper_id: string; title: string; relationship: "baseline" | "support" | "overlap" | "counterevidence";
  task_or_capability_zh: string; task_or_capability_en: string; method_or_change_zh: string; method_or_change_en: string;
  output_or_evaluation_zh: string; output_or_evaluation_en: string; key_constraint_zh: string; key_constraint_en: string;
  difference_to_idea_zh: string; difference_to_idea_en: string; evidence_grade: "input_pdf" | "full_text" | "abstract" | "snippet" | "metadata";
  source_urls: string[]; input_evidence_ids: string[];
}
export interface IdeaComparisonMatrix { idea_key: string; status: "viable" | "conditional" | "rejected"; rows: IdeaComparisonRow[]; }
export interface ReportPresentationV3 {
  version: 3; headline_zh: string; headline_en: string; problem_briefs: ProblemBrief[]; ideas: IdeaAssessment[];
  promising_ideas?: IdeaAssessment[]; rejected_ideas: RejectedIdea[]; idea_comparisons?: IdeaComparisonMatrix[];
}
export interface JointProblemStatement { common_problem_zh: string; common_problem_en: string; aligned_concepts: Record<string, unknown>[]; differences: Record<string, unknown>[]; compatible_assumptions: string[]; conflicting_assumptions: string[]; formalization?: string; }
export interface GraphNode { id: string; name: string; year?: number; }
export interface GraphLink { source: string; target: string; }
export interface VisualizationData { timeline: {year: number; count: number}[]; sources: {source: string; count: number}[]; opportunities: {name_zh: string; name_en: string; feasibility: number; impact: number; uncertainty: number}[]; graph: {nodes: GraphNode[]; links: GraphLink[]}; }
export interface AnalysisReport { job_id: string; generated_at: string; problem_statements: ProblemStatement[]; joint_problem_statement?: JointProblemStatement; related_papers: CandidatePaper[]; rounds: RoundAnalysis[]; search_audit: Record<string, unknown>[]; parser_audit: {paper_id: string; parser: string; degraded?: boolean; page_count?: number}[]; source_coverage: { counts: Record<string, number>; rounds_completed: number; queries: number; visualizations: VisualizationData }; limitations_zh: string; limitations_en: string; presentation?: ReportPresentation | ReportPresentationV3; idea_rounds?: { assessments: IdeaAssessment[] }[]; }
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
  cancellation_requested: boolean;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  file_names: string[];
  report_id: string | null;
}
