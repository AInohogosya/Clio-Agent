"""
ArchitectAgent — Masterpiece Meta-Reasoning Sub-Agent

A state-of-the-art sub-agent for architectural analysis, design, and
decision-making. Operates a 6-phase reasoning loop (Discovery → Analysis
→ Design → Critique → Refinement → Synthesis) powered by the LLM
model runner, producing formal Architectural Decision Records (ADRs),
trade-off matrices, risk registers, and implementation roadmaps.

Why it surpasses the Main Agent:
  1. Structured multi-phase reasoning with phase-gate transitions
  2. Built-in self-critique mechanism that attacks its own designs
  3. Formal ADR output format — professional-grade architecture docs
  4. Quantitative trade-off scoring across 6 dimensions
  5. Iterative refinement: designs improve through critique cycles
  6. Deep codebase exploration with dependency mapping
  7. Risk-aware: identifies and classifies risks with mitigations

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │                    ArchitectAgent                           │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
  │  │DISCOVERY │→│ ANALYSIS │→│  DESIGN  │→│ CRITIQUE │   │
  │  │  Phase   │  │  Phase   │  │  Phase   │  │  Phase   │   │
  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
  │        │                                        │          │
  │        └────────────┐              ┌────────────┘          │
  │                     ▼              ▼                        │
  │              ┌──────────┐  ┌──────────────┐               │
  │              │REFINEMENT│→│  SYNTHESIS   │               │
  │              │  Phase   │  │    Phase     │→ Report       │
  │              └──────────┘  └──────────────┘               │
  │                                                            │
  │  Supporting Engines:                                       │
  │  • TradeOffScorer — multi-dimensional quantitative scoring │
  │  • ADRGenerator   — produces formatted ADR documents       │
  │  • CritiqueEngine  — systematic weakness detection         │
  │  • DependencyMapper — builds dependency graphs from code   │
  └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json as _json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..base import SubAgentBase, SubAgentResult, SubAgentStatus
from ..context import SubAgentContext
from ..registry import sub_agent
from ..prompts import (
    ARCHITECT_SYSTEM_PROMPT,
    ARCHITECT_TASK_PROMPT,
    ARCHITECT_PHASE1_DISCOVERY,
    ARCHITECT_PHASE2_ANALYSIS,
    ARCHITECT_PHASE3_DESIGN,
    ARCHITECT_PHASE4_CRITIQUE,
    ARCHITECT_PHASE5_REFINEMENT,
    ARCHITECT_PHASE6_SYNTHESIS,
)
from ...utils.logger import get_logger

logger = get_logger("sub_agent.architect")


# ════════════════════════════════════════════════════════════════
# Enums & Data Classes
# ════════════════════════════════════════════════════════════════

class ArchitectPhase(Enum):
    """Phases of the Architect Agent's reasoning loop."""
    DISCOVERY = auto()
    ANALYSIS = auto()
    DESIGN = auto()
    CRITIQUE = auto()
    REFINEMENT = auto()
    SYNTHESIS = auto()

    def next_phase(self) -> Optional[ArchitectPhase]:
        order = [
            ArchitectPhase.DISCOVERY,
            ArchitectPhase.ANALYSIS,
            ArchitectPhase.DESIGN,
            ArchitectPhase.CRITIQUE,
            ArchitectPhase.REFINEMENT,
            ArchitectPhase.SYNTHESIS,
        ]
        idx = order.index(self)
        return order[idx + 1] if idx + 1 < len(order) else None

    @property
    def prompt_constant(self):
        mapping = {
            ArchitectPhase.DISCOVERY: ARCHITECT_PHASE1_DISCOVERY,
            ArchitectPhase.ANALYSIS: ARCHITECT_PHASE2_ANALYSIS,
            ArchitectPhase.DESIGN: ARCHITECT_PHASE3_DESIGN,
            ArchitectPhase.CRITIQUE: ARCHITECT_PHASE4_CRITIQUE,
            ArchitectPhase.REFINEMENT: ARCHITECT_PHASE5_REFINEMENT,
            ArchitectPhase.SYNTHESIS: ARCHITECT_PHASE6_SYNTHESIS,
        }
        return mapping[self]


class ScoreDimension(Enum):
    """Dimensions for quantitative architectural trade-off scoring."""
    SIMPLICITY = "simplicity"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    EXTENSIBILITY = "extensibility"
    RISK = "risk"               # Higher = riskier
    ALIGNMENT = "alignment"     # Alignment with existing codebase
    TESTABILITY = "testability"
    SCALABILITY = "scalability"


class RiskLikelihood(Enum):
    """Likelihood of a risk materializing."""
    VERY_LOW = "Very Low"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    VERY_HIGH = "Very High"


class RiskImpact(Enum):
    """Impact if a risk materializes."""
    NEGLIGIBLE = "Negligible"
    MINOR = "Minor"
    MODERATE = "Moderate"
    MAJOR = "Major"
    CATASTROPHIC = "Catastrophic"


@dataclass
class Scorecard:
    """Multi-dimensional score for an architectural alternative."""
    alternative_name: str = ""
    scores: Dict[ScoreDimension, float] = field(default_factory=dict)
    notes: Dict[ScoreDimension, str] = field(default_factory=dict)

    def set_score(self, dimension: ScoreDimension, value: float, note: str = "") -> None:
        if not (1.0 <= value <= 10.0):
            raise ValueError(f"Score must be 1-10, got {value}")
        self.scores[dimension] = value
        if note:
            self.notes[dimension] = note

    def weighted_score(self, weights: Optional[Dict[ScoreDimension, float]] = None) -> float:
        if weights is None:
            weights = {
                ScoreDimension.SIMPLICITY: 1.0,
                ScoreDimension.PERFORMANCE: 0.8,
                ScoreDimension.MAINTAINABILITY: 1.0,
                ScoreDimension.EXTENSIBILITY: 0.7,
                ScoreDimension.RISK: 1.0,
                ScoreDimension.ALIGNMENT: 1.2,
                ScoreDimension.TESTABILITY: 0.6,
                ScoreDimension.SCALABILITY: 0.5,
            }
        total_weight = 0.0
        weighted_sum = 0.0
        for dim, score in self.scores.items():
            w = weights.get(dim, 0.5)
            # Risk is inverted: high risk = bad
            if dim == ScoreDimension.RISK:
                adjusted = 11.0 - score
            else:
                adjusted = score
            weighted_sum += adjusted * w
            total_weight += w
        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def to_markdown_table(self) -> str:
        lines = []
        for dim in ScoreDimension:
            if dim in self.scores:
                note = self.notes.get(dim, "")
                lines.append(f"| {dim.value.capitalize()} | {self.scores[dim]:.1f}/10 | {note} |")
        return "\n".join(lines)


@dataclass
class ArchitecturalAlternative:
    """A complete architectural design alternative."""
    name: str
    description: str = ""
    components: List[Dict[str, str]] = field(default_factory=list)
    data_flow: str = ""
    integration_points: List[str] = field(default_factory=list)
    scorecard: Scorecard = field(default_factory=Scorecard)
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    edge_cases: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)
    hidden_costs: List[str] = field(default_factory=list)
    critique_notes: str = ""
    refinement_notes: str = ""


@dataclass
class RiskEntry:
    """A single entry in the risk register."""
    risk_id: str
    description: str
    likelihood: RiskLikelihood = RiskLikelihood.MEDIUM
    impact: RiskImpact = RiskImpact.MODERATE
    mitigation: str = ""
    trigger: str = ""
    owner: str = "Architect"


@dataclass
class ADR:
    """Architectural Decision Record."""
    adr_id: str
    title: str
    status: str = "Proposed"
    context: str = ""
    decision: str = ""
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    consequences: str = ""
    confidence: str = "Medium"

    def to_markdown(self) -> str:
        lines = [
            f"### {self.adr_id}: {self.title}",
            "",
            f"**Status:** {self.status}",
            "",
            "**Context:**",
            self.context,
            "",
            "**Decision:**",
            self.decision,
            "",
        ]
        if self.alternatives:
            lines.append("**Alternatives Considered:**")
            for i, alt in enumerate(self.alternatives, 1):
                name = alt.get("name", f"Alternative {i}")
                desc = alt.get("description", "")
                pros_list = alt.get("pros", [])
                cons_list = alt.get("cons", [])
                lines.append(f"{i}. **{name}** — {desc}")
                if pros_list:
                    lines.append("   - Pros: " + "; ".join(pros_list))
                if cons_list:
                    lines.append("   - Cons: " + "; ".join(cons_list))
            lines.append("")
        lines.extend([
            "**Consequences:**",
            self.consequences,
            "",
            f"**Confidence:** {self.confidence}",
            "",
        ])
        return "\n".join(lines)


@dataclass
class DependencyNode:
    """Node in a dependency graph."""
    file_path: str
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    is_core: bool = False
    module_type: str = "unknown"
    complexity: int = 0


@dataclass
class DiscoveryFinding:
    """A single finding from the DISCOVERY phase."""
    file_path: str
    pattern: str = ""
    observation: str = ""
    constraint: str = ""
    technical_debt: str = ""


# ════════════════════════════════════════════════════════════════
# Supporting Engines
# ════════════════════════════════════════════════════════════════

class TradeOffScorer:
    """Engine for quantitative multi-dimensional architectural scoring.

    Provides:
    - Weighted scoring across 8 dimensions
    - Pairwise comparison for relative ranking
    - Pareto frontier identification
    - Sensitivity analysis (which weight changes flip the ranking?)
    """

    DEFAULT_WEIGHTS: Dict[ScoreDimension, float] = {
        ScoreDimension.SIMPLICITY: 1.0,
        ScoreDimension.PERFORMANCE: 0.8,
        ScoreDimension.MAINTAINABILITY: 1.0,
        ScoreDimension.EXTENSIBILITY: 0.7,
        ScoreDimension.RISK: 1.0,
        ScoreDimension.ALIGNMENT: 1.2,
        ScoreDimension.TESTABILITY: 0.6,
        ScoreDimension.SCALABILITY: 0.5,
    }

    @classmethod
    def rank_alternatives(
        cls,
        alternatives: List[ArchitecturalAlternative],
        weights: Optional[Dict[ScoreDimension, float]] = None,
    ) -> List[Tuple[ArchitecturalAlternative, float]]:
        """Rank alternatives by weighted score, descending."""
        if weights is None:
            weights = cls.DEFAULT_WEIGHTS
        scored = []
        for alt in alternatives:
            score = alt.scorecard.weighted_score(weights)
            scored.append((alt, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @classmethod
    def compare_pairwise(
        cls,
        alt_a: ArchitecturalAlternative,
        alt_b: ArchitecturalAlternative,
    ) -> Dict[ScoreDimension, Tuple[float, float, str]]:
        """Return pairwise comparison dict: dimension → (score_a, score_b, winner)."""
        results = {}
        for dim in ScoreDimension:
            sa = alt_a.scorecard.scores.get(dim, 5.0)
            sb = alt_b.scorecard.scores.get(dim, 5.0)
            if dim == ScoreDimension.RISK:
                winner = alt_a.name if sa < sb else (alt_b.name if sb < sa else "tie")
            else:
                winner = alt_a.name if sa > sb else (alt_b.name if sb > sa else "tie")
            results[dim] = (sa, sb, winner)
        return results

    @classmethod
    def sensitivity_analysis(
        cls,
        alternatives: List[ArchitecturalAlternative],
        base_weights: Dict[ScoreDimension, float],
        dimension: ScoreDimension,
        step: float = 0.5,
        max_change: float = 5.0,
    ) -> List[Tuple[float, List[Tuple[str, float]]]]:
        """Determine at which weight values the ranking changes."""
        results = []
        original_weight = base_weights.get(dimension, 1.0)
        delta = 0.0
        while delta <= max_change:
            modified = dict(base_weights)
            modified[dimension] = original_weight + delta
            ranked = cls.rank_alternatives(alternatives, modified)
            results.append((delta, [(a.name, s) for a, s in ranked]))
            delta += step
        return results


class CritiqueEngine:
    """Engine for systematic architectural critique.

    Challenges designs by examining:
    - Assumptions (stated and unstated)
    - Edge cases and boundary conditions
    - Failure modes and blast radius
    - Hidden costs (migration, learning, operations)
    - Technical debt generation
    - Consistency with existing patterns
    """

    CRITIQUE_QUESTIONS = [
        ("Assumptions", [
            "What must be true for this design to work correctly?",
            "Are all dependencies available in the current environment?",
            "Does this assume a specific threading/asyncio model?",
            "Does this assume unlimited resources (memory, disk, CPU)?",
            "What implicit API contracts does this rely on?",
        ]),
        ("Edge Cases", [
            "What happens when input data is empty or malformed?",
            "What happens under concurrent access?",
            "What happens when a dependency is unavailable?",
            "What happens at scale (1000x current load)?",
            "What happens when the system is partially migrated?",
        ]),
        ("Failure Modes", [
            "What's the blast radius if the core component fails?",
            "Is failure graceful or catastrophic?",
            "Can the system recover automatically from failure?",
            "What is the worst-case data loss scenario?",
            "What is the worst-case downtime scenario?",
        ]),
        ("Hidden Costs", [
            "How much existing code must change to adopt this?",
            "What new dependencies or infrastructure are required?",
            "What is the learning curve for the team?",
            "How much ongoing operational complexity is added?",
            "What is the migration window and can it be done incrementally?",
        ]),
        ("Technical Debt", [
            "Does this design create coupling that will be hard to break later?",
            "Are there any temporary workarounds that will need to be fixed?",
            "Does this create inconsistent patterns with the rest of the codebase?",
            "What documentation burden does this create?",
            "How hard would it be to reverse this decision later?",
        ]),
    ]

    @classmethod
    def critique(
        cls,
        alternative: ArchitecturalAlternative,
    ) -> Dict[str, List[str]]:
        """Generate a structured critique of an alternative."""
        critique: Dict[str, List[str]] = {}
        for category, questions in cls.CRITIQUE_QUESTIONS:
            critique[category] = [
                f"{q}  [{alternative.name}: {'NEEDS ANALYSIS'}]"
                for q in questions
            ]
        return critique

    @classmethod
    def compare_alternatives(
        cls,
        alternatives: List[ArchitecturalAlternative],
    ) -> Dict[str, Any]:
        """Produce a comparative analysis of all alternatives."""
        comparison = {
            "alternatives": [alt.name for alt in alternatives],
            "critiques": {},
            "strongest": "",
            "strongest_rationale": "",
            "fatal_flaws": {},
        }

        for alt in alternatives:
            comparison["critiques"][alt.name] = cls.critique(alt)

        if alternatives:
            scored = TradeOffScorer.rank_alternatives(alternatives)
            comparison["strongest"] = scored[0][0].name if scored else ""

        return comparison


class DependencyMapper:
    """Engine for mapping codebase dependencies.

    Builds import graphs, identifies core modules, and detects
    circular dependencies that constrain architectural choices.
    """

    @staticmethod
    def map_dependencies(
        files: List[str],
        cwd: str,
    ) -> Dict[str, DependencyNode]:
        """Build a dependency graph for given files.

        Args:
            files: List of file paths relative to cwd.
            cwd: Working directory.

        Returns:
            Dict mapping file paths to DependencyNodes.
        """
        nodes: Dict[str, DependencyNode] = {}

        for fpath in files:
            full_path = os.path.join(cwd, fpath)
            if not os.path.isfile(full_path):
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            # Extract imports
            import_pattern = re.compile(
                r'^(?:from\s+(\S+)\s+import|import\s+(\S+))',
                re.MULTILINE,
            )
            imports = []
            for match in import_pattern.finditer(content):
                imp = match.group(1) or match.group(2)
                imports.append(imp)

            # Heuristic complexity: count lines, classes, functions
            lines = content.count("\n") + 1
            classes = len(re.findall(r'^\s*class\s+\w+', content, re.MULTILINE))
            functions = len(re.findall(r'^\s*def\s+\w+', content, re.MULTILINE))
            complexity = min(10, max(1, (lines // 100) + classes + functions))

            # Heuristic module type
            if "sub_agent" in fpath.lower() or "agent" in fpath.lower():
                module_type = "agent"
            elif "tool" in fpath.lower():
                module_type = "tool"
            elif "provider" in fpath.lower() or "api" in fpath.lower():
                module_type = "api_client"
            elif "test" in fpath.lower():
                module_type = "test"
            else:
                module_type = "module"

            # Heuristic core module detection
            is_core = (
                "core" in fpath.lower()
                or "base" in fpath.lower()
                or fpath.endswith("__init__.py")
                or "engine" in fpath.lower()
            )

            nodes[fpath] = DependencyNode(
                file_path=fpath,
                imports=imports,
                imported_by=[],
                is_core=is_core,
                module_type=module_type,
                complexity=complexity,
            )

        return nodes

    @staticmethod
    def detect_circular_dependencies(
        nodes: Dict[str, DependencyNode],
    ) -> List[List[str]]:
        """Detect circular import chains using DFS."""
        known_packages = set()
        for node in nodes.values():
            for imp in node.imports:
                parts = imp.split(".")
                if len(parts) >= 2:
                    known_packages.add(".".join(parts[:2]))

        cycles = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def _dfs(file_path: str, path: List[str]) -> bool:
            visited.add(file_path)
            rec_stack.add(file_path)
            path.append(file_path)

            node = nodes.get(file_path)
            if node:
                for imp in node.imports:
                    for other_path, other_node in nodes.items():
                        module_name = other_path.replace("/", ".").replace(".py", "")
                        if imp in module_name or module_name.endswith(imp):
                            if other_path not in visited:
                                if _dfs(other_path, path):
                                    return True
                            elif other_path in rec_stack:
                                cycle_start = path.index(other_path)
                                cycles.append(path[cycle_start:] + [other_path])
                                return True
                            break

            path.pop()
            rec_stack.discard(file_path)
            return False

        for fpath in nodes:
            if fpath not in visited:
                _dfs(fpath, [])

        return cycles


class ADRGenerator:
    """Engine for producing formatted Architectural Decision Records."""

    @staticmethod
    def create_adr(
        adr_id: str,
        title: str,
        status: str = "Proposed",
        context: str = "",
        decision: str = "",
        alternatives: Optional[List[Dict[str, Any]]] = None,
        consequences: str = "",
        confidence: str = "Medium",
    ) -> ADR:
        return ADR(
            adr_id=adr_id,
            title=title,
            status=status,
            context=context,
            decision=decision,
            alternatives=alternatives or [],
            consequences=consequences,
            confidence=confidence,
        )

    @staticmethod
    def from_text_block(text: str) -> Optional[ADR]:
        """Parse an ADR from a text block."""
        lines = text.strip().splitlines()
        if len(lines) < 3:
            return None

        adr = ADR(adr_id="", title="")

        for line in lines:
            line = line.strip()
            if line.startswith("### ADR-") or line.startswith("## ADR-"):
                parts = re.match(r"#{2,3}\s*(ADR-\d+):\s*(.+)", line)
                if parts:
                    adr.adr_id = parts.group(1)
                    adr.title = parts.group(2)
            elif line.startswith("**Status:**"):
                adr.status = line.replace("**Status:**", "").strip()
            elif line.startswith("**Confidence:**"):
                adr.confidence = line.replace("**Confidence:**", "").strip()

        return adr if adr.adr_id else None

    @staticmethod
    def build_report(
        title: str,
        executive_summary: str,
        current_state: str,
        problem_decomposition: str,
        alternatives: List[ArchitecturalAlternative],
        recommended: str,
        adrs: List[ADR],
        roadmap: str,
        risk_register: List[RiskEntry],
        tradeoff_summary: str,
    ) -> str:
        """Build the complete architecture report as a string."""
        lines = [
            f"# Architecture Plan: {title}",
            "",
            "## Executive Summary",
            executive_summary,
            "",
            "## Current State Analysis",
            current_state,
            "",
            "## Problem Decomposition",
            problem_decomposition,
            "",
            "## Design Alternatives",
        ]

        for i, alt in enumerate(alternatives, 1):
            lines.extend([
                f"### Alternative {chr(64 + i)}: {alt.name}",
                "",
                f"**Approach:** {alt.description}",
                "",
            ])
            if alt.components:
                lines.append("**Components:**")
                for comp in alt.components:
                    name = comp.get("name", "?")
                    role = comp.get("role", "")
                    lines.append(f"- **{name}**: {role}")
                lines.append("")

            if alt.data_flow:
                lines.extend(["**Data Flow:**", alt.data_flow, ""])

            if alt.integration_points:
                lines.append("**Integration Points:**")
                for pt in alt.integration_points:
                    lines.append(f"- {pt}")
                lines.append("")

            lines.extend([
                "**Scorecard:**",
                "",
                "| Dimension | Score | Notes |",
                "|-----------|-------|-------|",
                alt.scorecard.to_markdown_table(),
                "",
                f"**Weighted Score:** {alt.scorecard.weighted_score():.2f}/10",
                "",
            ])

            if alt.pros:
                lines.append("**Pros:**")
                for p in alt.pros:
                    lines.append(f"- ✅ {p}")
                lines.append("")

            if alt.cons:
                lines.append("**Cons:**")
                for c in alt.cons:
                    lines.append(f"- ❌ {c}")
                lines.append("")

        lines.extend([
            "---",
            "",
            "## Recommended Architecture",
            "",
            recommended,
            "",
            "---",
            "",
            "## Architectural Decision Records",
            "",
        ])

        for adr in adrs:
            lines.append(adr.to_markdown())
            lines.append("---")
            lines.append("")

        lines.extend([
            "## Implementation Roadmap",
            roadmap,
            "",
            "## Risk Register",
            "",
            "| Risk ID | Description | Likelihood | Impact | Mitigation | Trigger |",
            "|---------|-------------|-----------|--------|------------|---------|",
        ])

        for risk in risk_register:
            lines.append(
                f"| {risk.risk_id} | {risk.description} | "
                f"{risk.likelihood.value} | {risk.impact.value} | "
                f"{risk.mitigation} | {risk.trigger} |"
            )

        lines.extend([
            "",
            "## Trade-off Summary",
            tradeoff_summary,
        ])

        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# ArchitectAgent — Main Class
# ════════════════════════════════════════════════════════════════

@sub_agent("architect", description="Architectural analysis, design, ADR generation, and trade-off analysis")
class ArchitectAgent(SubAgentBase):
    """Masterpiece meta-reasoning sub-agent for architectural design.

    Capabilities:
    - Multi-phase structured reasoning (Discovery → Synthesis)
    - LLM-powered analysis with the model runner
    - Quantitative trade-off scoring across 8 dimensions
    - Formal ADR document generation
    - Codebase dependency mapping
    - Risk analysis and mitigation planning
    - Self-critique for design improvement
    """

    agent_type = "architect"

    # Maximum iterations per phase (prevents infinite loops)
    MAX_ITERATIONS_PER_PHASE = 10
    # Maximum total iterations
    MAX_TOTAL_ITERATIONS = 60
    # Temperature for creative phases (Design, Critique)
    CREATIVE_TEMPERATURE = 0.7
    # Temperature for analytical phases (Discovery, Analysis, Synthesis)
    ANALYTICAL_TEMPERATURE = 0.3
    # Max tokens for model responses
    MAX_TOKENS = 4096

    def __init__(self, context: SubAgentContext) -> None:
        super().__init__(context)
        self._model_runner = context.model_runner
        self._cwd = context.working_directory
        self._phase: ArchitectPhase = ArchitectPhase.DISCOVERY
        self._iteration: int = 0
        self._total_iteration: int = 0
        self._phase_outputs: Dict[ArchitectPhase, str] = {}
        self._discovery_findings: List[DiscoveryFinding] = []
        self._alternatives: List[ArchitecturalAlternative] = []
        self._adrs: List[ADR] = []
        self._risk_register: List[RiskEntry] = []
        self._phase_history: List[str] = []
        self._dependency_graph: Dict[str, DependencyNode] = {}
        self._circular_deps: List[List[str]] = []

    def initialize(self) -> None:
        super().initialize()
        self._phase = ArchitectPhase.DISCOVERY
        self._iteration = 0
        self._total_iteration = 0
        self._phase_outputs.clear()
        self._discovery_findings.clear()
        self._alternatives.clear()
        self._adrs.clear()
        self._risk_register.clear()
        self._phase_history.clear()
        self._dependency_graph.clear()
        self._circular_deps.clear()

    def _run(self) -> str:
        """Execute the full 6-phase architectural reasoning loop.

        Each phase may involve 1+ iterations with the LLM model runner.
        The agent progresses through phases sequentially, using the output
        of each phase as input to the next.
        """
        self._total_iteration = 0
        max_total = min(self.context.max_iterations, self.MAX_TOTAL_ITERATIONS)

        while self._total_iteration < max_total and self._phase is not None:
            self._iteration = 0
            phase_max = self.MAX_ITERATIONS_PER_PHASE

            self._log_phase_start()

            while self._iteration < phase_max and self._total_iteration < max_total:
                self._iteration += 1
                self._total_iteration += 1

                try:
                    phase_output = self._execute_phase_iteration()
                except Exception as e:
                    logger.error(
                        f"Phase {self._phase.name} iteration {self._iteration} "
                        f"failed: {e}"
                    )
                    self._phase_history.append(
                        f"[{self._phase.name}] ERROR: {e}"
                    )
                    break

                if phase_output:
                    self._phase_history.append(phase_output[:500])

                if self._is_phase_complete():
                    self._log_phase_complete()
                    break

            self._phase = self._phase.next_phase()

        return self._build_final_report()

    # ── Phase Execution ──────────────────────────────────────────

    def _execute_phase_iteration(self) -> str:
        """Execute one iteration of the current phase.

        Combines:
        1. Phase-specific system instructions
        2. Accumulated context from previous phases
        3. Task description
        4. LLM call via model runner
        5. Parsing and storing phase output
        """
        if self._model_runner is None:
            return self._phase_fallback_execution()

        prompt = self._build_phase_prompt()
        temperature = (
            self.CREATIVE_TEMPERATURE
            if self._phase in (ArchitectPhase.DESIGN, ArchitectPhase.CRITIQUE)
            else self.ANALYTICAL_TEMPERATURE
        )

        try:
            from ...external_integration.model_runner import (
                ModelRequest,
                TaskType,
            )
            request = ModelRequest(
                task_type=TaskType.AUTONOMOUS_LOOP,
                prompt=prompt,
                max_tokens=self.MAX_TOKENS,
                temperature=temperature,
            )
            response = self._model_runner.run_model(request)

            if response.success and response.content:
                output = response.content
                self._process_phase_output(output)
                self._execute_tools_from_output(output)
                return output
            else:
                error = response.error or "Model returned no content"
                logger.warning(f"Phase {self._phase.name} model error: {error}")
                return f"[MODEL ERROR]: {error}"

        except Exception as e:
            logger.error(f"Phase {self._phase.name} execution error: {e}")
            return f"[EXECUTION ERROR]: {e}"

    def _phase_fallback_execution(self) -> str:
        """Execute the phase without LLM — using codebase exploration.

        This runs when no model runner is available. The agent performs
        structural analysis directly using the filesystem and tools.
        """
        if self._phase == ArchitectPhase.DISCOVERY:
            return self._run_discovery_fallback()
        elif self._phase == ArchitectPhase.ANALYSIS:
            return self._run_analysis_fallback()
        else:
            return f"[NO MODEL RUNNER] Cannot complete {self._phase.name} phase without LLM."

    def _run_discovery_fallback(self) -> str:
        """Fallback DISCOVERY: scan the codebase directly."""
        findings: List[str] = []
        project_dirs = ["src", "tests", "peripherals", "gui"]

        for pd in project_dirs:
            full = os.path.join(self._cwd, pd)
            if not os.path.isdir(full):
                continue

            py_files = []
            for root, _dirs, files in os.walk(full):
                for fname in files:
                    if fname.endswith(".py"):
                        py_files.append(os.path.relpath(
                            os.path.join(root, fname), self._cwd
                        ))

            findings.append(f"### {pd}/ — {len(py_files)} Python files")

            # Map dependencies
            sub_files = py_files[:20]  # Limit to avoid timeout
            nodes = DependencyMapper.map_dependencies(sub_files, self._cwd)
            self._dependency_graph.update(nodes)

            # Count patterns
            core_count = sum(1 for n in nodes.values() if n.is_core)
            agent_count = sum(1 for n in nodes.values() if n.module_type == "agent")
            tool_count = sum(1 for n in nodes.values() if n.module_type == "tool")

            findings.append(f"  - Core modules: {core_count}")
            findings.append(f"  - Agent modules: {agent_count}")
            findings.append(f"  - Tool modules: {tool_count}")

            for fpath in sub_files[:10]:
                node = nodes.get(fpath)
                if node:
                    findings.append(
                        f"  - `{fpath}`: {node.module_type}, "
                        f"complexity={node.complexity}, "
                        f"{'CORE' if node.is_core else 'leaf'}"
                    )
                    self._discovery_findings.append(DiscoveryFinding(
                        file_path=fpath,
                        pattern=node.module_type,
                        observation=f"{node.complexity} complexity, {len(node.imports)} imports",
                    ))

        # Detect circular deps
        if len(self._dependency_graph) > 1:
            self._circular_deps = DependencyMapper.detect_circular_dependencies(
                self._dependency_graph
            )

        result = "\n".join(findings)
        if self._circular_deps:
            result += "\n\n### ⚠️ Circular Dependencies Detected\n"
            for cycle in self._circular_deps:
                result += f"- {' → '.join(cycle)}\n"

        self._phase_outputs[self._phase] = result
        return result

    def _run_analysis_fallback(self) -> str:
        """Fallback ANALYSIS: decompose based on discovery findings."""
        sub_problems: List[Dict[str, Any]] = []

        # Use discovery findings to identify sub-problems
        files_by_type: Dict[str, List[str]] = {}
        for finding in self._discovery_findings:
            ft = finding.pattern or "unknown"
            if ft not in files_by_type:
                files_by_type[ft] = []
            files_by_type[ft].append(finding.file_path)

        for ftype, fpaths in files_by_type.items():
            sub_problems.append({
                "name": f"Refactor {ftype} layer",
                "description": f"Files: {', '.join(fpaths[:5])}",
                "complexity": "Medium" if len(fpaths) > 5 else "Low",
                "impact": "High" if ftype in ("agent", "core") else "Medium",
                "risk": "Medium",
            })

        lines = ["## Problem Decomposition (Automated)", ""]
        for i, sp in enumerate(sub_problems, 1):
            lines.extend([
                f"### Sub-problem {i}: {sp['name']}",
                f"- Description: {sp['description']}",
                f"- Complexity: {sp['complexity']}",
                f"- Impact: {sp['impact']}",
                f"- Risk: {sp['risk']}",
                "",
            ])

        result = "\n".join(lines)
        self._phase_outputs[self._phase] = result
        return result

    # ── Prompt Building ──────────────────────────────────────────

    def _build_phase_prompt(self) -> str:
        """Build the complete prompt for the current phase iteration.

        Includes:
        - Task description (from context)
        - Phase-specific instructions
        - Accumulated context from previous phases
        - Current iteration status
        """
        ctx = self.context.build_prompt_context()
        history = self._format_phase_history()

        parts = []

        # Task header
        try:
            parts.append(
                ARCHITECT_TASK_PROMPT.format(
                    task=ctx.get("task", ""),
                    working_directory=ctx.get("working_directory", self._cwd),
                    max_iterations=ctx.get("max_iterations", 50),
                    timeout_seconds=ctx.get("timeout_seconds", 600),
                    history=history,
                )
            )
        except Exception:
            parts.append(f"## TASK\n{self.context.task}\n")

        # Phase-specific instructions
        parts.append(self._phase.prompt_constant)

        # Iteration marker
        parts.append(
            f"\n\n### Current State\n"
            f"- Phase: {self._phase.name}\n"
            f"- Phase iteration: {self._iteration}/{self.MAX_ITERATIONS_PER_PHASE}\n"
            f"- Total iteration: {self._total_iteration}/{self.MAX_TOTAL_ITERATIONS}\n"
        )

        # Previous phase output (for phases after DISCOVERY)
        if self._phase != ArchitectPhase.DISCOVERY:
            prev_phase = self._get_previous_phase()
            if prev_phase and prev_phase in self._phase_outputs:
                prev_output = self._phase_outputs[prev_phase]
                parts.append(
                    f"\n## Output from {prev_phase.name} Phase\n"
                    f"{prev_output[:4000]}\n"
                )

        # For CRITIQUE: include all alternatives
        if self._phase == ArchitectPhase.CRITIQUE and self._alternatives:
            parts.append("\n## Alternatives to Critique\n")
            for i, alt in enumerate(self._alternatives, 1):
                parts.append(
                    f"### Alternative {i}: {alt.name}\n"
                    f"Description: {alt.description}\n"
                    f"Components: {', '.join(c.get('name', '?') for c in alt.components)}\n"
                    f"Scorecard:\n"
                    f"{alt.scorecard.to_markdown_table()}\n"
                )

        # For REFINEMENT: include critiques
        if self._phase == ArchitectPhase.REFINEMENT and self._alternatives:
            parts.append("\n## Alternative to Refine\n")
            # Find the highest-scored alternative
            scored = TradeOffScorer.rank_alternatives(self._alternatives)
            if scored:
                best = scored[0][0]
                parts.append(f"**Selected Alternative:** {best.name}\n")
                if best.critique_notes:
                    parts.append(f"**Critique Notes:**\n{best.critique_notes}\n")

        return "\n".join(parts)

    def _format_phase_history(self) -> str:
        """Format recent phase execution history."""
        if not self._phase_history:
            return "(no actions yet)"

        recent = self._phase_history[-20:]
        return "\n".join(f"  {h}" for h in recent)

    def _get_previous_phase(self) -> Optional[ArchitectPhase]:
        """Get the phase that executed before the current one."""
        order = [
            ArchitectPhase.DISCOVERY,
            ArchitectPhase.ANALYSIS,
            ArchitectPhase.DESIGN,
            ArchitectPhase.CRITIQUE,
            ArchitectPhase.REFINEMENT,
            ArchitectPhase.SYNTHESIS,
        ]
        idx = order.index(self._phase)
        return order[idx - 1] if idx > 0 else None

    # ── Output Processing ────────────────────────────────────────

    def _process_phase_output(self, output: str) -> None:
        """Parse and store structured data from the LLM output.

        Extracts:
        - ADRs from SYNTHESIS phase
        - Scorecard data from DESIGN phase
        - Risk entries from SYNTHESIS phase
        """
        self._phase_outputs[self._phase] = output

        if self._phase == ArchitectPhase.DESIGN:
            self._parse_alternatives_from_output(output)
        elif self._phase == ArchitectPhase.CRITIQUE:
            self._apply_critique_to_alternatives(output)
        elif self._phase == ArchitectPhase.SYNTHESIS:
            self._parse_adrs_from_output(output)
            self._parse_risks_from_output(output)

    def _parse_alternatives_from_output(self, output: str) -> None:
        """Parse architectural alternatives from DESIGN phase output."""
        # Look for structured alternative sections
        alt_pattern = re.compile(
            r'###\s+(?:Alternative\s+[A-Z]|Alternative\s+\d+)[:\s-]*\s*(.+?)(?=###|\Z)',
            re.DOTALL | re.IGNORECASE,
        )

        matches = alt_pattern.findall(output)
        if not matches:
            # Try looser pattern
            alt_pattern2 = re.compile(
                r'(?:Alternative|Option|Approach)\s+[A-Z\d][:\s-]*\s*(.+?)(?=(?:Alternative|Option|Approach)\s+[A-Z\d]|\Z)',
                re.DOTALL | re.IGNORECASE,
            )
            matches = alt_pattern2.findall(output)

        new_alternatives = []
        for i, match in enumerate(matches):
            name = self._extract_alternative_name(match, i)
            description = self._extract_alternative_description(match)
            components = self._extract_components(match)
            scorecard = self._extract_scorecard(match, name)
            pros, cons = self._extract_pros_cons(match)

            alt = ArchitecturalAlternative(
                name=name,
                description=description,
                components=components,
                scorecard=scorecard,
                pros=pros,
                cons=cons,
            )
            new_alternatives.append(alt)

        if new_alternatives:
            self._alternatives = new_alternatives
        else:
            # Create at least one alternative from the raw output
            self._alternatives = [
                ArchitecturalAlternative(
                    name="Design from LLM output",
                    description=output[:500],
                    scorecard=Scorecard(alternative_name="Design from LLM output"),
                )
            ]

    def _apply_critique_to_alternatives(self, output: str) -> None:
        """Apply critique findings to alternatives."""
        if not self._alternatives:
            return

        for alt in self._alternatives:
            if alt.name.lower() in output.lower():
                alt.critique_notes = output[:2000]

    def _parse_adrs_from_output(self, output: str) -> None:
        """Parse ADRs from SYNTHESIS output."""
        adr_pattern = re.compile(
            r'(?:###|##)\s*(ADR-\d+):\s*(.+?)(?=(?:###|##)\s*ADR-\d+:|\Z)',
            re.DOTALL,
        )

        matches = adr_pattern.findall(output)
        for adr_id, adr_content in matches:
            adr = ADR(adr_id=adr_id.strip(), title=adr_content.split("\n")[0].strip())

            # Extract status
            status_match = re.search(r'\*\*Status:\*\*\s*(.+)', adr_content)
            if status_match:
                adr.status = status_match.group(1).strip()

            # Extract context
            context_match = re.search(
                r'\*\*Context:\*\*\s*\n(.+?)(?=\*\*Decision:\*\*|\Z)',
                adr_content, re.DOTALL,
            )
            if context_match:
                adr.context = context_match.group(1).strip()

            # Extract decision
            decision_match = re.search(
                r'\*\*Decision:\*\*\s*\n(.+?)(?=\*\*(?:Alternatives|Consequences|Confidence):\*\*|\Z)',
                adr_content, re.DOTALL,
            )
            if decision_match:
                adr.decision = decision_match.group(1).strip()

            # Extract consequences
            cons_match = re.search(
                r'\*\*Consequences:\*\*\s*\n(.+?)(?=\*\*(?:Confidence):\*\*|\Z)',
                adr_content, re.DOTALL,
            )
            if cons_match:
                adr.consequences = cons_match.group(1).strip()

            # Extract confidence
            conf_match = re.search(
                r'\*\*Confidence:\*\*\s*(.+)',
                adr_content,
            )
            if conf_match:
                adr.confidence = conf_match.group(1).strip()

            self._adrs.append(adr)

    def _parse_risks_from_output(self, output: str) -> None:
        """Parse risk register entries from SYNTHESIS output."""
        # Look for risk table rows
        table_pattern = re.compile(
            r'\|\s*(R\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|',
        )

        for match in table_pattern.finditer(output):
            risk_id = match.group(1).strip()
            if risk_id.startswith("R") and risk_id[1:].isdigit():
                risk = RiskEntry(
                    risk_id=risk_id,
                    description=match.group(2).strip(),
                    likelihood=self._parse_likelihood(match.group(3).strip()),
                    impact=self._parse_impact(match.group(4).strip()),
                    mitigation=match.group(5).strip(),
                    trigger=match.group(6).strip(),
                )
                self._risk_register.append(risk)

    # ── Extraction Helpers ───────────────────────────────────────

    @staticmethod
    def _extract_alternative_name(text: str, index: int) -> str:
        """Extract alternative name from text."""
        first_line = text.strip().split("\n")[0].strip()
        # Remove markdown formatting
        first_line = re.sub(r'[*_#]', '', first_line).strip()
        if first_line and len(first_line) < 80:
            return first_line
        return f"Alternative {chr(65 + index)}"

    @staticmethod
    def _extract_alternative_description(text: str) -> str:
        """Extract description from alternative text."""
        desc_match = re.search(
            r'(?:Description|Approach|Overview)[:\s-]*\s*\n?(.+?)(?=\n\n|\n(?:Components|Data Flow|Integration|Scorecard|Pros|Cons))',
            text, re.DOTALL | re.IGNORECASE,
        )
        if desc_match:
            return desc_match.group(1).strip()
        # Fallback: first substantial paragraph
        lines = text.strip().split("\n")
        paragraphs = []
        current = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("*") or line.startswith("-"):
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                if not line:
                    continue
            current.append(line)
        if current:
            paragraphs.append(" ".join(current))
        for p in paragraphs:
            if len(p) > 40 and not p.startswith("```"):
                return p[:500]
        return text[:500]

    @staticmethod
    def _extract_components(text: str) -> List[Dict[str, str]]:
        """Extract component list from alternative text."""
        components = []
        comp_pattern = re.compile(
            r'[-*]\s*\*\*(.+?)\*\*[:\s]*(.+)',
        )
        for match in comp_pattern.finditer(text):
            name = match.group(1).strip()
            role = match.group(2).strip()
            if not name.lower().startswith(("pros", "cons", "score", "desc", "appro")):
                components.append({"name": name, "role": role})
        return components[:10]

    @staticmethod
    def _extract_scorecard(text: str, name: str) -> Scorecard:
        """Extract scorecard from alternative text."""
        scorecard = Scorecard(alternative_name=name)

        # Look for table-like score entries
        score_pattern = re.compile(
            r'(?:Simplicity|Performance|Maintainability|Extensibility|Risk|Alignment|Testability|Scalability)[:\s]*(\d+(?:\.\d+)?)\s*(?:/10)?',
            re.IGNORECASE,
        )

        for dim_str in ScoreDimension:
            pattern = rf'{dim_str.value}[:\s]*(\d+(?:\.\d+)?)'
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                scorecard.set_score(dim_str, min(10.0, max(1.0, float(m.group(1)))))

        return scorecard

    @staticmethod
    def _extract_pros_cons(text: str) -> Tuple[List[str], List[str]]:
        """Extract pros and cons from alternative text."""
        pros = []
        cons = []
        in_pros = False
        in_cons = False

        for line in text.split("\n"):
            line_lower = line.strip().lower()
            if line_lower.startswith("**pros"):
                in_pros = True
                in_cons = False
                continue
            if line_lower.startswith("**cons"):
                in_cons = True
                in_pros = False
                continue
            if line_lower.startswith("**") and not line_lower.startswith("**pros") and not line_lower.startswith("**cons"):
                in_pros = False
                in_cons = False

            if in_pros and line.strip().startswith(("-", "*", "✅")):
                pros.append(re.sub(r'^[-*✅]\s*', '', line.strip()))
            if in_cons and line.strip().startswith(("-", "*", "❌")):
                cons.append(re.sub(r'^[-*❌]\s*', '', line.strip()))

        return pros, cons

    @staticmethod
    def _parse_likelihood(text: str) -> RiskLikelihood:
        """Parse risk likelihood from text."""
        text_lower = text.lower()
        for level in RiskLikelihood:
            if level.value.lower() in text_lower:
                return level
        return RiskLikelihood.MEDIUM

    @staticmethod
    def _parse_impact(text: str) -> RiskImpact:
        """Parse risk impact from text."""
        text_lower = text.lower()
        for level in RiskImpact:
            if level.value.lower() in text_lower:
                return level
        return RiskImpact.MODERATE

    # ── Tool Execution ──────────────────────────────────────────

    def _execute_tools_from_output(self, output: str) -> None:
        """Parse and execute tool calls from LLM output.

        Supports: read, write, edit, glob, grep, bash.
        """
        lines = output.strip().splitlines()
        executed_count = 0

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("```"):
                continue

            if line.startswith("read("):
                self._execute_read(line)
                executed_count += 1
            elif line.startswith("write("):
                self._execute_write(line)
                executed_count += 1
            elif line.startswith("edit("):
                self._execute_edit(line)
                executed_count += 1
            elif line.startswith("glob("):
                self._execute_glob(line)
                executed_count += 1
            elif line.startswith("grep("):
                self._execute_grep(line)
                executed_count += 1
            elif line.startswith("bash("):
                self._execute_bash(line)
                executed_count += 1

            # Limit tool executions per iteration
            if executed_count >= 20:
                break

    def _execute_read(self, line: str) -> None:
        path = self._extract_arg(line, "path")
        if not path:
            return
        full_path = os.path.join(self._cwd, path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.context.set_artifact(f"read_{path}", content[:5000])
                self.context.append_artifact_list("files_read", path)
            except Exception as e:
                logger.warning(f"read({path}) failed: {e}")

    def _execute_write(self, line: str) -> None:
        path = self._extract_arg(line, "path")
        content = self._extract_arg(line, "content")
        if path and content:
            full_path = os.path.join(self._cwd, path)
            try:
                os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.context.append_artifact_list("files_written", path)
            except Exception as e:
                logger.warning(f"write({path}) failed: {e}")

    def _execute_edit(self, line: str) -> None:
        path = self._extract_arg(line, "path")
        old = self._extract_arg(line, "old_string") or self._extract_arg(line, "old")
        new = self._extract_arg(line, "new_string") or self._extract_arg(line, "new")
        if not (path and old and new):
            return
        full_path = os.path.join(self._cwd, path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if old in content:
                    content = content.replace(old, new, 1)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    self.context.append_artifact_list("files_modified", path)
            except Exception as e:
                logger.warning(f"edit({path}) failed: {e}")

    def _execute_glob(self, line: str) -> None:
        pattern = self._extract_arg(line, "pattern")
        if not pattern:
            return
        try:
            import glob as glob_mod
            files = glob_mod.glob(pattern, root_dir=self._cwd, recursive=True)
            self.context.set_artifact(f"glob_{pattern}", files[:100])
            self.context.append_artifact_list("glob_searches", pattern)
        except Exception as e:
            logger.warning(f"glob({pattern}) failed: {e}")

    def _execute_grep(self, line: str) -> None:
        pattern = self._extract_arg(line, "pattern")
        search_path = self._extract_arg(line, "path") or "."
        if not pattern:
            return
        try:
            full_path = os.path.join(self._cwd, search_path)
            matches = []
            if os.path.isdir(full_path):
                for root, _dirs, files in os.walk(full_path):
                    for fname in files:
                        if fname.endswith(".py"):
                            fpath = os.path.join(root, fname)
                            try:
                                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                                    for i, fline in enumerate(f, 1):
                                        if re.search(pattern, fline):
                                            matches.append(
                                                f"{os.path.relpath(fpath, self._cwd)}:{i}: {fline.strip()}"
                                            )
                            except (UnicodeDecodeError, PermissionError):
                                pass
            self.context.set_artifact(f"grep_{pattern}", matches[:50])
            self.context.append_artifact_list("grep_searches", pattern)
        except Exception as e:
            logger.warning(f"grep({pattern}) failed: {e}")

    def _execute_bash(self, line: str) -> None:
        cmd = self._extract_arg(line, "command")
        if not cmd:
            return
        # Safety: block dangerous commands
        dangerous = ["rm -rf /", "dd if=", "fork bomb", ":(){ :|:& };:"]
        if any(d in cmd for d in dangerous):
            logger.warning(f"Blocked dangerous command: {cmd}")
            return
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=self._cwd,
            )
            self.context.set_artifact(f"bash_{cmd[:30]}", {
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
            })
            self.context.append_artifact_list("shell_commands", cmd)
        except subprocess.TimeoutExpired:
            logger.warning(f"bash({cmd[:30]}) timed out")
        except Exception as e:
            logger.warning(f"bash({cmd[:30]}) failed: {e}")

    @staticmethod
    def _extract_arg(command_line: str, arg_name: str) -> Optional[str]:
        """Extract a key=value argument from a command line.

        Handles: key="value", key='value', key=value.
        """
        pattern = rf'{arg_name}=["\']([^"\']*)["\']|{arg_name}=([^\s,)]+)'
        m = re.search(pattern, command_line)
        if m:
            return m.group(1) or m.group(2)
        return None

    # ── Phase Completion Detection ───────────────────────────────

    def _is_phase_complete(self) -> bool:
        """Determine if the current phase has produced sufficient output."""
        phase_output = self._phase_outputs.get(self._phase, "")
        if not phase_output:
            return False

        # Minimum output length by phase
        min_lengths = {
            ArchitectPhase.DISCOVERY: 200,
            ArchitectPhase.ANALYSIS: 300,
            ArchitectPhase.DESIGN: 400,
            ArchitectPhase.CRITIQUE: 300,
            ArchitectPhase.REFINEMENT: 300,
            ArchitectPhase.SYNTHESIS: 500,
        }
        min_len = min_lengths.get(self._phase, 300)

        if len(phase_output) < min_len:
            return False

        # Specific checks per phase
        if self._phase == ArchitectPhase.DISCOVERY:
            return bool(self._discovery_findings or len(phase_output) > 500)
        if self._phase == ArchitectPhase.DESIGN:
            return len(self._alternatives) >= 1
        if self._phase == ArchitectPhase.SYNTHESIS:
            return len(self._adrs) >= 1 or "ADR-" in phase_output

        return True

    # ── Logging ──────────────────────────────────────────────────

    def _log_phase_start(self) -> None:
        logger.info(
            f"ArchitectAgent [{self.agent_id}]: "
            f"Starting {self._phase.name} phase"
        )

    def _log_phase_complete(self) -> None:
        output_len = len(self._phase_outputs.get(self._phase, ""))
        logger.info(
            f"ArchitectAgent [{self.agent_id}]: "
            f"Completed {self._phase.name} phase "
            f"({self._iteration} iter(s), {output_len} chars)",
            phase=self._phase.name,
            iterations=self._iteration,
            output_chars=output_len,
        )

    # ── Final Report ─────────────────────────────────────────────

    def _build_final_report(self) -> str:
        """Build the comprehensive final architectural report.

        Combines all phase outputs, structured data, and generates
        a unified document that the main agent can use.
        """
        # If SYNTHESIS produced output, use it as the primary report
        synthesis_output = self._phase_outputs.get(ArchitectPhase.SYNTHESIS, "")

        if synthesis_output and len(synthesis_output) > 500:
            # Prepend header
            header = (
                f"# Architect Agent Report\n\n"
                f"**Agent ID:** {self.agent_id}\n"
                f"**Task:** {self.context.task}\n"
                f"**Phases completed:** "
                f"{', '.join(p.name for p in self._phase_outputs)}\n"
                f"**Total iterations:** {self._total_iteration}\n"
                f"**Alternatives generated:** {len(self._alternatives)}\n"
                f"**ADRs produced:** {len(self._adrs)}\n"
                f"**Risks identified:** {len(self._risk_register)}\n\n"
                f"---\n\n"
            )
            return header + synthesis_output

        # Fallback: build report from structured data
        return ADRGenerator.build_report(
            title=self.context.task[:100],
            executive_summary=self._phase_outputs.get(
                ArchitectPhase.SYNTHESIS,
                self._phase_outputs.get(ArchitectPhase.DISCOVERY, "Analysis completed.")
            )[:2000],
            current_state=self._phase_outputs.get(
                ArchitectPhase.DISCOVERY, "No discovery performed."
            )[:2000],
            problem_decomposition=self._phase_outputs.get(
                ArchitectPhase.ANALYSIS, "No analysis performed."
            )[:2000],
            alternatives=self._alternatives,
            recommended=self._phase_outputs.get(
                ArchitectPhase.REFINEMENT, "See alternatives above."
            )[:2000],
            adrs=self._adrs,
            roadmap=self._phase_outputs.get(
                ArchitectPhase.REFINEMENT, "See recommended architecture."
            )[:2000],
            risk_register=self._risk_register if self._risk_register else [
                RiskEntry(
                    risk_id="R001",
                    description="Insufficient analysis — no risks formally identified",
                    mitigation="Perform manual risk assessment",
                )
            ],
            tradeoff_summary="See scorecards in Design Alternatives section above.",
        )