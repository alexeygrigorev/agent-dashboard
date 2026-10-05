"""
Agent Dashboard package.
"""

__version__ = "0.1.0"

CANONICAL_PROJECT_IDS = (
    "agent-branches",
    "agent-dashboard",
    "quota-launcher",
    "agent-coordination",
)
PROJECT_ALIASES = {
    "agent-quota-launcher": "quota-launcher",
    "agent_quota_launcher": "quota-launcher",
    "agent_branches": "agent-branches",
    "agent_dashboard": "agent-dashboard",
    "agent_coordination": "agent-coordination",
}
UNATTRIBUTED_PROJECT_ID = "unattributed"


def canonical_project_id(project_id: str | None) -> str:
    """Map a raw project id onto a canonical id or unattributed."""
    if project_id in PROJECT_ALIASES:
        return PROJECT_ALIASES[project_id]
    if project_id in CANONICAL_PROJECT_IDS:
        return project_id
    return UNATTRIBUTED_PROJECT_ID
