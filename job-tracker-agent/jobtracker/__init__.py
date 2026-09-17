"""JOBFLOW — a deterministic, offline job-application pipeline agent.

Public surface::

    from jobtracker import JobFlowEngine

    engine = JobFlowEngine.open("jobflow.json")
    engine.add("Acme", "Backend Engineer", source="referral")
    engine.move("acme-backend-engineer", "screen")
    print(engine.render_status(today="2026-09-17"))

The engine never reads the wall clock and never touches the network: pass
``today`` explicitly and the same vault always produces the same report.
"""

from .analytics import (
    STAGE_MATURITY_DAYS,
    STALL_DAYS,
    aging_rows,
    funnel,
    funnel_rows,
    salary_summary,
    source_breakdown,
    stalled,
    summary,
    time_in_stage,
    weekly_activity,
)
from .engine import DEFAULT_CONFIG, JobFlowEngine
from .followups import RULE_LABELS, RULE_ORDER, SEVERITIES, Action, actions, plan
from .models import (
    ACTIVE,
    CLOSED,
    EVENT_KINDS,
    INTERVIEW_KINDS,
    PRE,
    SOURCES,
    STAGE_INDEX,
    STAGES,
    STATUS_LABELS,
    STATUSES,
    TERMINAL,
    WORK_MODES,
    Application,
    Event,
    Interview,
    JobFlowError,
    Note,
    NotFoundError,
    ValidationError,
    format_money,
    format_range,
)
from .pipeline import Pipeline
from .store import DEFAULT_VAULT_NAME, SCHEMA_VERSION, Vault, default_vault_path

__version__ = "1.0.0"

#: A short persona line used by the CLI banner and the API's /health payload.
TAGLINE = "JOBFLOW — your job search, with the maths done honestly."

__all__ = [
    "ACTIVE",
    "Action",
    "Application",
    "CLOSED",
    "DEFAULT_CONFIG",
    "DEFAULT_VAULT_NAME",
    "EVENT_KINDS",
    "Event",
    "INTERVIEW_KINDS",
    "Interview",
    "JobFlowEngine",
    "JobFlowError",
    "Note",
    "NotFoundError",
    "PRE",
    "Pipeline",
    "RULE_LABELS",
    "RULE_ORDER",
    "SCHEMA_VERSION",
    "SEVERITIES",
    "SOURCES",
    "STAGE_INDEX",
    "STAGE_MATURITY_DAYS",
    "STAGES",
    "STALL_DAYS",
    "STATUS_LABELS",
    "STATUSES",
    "TAGLINE",
    "TERMINAL",
    "ValidationError",
    "Vault",
    "WORK_MODES",
    "actions",
    "aging_rows",
    "default_vault_path",
    "format_money",
    "format_range",
    "funnel",
    "funnel_rows",
    "plan",
    "salary_summary",
    "source_breakdown",
    "stalled",
    "summary",
    "time_in_stage",
    "weekly_activity",
]
