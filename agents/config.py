"""AgentConfig + AgentRole + AgentCapabilities.
Forward references to Memory and WorkflowStep are resolved explicitly."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403


class AgentConfig(BaseModel):
    """Pydantic v2 agent configuration — validated on construction."""
    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    provider:       Any              # ProviderChain
    model:          str
    workspace:      Path
    thinking:       bool  = False    # applies to executor; planner always thinks
    budget:         int   = 1024     # thinking token budget
    max_steps:      int   = 12
    critic:         bool  = True
    memory_window:  int   = 10       # turns before distillation
    use_tools:      bool  = True
    allow_outside:  bool  = False    # workspace sandbox bypass
    autonomy_level: int   = 1        # 0=confirm every tool call,
                                     # 1=confirm destructive tools only,
                                     # 2=fully autonomous (no confirmations)
    # v16: session identifier used to key the BrowserSession pool so that
    # browser_open / browser_click / browser_fill / browser_extract all
    # operate on the same live Playwright page within one agent task.
    session_id:     str   = ""
    # Production / team fields
    team_id:        str   = _TEAM_ID   # memory namespace; "local" = per-user
    cost_budget:    int   = _COST_BUDGET  # max tokens per task; 0 = unlimited
    sop_dir:        str   = _SOP_DIR   # path to SOP markdown procedures dir

    @model_validator(mode='after')
    def _validate_ranges(self) -> 'AgentConfig':
        if self.autonomy_level not in (0, 1, 2):
            raise ValueError(
                f'autonomy_level must be 0, 1, or 2; got {self.autonomy_level}')
        if self.budget < 64:
            raise ValueError(f'budget must be >= 64; got {self.budget}')
        if self.max_steps < 1:
            raise ValueError(f'max_steps must be >= 1; got {self.max_steps}')
        if self.memory_window < 2:
            raise ValueError(
                f'memory_window must be >= 2; got {self.memory_window}')
        return self




# Resolve forward references: Memory and WorkflowStep must be imported
# before model_rebuild() is called.  The try/except is intentional
# (pydantic v1 has no model_rebuild), but we log failures rather than
# silently swallowing them.
try:
    from essence.memory.memory import Memory          # noqa: F401
    from essence.agents.workflow import WorkflowStep  # noqa: F401
    AgentConfig.model_rebuild()
except ImportError as _e:
    import logging as _logging
    _logging.getLogger("essence.agents.config").debug(
        "AgentConfig.model_rebuild skipped: %s", _e)
except Exception as _e:
    import logging as _logging
    _logging.getLogger("essence.agents.config").warning(
        "AgentConfig.model_rebuild failed: %s", _e)
