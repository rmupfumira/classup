"""Curriculum packs per jurisdiction.

A **curriculum pack** is a named catalogue of subjects a school teaches
under a specific examining body (ZIMSEC, Cambridge, CAPS, IEB, ...).
Super admin picks the tenant's **jurisdiction** (country); the tenant
admin then picks which **curriculum** they teach from the packs the
jurisdiction offers.

Applying a pack calls into ``academic_service.seed_curriculum_pack``,
which adds every pack subject whose ``code`` doesn't already exist on
the tenant. It never deletes or overwrites — admins can still edit or
remove individual subjects afterwards.

Zimbabwe
--------
- **ZIMSEC** — Primary (Grade 1-7) + O-Level (Form 1-4) + A-Level
  (Form 5-6). Shona / Ndebele as local languages; Heritage Studies,
  Family & Religious Studies, Combined Science at O-Level, separate
  sciences at A-Level.
- **Cambridge** — Cambridge Primary (Stage 1-6), IGCSE (Form 3-4),
  AS/A-Level (Form 5-6). Same core, different subject names
  (e.g. "Combined Science" → "Co-ordinated Sciences" at IGCSE).

South Africa
------------
- **CAPS** — Foundation Phase (R-3), Intermediate (4-6), Senior
  Phase (7-9), FET (10-12). Includes Afrikaans / Zulu / Xhosa as
  home-language options. The state (DBE) curriculum.
- **IEB** — Independent Examinations Board. Most private SA schools.
  Same subjects as CAPS at FET level; its differentiator is
  assessment style, not subject list. Pack mirrors CAPS with IEB
  branding so schools can switch exams later without re-mapping.
- **Cambridge** — Cambridge Primary + IGCSE + AS/A-Level. Used by
  many SA international schools.

Other jurisdictions
-------------------
Not covered here yet. Super admin can still set the country; tenant
admin can only pick **Custom** and build their subject list manually.
Add new packs by extending ``CURRICULUM_PACKS`` with the country's
ISO 3166-1 alpha-2 code.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubjectDef:
    """One subject within a curriculum pack."""

    code: str                       # Short unique code within pack+tenant
    name: str                       # Display name
    category: str                   # "PRIMARY" | "SECONDARY_O" | "SECONDARY_A" | "ALL"
    default_total_marks: int = 100
    description: str = ""


@dataclass(frozen=True)
class CurriculumPack:
    """A named catalogue of subjects."""

    code: str                       # Stable identifier, e.g. "ZW_ZIMSEC"
    name: str                       # Display name, e.g. "ZIMSEC"
    description: str                # One-sentence summary
    country_code: str               # ISO 3166-1 alpha-2 (ZW, ZA, ...)
    subjects: tuple[SubjectDef, ...]


# ---------------------------------------------------------------------------
# Zimbabwe — ZIMSEC
# Primary (Grade 1-7) + O-Level (Form 1-4) + A-Level (Form 5-6).
# ---------------------------------------------------------------------------

_ZIMSEC_SUBJECTS: tuple[SubjectDef, ...] = (
    # Primary (Grade 1-7)
    SubjectDef("ZS-ENG-P",    "English Language",             "PRIMARY"),
    SubjectDef("ZS-SHO-P",    "Shona",                        "PRIMARY"),
    SubjectDef("ZS-NDE-P",    "Ndebele",                      "PRIMARY"),
    SubjectDef("ZS-MAT-P",    "Mathematics",                  "PRIMARY"),
    SubjectDef("ZS-ES-P",     "Environmental Science",        "PRIMARY"),
    SubjectDef("ZS-ICT-P",    "ICT",                          "PRIMARY"),
    SubjectDef("ZS-AGR-P",    "Agriculture",                  "PRIMARY"),
    SubjectDef("ZS-PE-P",     "Physical Education",           "PRIMARY"),
    SubjectDef("ZS-VPA-P",    "Visual and Performing Arts",   "PRIMARY"),
    SubjectDef("ZS-SS-P",     "Social Studies",               "PRIMARY"),
    SubjectDef("ZS-FRE-P",    "Family and Religious Studies", "PRIMARY"),
    # O-Level (Form 1-4) — core
    SubjectDef("ZS-ENG-O",    "English Language",             "SECONDARY_O"),
    SubjectDef("ZS-LIT-O",    "Literature in English",        "SECONDARY_O"),
    SubjectDef("ZS-SHO-O",    "Shona",                        "SECONDARY_O"),
    SubjectDef("ZS-NDE-O",    "Ndebele",                      "SECONDARY_O"),
    SubjectDef("ZS-MAT-O",    "Mathematics",                  "SECONDARY_O"),
    SubjectDef("ZS-CS-O",     "Combined Science",             "SECONDARY_O"),
    SubjectDef("ZS-GEO-O",    "Geography",                    "SECONDARY_O"),
    SubjectDef("ZS-HIS-O",    "History",                      "SECONDARY_O"),
    SubjectDef("ZS-HER-O",    "Heritage Studies",             "SECONDARY_O"),
    SubjectDef("ZS-FRE-O",    "Family and Religious Studies", "SECONDARY_O"),
    # O-Level — commercial / practical options
    SubjectDef("ZS-BE-O",     "Business Enterprise and Skills","SECONDARY_O"),
    SubjectDef("ZS-ACC-O",    "Principles of Accounting",     "SECONDARY_O"),
    SubjectDef("ZS-COMP-O",   "Computer Science",             "SECONDARY_O"),
    SubjectDef("ZS-ICT-O",    "ICT",                          "SECONDARY_O"),
    SubjectDef("ZS-AGR-O",    "Agriculture",                  "SECONDARY_O"),
    SubjectDef("ZS-FN-O",     "Food Technology and Design",   "SECONDARY_O"),
    SubjectDef("ZS-DT-O",     "Design and Technology",        "SECONDARY_O"),
    SubjectDef("ZS-ART-O",    "Art",                          "SECONDARY_O"),
    SubjectDef("ZS-MUS-O",    "Music",                        "SECONDARY_O"),
    SubjectDef("ZS-PE-O",     "Physical Education",           "SECONDARY_O"),
    # A-Level (Form 5-6)
    SubjectDef("ZS-PMAT-A",   "Pure Mathematics",             "SECONDARY_A"),
    SubjectDef("ZS-MMAT-A",   "Mechanical Mathematics",       "SECONDARY_A"),
    SubjectDef("ZS-SMAT-A",   "Statistical Mathematics",      "SECONDARY_A"),
    SubjectDef("ZS-PHY-A",    "Physics",                      "SECONDARY_A"),
    SubjectDef("ZS-CHE-A",    "Chemistry",                    "SECONDARY_A"),
    SubjectDef("ZS-BIO-A",    "Biology",                      "SECONDARY_A"),
    SubjectDef("ZS-GEO-A",    "Geography",                    "SECONDARY_A"),
    SubjectDef("ZS-HIS-A",    "History",                      "SECONDARY_A"),
    SubjectDef("ZS-LIT-A",    "Literature in English",        "SECONDARY_A"),
    SubjectDef("ZS-SHO-A",    "Shona",                        "SECONDARY_A"),
    SubjectDef("ZS-NDE-A",    "Ndebele",                      "SECONDARY_A"),
    SubjectDef("ZS-ECO-A",    "Economics",                    "SECONDARY_A"),
    SubjectDef("ZS-ACC-A",    "Accounting",                   "SECONDARY_A"),
    SubjectDef("ZS-BM-A",     "Business Management",          "SECONDARY_A"),
    SubjectDef("ZS-COMP-A",   "Computer Science",             "SECONDARY_A"),
    SubjectDef("ZS-DIV-A",    "Divinity",                     "SECONDARY_A"),
    SubjectDef("ZS-SOC-A",    "Sociology",                    "SECONDARY_A"),
    SubjectDef("ZS-PA-A",     "Physical Education and Sport Science", "SECONDARY_A"),
    SubjectDef("ZS-GPAP-A",   "General Paper",                "SECONDARY_A"),
)


# ---------------------------------------------------------------------------
# South Africa — CAPS (Curriculum and Assessment Policy Statement, DBE)
# Foundation (R-3) + Intermediate (4-6) + Senior (7-9) + FET (10-12).
# Includes the main home-language options; schools delete the ones they
# don't offer.
# ---------------------------------------------------------------------------

_CAPS_SUBJECTS: tuple[SubjectDef, ...] = (
    # Foundation + Intermediate + Senior (consolidated — school picks which grades)
    SubjectDef("CP-ENG-P",    "English Home Language",            "PRIMARY"),
    SubjectDef("CP-ENGF-P",   "English First Additional Language","PRIMARY"),
    SubjectDef("CP-AFR-P",    "Afrikaans Home Language",          "PRIMARY"),
    SubjectDef("CP-AFRF-P",   "Afrikaans First Additional Language","PRIMARY"),
    SubjectDef("CP-ZUL-P",    "isiZulu",                          "PRIMARY"),
    SubjectDef("CP-XHO-P",    "isiXhosa",                         "PRIMARY"),
    SubjectDef("CP-SOT-P",    "Sesotho",                          "PRIMARY"),
    SubjectDef("CP-TSW-P",    "Setswana",                         "PRIMARY"),
    SubjectDef("CP-MAT-P",    "Mathematics",                      "PRIMARY"),
    SubjectDef("CP-NST-P",    "Natural Sciences and Technology",  "PRIMARY"),
    SubjectDef("CP-SS-P",     "Social Sciences",                  "PRIMARY"),
    SubjectDef("CP-LS-P",     "Life Skills",                      "PRIMARY"),
    SubjectDef("CP-EMS-P",    "Economic and Management Sciences", "PRIMARY"),
    SubjectDef("CP-CA-P",     "Creative Arts",                    "PRIMARY"),
    # FET (Grades 10-12) — Grade 12 is the NSC (matric)
    SubjectDef("CP-ENG-F",    "English Home Language",            "SECONDARY_O"),
    SubjectDef("CP-ENGF-F",   "English First Additional Language","SECONDARY_O"),
    SubjectDef("CP-AFR-F",    "Afrikaans",                        "SECONDARY_O"),
    SubjectDef("CP-ZUL-F",    "isiZulu",                          "SECONDARY_O"),
    SubjectDef("CP-XHO-F",    "isiXhosa",                         "SECONDARY_O"),
    SubjectDef("CP-MAT-F",    "Mathematics",                      "SECONDARY_O"),
    SubjectDef("CP-MLIT-F",   "Mathematical Literacy",            "SECONDARY_O"),
    SubjectDef("CP-LO-F",     "Life Orientation",                 "SECONDARY_O"),
    SubjectDef("CP-PS-F",     "Physical Sciences",                "SECONDARY_O"),
    SubjectDef("CP-LS-F",     "Life Sciences",                    "SECONDARY_O"),
    SubjectDef("CP-GEO-F",    "Geography",                        "SECONDARY_O"),
    SubjectDef("CP-HIS-F",    "History",                          "SECONDARY_O"),
    SubjectDef("CP-BS-F",     "Business Studies",                 "SECONDARY_O"),
    SubjectDef("CP-ACC-F",    "Accounting",                       "SECONDARY_O"),
    SubjectDef("CP-ECO-F",    "Economics",                        "SECONDARY_O"),
    SubjectDef("CP-IT-F",     "Information Technology",           "SECONDARY_O"),
    SubjectDef("CP-CAT-F",    "Computer Applications Technology", "SECONDARY_O"),
    SubjectDef("CP-EGD-F",    "Engineering Graphics and Design",  "SECONDARY_O"),
    SubjectDef("CP-AGS-F",    "Agricultural Sciences",            "SECONDARY_O"),
    SubjectDef("CP-CS-F",     "Consumer Studies",                 "SECONDARY_O"),
    SubjectDef("CP-HSP-F",    "Hospitality Studies",              "SECONDARY_O"),
    SubjectDef("CP-TOU-F",    "Tourism",                          "SECONDARY_O"),
    SubjectDef("CP-MUS-F",    "Music",                            "SECONDARY_O"),
    SubjectDef("CP-VIS-F",    "Visual Arts",                      "SECONDARY_O"),
    SubjectDef("CP-DRA-F",    "Dramatic Arts",                    "SECONDARY_O"),
    SubjectDef("CP-DAN-F",    "Dance Studies",                    "SECONDARY_O"),
    SubjectDef("CP-REL-F",    "Religion Studies",                 "SECONDARY_O"),
)


# ---------------------------------------------------------------------------
# South Africa — IEB (Independent Examinations Board)
# Mirrors CAPS at the FET subject level. IEB's differentiator is
# assessment and item-writing, not the subject catalogue. We keep the
# same subject NAMES as CAPS so schools can co-list; the pack code
# differs so admins can tell which curriculum they chose.
# ---------------------------------------------------------------------------

_IEB_SUBJECTS: tuple[SubjectDef, ...] = tuple(
    SubjectDef(
        code=s.code.replace("CP-", "IEB-"),
        name=s.name,
        category=s.category,
        default_total_marks=s.default_total_marks,
        description=s.description,
    )
    for s in _CAPS_SUBJECTS
)


# ---------------------------------------------------------------------------
# Cambridge International — used in both Zimbabwe and South Africa (and
# others). Cambridge Primary (Stage 1-6) + IGCSE (Y10-11, SA Grades 10-11,
# ZW Form 3-4) + AS / A-Level.
# ---------------------------------------------------------------------------

_CAMBRIDGE_SUBJECTS: tuple[SubjectDef, ...] = (
    # Cambridge Primary
    SubjectDef("CIE-ENG-P",   "Cambridge English",               "PRIMARY"),
    SubjectDef("CIE-MAT-P",   "Cambridge Mathematics",           "PRIMARY"),
    SubjectDef("CIE-SCI-P",   "Cambridge Science",               "PRIMARY"),
    SubjectDef("CIE-GLB-P",   "Cambridge Global Perspectives",   "PRIMARY"),
    SubjectDef("CIE-ICT-P",   "Cambridge Digital Literacy",      "PRIMARY"),
    # Cambridge Lower Secondary (ages 11-14)
    SubjectDef("CIE-ENG-LS",  "English (Lower Secondary)",       "SECONDARY_O"),
    SubjectDef("CIE-MAT-LS",  "Mathematics (Lower Secondary)",   "SECONDARY_O"),
    SubjectDef("CIE-SCI-LS",  "Science (Lower Secondary)",       "SECONDARY_O"),
    # IGCSE / O-Level (ages 14-16)
    SubjectDef("CIE-ENG-IG",  "English Language (0500)",         "SECONDARY_O"),
    SubjectDef("CIE-LIT-IG",  "English Literature (0475)",       "SECONDARY_O"),
    SubjectDef("CIE-MAT-IG",  "Mathematics (0580)",              "SECONDARY_O"),
    SubjectDef("CIE-AMAT-IG", "Additional Mathematics (0606)",   "SECONDARY_O"),
    SubjectDef("CIE-PHY-IG",  "Physics (0625)",                  "SECONDARY_O"),
    SubjectDef("CIE-CHE-IG",  "Chemistry (0620)",                "SECONDARY_O"),
    SubjectDef("CIE-BIO-IG",  "Biology (0610)",                  "SECONDARY_O"),
    SubjectDef("CIE-CSC-IG",  "Co-ordinated Sciences (0654)",    "SECONDARY_O"),
    SubjectDef("CIE-GEO-IG",  "Geography (0460)",                "SECONDARY_O"),
    SubjectDef("CIE-HIS-IG",  "History (0470)",                  "SECONDARY_O"),
    SubjectDef("CIE-BUS-IG",  "Business Studies (0450)",         "SECONDARY_O"),
    SubjectDef("CIE-ACC-IG",  "Accounting (0452)",               "SECONDARY_O"),
    SubjectDef("CIE-ECO-IG",  "Economics (0455)",                "SECONDARY_O"),
    SubjectDef("CIE-COMP-IG", "Computer Science (0478)",         "SECONDARY_O"),
    SubjectDef("CIE-ICT-IG",  "ICT (0417)",                      "SECONDARY_O"),
    SubjectDef("CIE-ART-IG",  "Art and Design (0400)",           "SECONDARY_O"),
    SubjectDef("CIE-MUS-IG",  "Music (0410)",                    "SECONDARY_O"),
    SubjectDef("CIE-PE-IG",   "Physical Education (0413)",       "SECONDARY_O"),
    SubjectDef("CIE-FRE-IG",  "French (0520)",                   "SECONDARY_O"),
    SubjectDef("CIE-GLB-IG",  "Global Perspectives (0457)",      "SECONDARY_O"),
    # AS / A-Level (ages 16-18)
    SubjectDef("CIE-ENG-A",   "English Language (9093)",         "SECONDARY_A"),
    SubjectDef("CIE-LIT-A",   "English Literature (9695)",       "SECONDARY_A"),
    SubjectDef("CIE-MAT-A",   "Mathematics (9709)",              "SECONDARY_A"),
    SubjectDef("CIE-FMAT-A",  "Further Mathematics (9231)",      "SECONDARY_A"),
    SubjectDef("CIE-PHY-A",   "Physics (9702)",                  "SECONDARY_A"),
    SubjectDef("CIE-CHE-A",   "Chemistry (9701)",                "SECONDARY_A"),
    SubjectDef("CIE-BIO-A",   "Biology (9700)",                  "SECONDARY_A"),
    SubjectDef("CIE-GEO-A",   "Geography (9696)",                "SECONDARY_A"),
    SubjectDef("CIE-HIS-A",   "History (9489)",                  "SECONDARY_A"),
    SubjectDef("CIE-BUS-A",   "Business (9609)",                 "SECONDARY_A"),
    SubjectDef("CIE-ACC-A",   "Accounting (9706)",               "SECONDARY_A"),
    SubjectDef("CIE-ECO-A",   "Economics (9708)",                "SECONDARY_A"),
    SubjectDef("CIE-COMP-A",  "Computer Science (9618)",         "SECONDARY_A"),
    SubjectDef("CIE-PSY-A",   "Psychology (9990)",               "SECONDARY_A"),
    SubjectDef("CIE-SOC-A",   "Sociology (9699)",                "SECONDARY_A"),
    SubjectDef("CIE-GLB-A",   "Global Perspectives (9239)",      "SECONDARY_A"),
)


# ---------------------------------------------------------------------------
# Pack registry — keyed by country code, then pack code.
#
# Each country exposes the packs we actively support. "Custom" is a
# universal fallback and NOT listed here — the API adds it to every
# response so admins can always opt out of a pack.
# ---------------------------------------------------------------------------

CURRICULUM_PACKS: dict[str, dict[str, CurriculumPack]] = {
    "ZW": {
        "ZW_ZIMSEC": CurriculumPack(
            code="ZW_ZIMSEC",
            name="ZIMSEC",
            description="Zimbabwe Schools Examinations Council — Primary, O-Level and A-Level.",
            country_code="ZW",
            subjects=_ZIMSEC_SUBJECTS,
        ),
        "ZW_CAMBRIDGE": CurriculumPack(
            code="ZW_CAMBRIDGE",
            name="Cambridge International",
            description="Cambridge Primary, IGCSE and AS/A-Level.",
            country_code="ZW",
            subjects=_CAMBRIDGE_SUBJECTS,
        ),
    },
    "ZA": {
        "ZA_CAPS": CurriculumPack(
            code="ZA_CAPS",
            name="CAPS (DBE)",
            description="South African Department of Basic Education — Foundation, Intermediate, Senior and FET phases.",
            country_code="ZA",
            subjects=_CAPS_SUBJECTS,
        ),
        "ZA_IEB": CurriculumPack(
            code="ZA_IEB",
            name="IEB",
            description="Independent Examinations Board — South African private-school equivalent to CAPS at NSC level.",
            country_code="ZA",
            subjects=_IEB_SUBJECTS,
        ),
        "ZA_CAMBRIDGE": CurriculumPack(
            code="ZA_CAMBRIDGE",
            name="Cambridge International",
            description="Cambridge Primary, IGCSE and AS/A-Level.",
            country_code="ZA",
            subjects=_CAMBRIDGE_SUBJECTS,
        ),
    },
}


CUSTOM_PACK_CODE = "CUSTOM"
"""Sentinel used by the API to represent 'no pack — I'll add subjects myself'.
It is not a real pack and ``get_pack(CUSTOM_PACK_CODE)`` returns None."""


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def get_packs_for_country(country_code: str) -> list[CurriculumPack]:
    """All packs available for a given country, in display order.

    An unknown country returns an empty list. The caller is expected to
    always offer ``CUSTOM`` as an option on top of whatever this returns
    — a tenant whose jurisdiction has no supported packs can still
    opt into Custom and build subjects manually.
    """
    if not country_code:
        return []
    packs = CURRICULUM_PACKS.get(country_code.upper(), {})
    return list(packs.values())


def get_pack(pack_code: str) -> CurriculumPack | None:
    """Look up a pack by its global ``code``. Returns None for Custom
    or any unknown value — callers treat None as 'nothing to seed'."""
    if not pack_code or pack_code == CUSTOM_PACK_CODE:
        return None
    for country_packs in CURRICULUM_PACKS.values():
        if pack_code in country_packs:
            return country_packs[pack_code]
    return None
