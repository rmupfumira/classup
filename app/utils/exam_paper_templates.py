"""Paper structure templates per curriculum pack.

ZIMSEC O-Level and Cambridge IGCSE subjects typically examine via
multiple papers that aggregate to a single subject grade via
weighted average. These templates are the "what the exam board
actually asks for" defaults.

Schools can still edit the paper list on each exam — these are
sensible seeds, not rigid rules. The service's
``apply_curriculum_papers(exam)`` call reads these and rebuilds
the exam's paper rows.

Lookup key: (curriculum_pack_code, subject_code) → list of
``PaperSpec(name, total_marks, weight_percent, duration_minutes)``.
A code that isn't listed falls back to the single-paper default
the generic ``create_exam`` path produces.

Weights sum to 100 for each subject. If you edit a list, keep
that invariant — the service validates it before saving so a
typo won't silently skew a student's final mark.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperSpec:
    name: str                    # Short label — "Paper 1 (Composition)"
    total_marks: int             # Raw out-of on that paper
    weight_percent: int          # Share of subject's final mark (0..100)
    duration_minutes: int        # Admins can tweak


# ---------------------------------------------------------------------------
# Standard shapes — reused across many subjects
# ---------------------------------------------------------------------------

_P1_P2_EQUAL = [
    PaperSpec("Paper 1", 50, 50, 120),
    PaperSpec("Paper 2", 50, 50, 120),
]

_P1_P2_P3_SCIENCE = [
    PaperSpec("Paper 1 - Multiple Choice", 40, 30, 60),
    PaperSpec("Paper 2 - Structured", 80, 50, 120),
    PaperSpec("Paper 3 - Practical / Alternative", 30, 20, 90),
]

_MATHS_LIKE = [
    # Paper 1 shorter, Paper 2 longer + calculator allowed. Weights
    # reflect the longer paper carrying more of the grade.
    PaperSpec("Paper 1 - Non-calculator", 40, 30, 90),
    PaperSpec("Paper 2 - Calculator", 100, 70, 150),
]

_COMPUTER_LIKE = [
    PaperSpec("Paper 1 - Theory", 80, 60, 120),
    PaperSpec("Paper 2 - Practical", 50, 40, 150),
]

_BUSINESS_LIKE = [
    PaperSpec("Paper 1 - Theory", 80, 70, 120),
    PaperSpec("Paper 2 - SBA / Coursework", 30, 30, 0),  # 0 = no exam room time
]


# ---------------------------------------------------------------------------
# ZIMSEC O-Level (ZW_ZIMSEC) paper structures
# ---------------------------------------------------------------------------

_ZIMSEC_O_LEVEL: dict[str, list[PaperSpec]] = {
    # English
    "ZS-ENG-O": [
        PaperSpec("Paper 1 - Composition", 70, 50, 120),
        PaperSpec("Paper 2 - Comprehension & Summary", 50, 50, 105),
    ],
    "ZS-LIT-O": [
        PaperSpec("Paper 1 - Response & Essay", 60, 50, 150),
        PaperSpec("Paper 2 - Context & Open", 40, 50, 120),
    ],
    # Local languages
    "ZS-SHO-O": _P1_P2_EQUAL,
    "ZS-NDE-O": _P1_P2_EQUAL,
    # Maths
    "ZS-MAT-O": _MATHS_LIKE,
    # Sciences
    "ZS-CS-O": _P1_P2_P3_SCIENCE,
    # Humanities
    "ZS-GEO-O": _P1_P2_EQUAL,
    "ZS-HIS-O": _P1_P2_EQUAL,
    "ZS-HER-O": _P1_P2_EQUAL,
    "ZS-FRE-O": _P1_P2_EQUAL,
    # Commercial / practical
    "ZS-BE-O": _BUSINESS_LIKE,
    "ZS-ACC-O": _P1_P2_EQUAL,
    "ZS-COMP-O": _COMPUTER_LIKE,
    "ZS-ICT-O": _COMPUTER_LIKE,
    "ZS-AGR-O": _P1_P2_P3_SCIENCE,
    "ZS-FN-O": _COMPUTER_LIKE,  # Food Tech — theory + practical
    "ZS-DT-O": _COMPUTER_LIKE,  # Design & Tech — theory + practical
    "ZS-ART-O": [
        PaperSpec("Paper 1 - Still Life / Imaginative", 100, 50, 180),
        PaperSpec("Paper 2 - Design / Study", 100, 50, 180),
    ],
    "ZS-MUS-O": [
        PaperSpec("Paper 1 - Listening & Theory", 60, 60, 90),
        PaperSpec("Paper 2 - Performance", 40, 40, 0),
    ],
    "ZS-PE-O": _P1_P2_EQUAL,
}


# ---------------------------------------------------------------------------
# Cambridge IGCSE (ZW_CAMBRIDGE) — same shape, different codes
# ---------------------------------------------------------------------------

_CAMBRIDGE_IGCSE: dict[str, list[PaperSpec]] = {
    # Cambridge uses numbered papers, often 2-4 per subject
    "CAM-ENG-O": [
        PaperSpec("Paper 1 - Reading & Directed Writing", 80, 50, 120),
        PaperSpec("Paper 2 - Writer's Craft", 50, 50, 120),
    ],
    "CAM-MAT-O": [
        PaperSpec("Paper 1 - Short Answer (Core)", 60, 40, 60),
        PaperSpec("Paper 2 - Structured (Ext)", 100, 60, 120),
    ],
    # Co-ordinated Sciences in IGCSE usually 3 papers mirroring ZIMSEC
    "CAM-CS-O": _P1_P2_P3_SCIENCE,
    "CAM-GEO-O": _P1_P2_EQUAL,
    "CAM-HIS-O": _P1_P2_EQUAL,
    "CAM-LIT-O": _P1_P2_EQUAL,
    "CAM-SHO-O": _P1_P2_EQUAL,
    "CAM-NDE-O": _P1_P2_EQUAL,
    "CAM-ACC-O": _P1_P2_EQUAL,
    "CAM-COMP-O": _COMPUTER_LIKE,
    "CAM-ICT-O": _COMPUTER_LIKE,
    "CAM-ART-O": [
        PaperSpec("Paper 1 - Observational", 100, 50, 180),
        PaperSpec("Paper 2 - Design Study", 100, 50, 180),
    ],
    "CAM-BUS-O": _BUSINESS_LIKE,
    "CAM-FRN-O": _P1_P2_EQUAL,
    "CAM-SPA-O": _P1_P2_EQUAL,
}


# ---------------------------------------------------------------------------
# Pack lookup
# ---------------------------------------------------------------------------

_PACK_TEMPLATES: dict[str, dict[str, list[PaperSpec]]] = {
    "ZW_ZIMSEC": _ZIMSEC_O_LEVEL,
    "ZW_CAMBRIDGE": _CAMBRIDGE_IGCSE,
    # Combined curriculum: subjects come from either pack, so try both
    # and return whichever matches.
    "ZW_COMBINED": {**_ZIMSEC_O_LEVEL, **_CAMBRIDGE_IGCSE},
}


def papers_for(pack_code: str | None, subject_code: str | None) -> list[PaperSpec]:
    """Return the canonical paper list for (pack, subject), or an
    empty list when no template matches. Callers treat an empty list
    as "keep the one-paper default".
    """
    if not pack_code or not subject_code:
        return []
    table = _PACK_TEMPLATES.get(pack_code.upper())
    if not table:
        return []
    return list(table.get(subject_code.upper(), []))


def has_template_for_pack(pack_code: str | None) -> bool:
    if not pack_code:
        return False
    return pack_code.upper() in _PACK_TEMPLATES
