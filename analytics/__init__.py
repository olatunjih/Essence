"""
Essence Analytics Engine — domain-agnostic statistical analysis and reasoning.

Modules:
    models          — core data structures (DatasetFingerprint, EntityProfile,
                      DomainLens, Finding, AnalyticalStateBus)
    engine          — orchestration layer (AnalyticalCore, WaveController)
    layers          — L0–L7 analysis pipeline implementations
    spine           — AnalyticalIntentLayer and shared state bus wiring
    resilience      — data immune system, robust statistics, contradiction
                      detection, temporal drift, self-healing
    learning        — strategy optimiser, pattern memory, confidence calibration
    domain_lens     — universal domain-abstraction (DomainLensManager)
    analysis_critic — statistical and logical finding validation
    analysis_verifier — claim-vs-finding verification
    reward          — analytical reward scoring and delayed reward queue
    prompt_evolver  — archetype-aware prompt pool and arm selection
    data_tools      — data ingestion helpers
    ml_tools        — ML utility helpers
    vision          — vision/image analysis helpers
    speech          — speech/audio helpers
    experiment      — experiment tracking
"""
from .models            import *   # noqa: F401,F403
from .spine             import *   # noqa: F401,F403
from .layers            import *   # noqa: F401,F403
from .analysis_critic   import *   # noqa: F401,F403
from .analysis_verifier import *   # noqa: F401,F403
from .reward            import *   # noqa: F401,F403
from .prompt_evolver    import *   # noqa: F401,F403
from .engine            import *   # noqa: F401,F403
from .resilience        import *   # noqa: F401,F403
from .learning          import *   # noqa: F401,F403
from .domain_lens       import *   # noqa: F401,F403
from .data_tools        import *   # noqa: F401,F403
from .ml_tools          import *   # noqa: F401,F403
from .vision            import *   # noqa: F401,F403
from .speech            import *   # noqa: F401,F403
from .experiment        import *   # noqa: F401,F403
