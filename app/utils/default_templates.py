"""Default report templates for different education types."""

# Primary School Term Report Card Template
PRIMARY_SCHOOL_REPORT_CARD = {
    "name": "Primary School Term Report Card",
    "description": "End of term academic report card for primary school students (Grades 1-7)",
    "report_type": "REPORT_CARD",
    "frequency": "TERMLY",
    "applies_to_grade_level": "GRADE_1,GRADE_2,GRADE_3,GRADE_4,GRADE_5,GRADE_6,GRADE_7",
    "sections": [
        {
            "id": "student_info",
            "title": "Student Information",
            "type": "INFO_DISPLAY",
            "color": "gray",
            "display_order": 1,
            "fields": [
                {"id": "term", "label": "Term", "type": "SELECT", "required": True,
                 "options": ["Term 1", "Term 2", "Term 3", "Term 4"]},
                {"id": "year", "label": "Academic Year", "type": "TEXT", "required": True},
                {"id": "days_present", "label": "Days Present", "type": "NUMBER", "required": False},
                {"id": "days_absent", "label": "Days Absent", "type": "NUMBER", "required": False},
                {"id": "total_days", "label": "Total School Days", "type": "NUMBER", "required": False}
            ]
        },
        {
            "id": "academic_grades",
            "title": "Academic Performance",
            "type": "ACADEMIC_GRADES",
            "color": "blue",
            "display_order": 2,
            "subjects": [
                {"id": "english", "name": "English", "total_marks": 100},
                {"id": "mathematics", "name": "Mathematics", "total_marks": 100},
                {"id": "science", "name": "Science", "total_marks": 100},
                {"id": "social_studies", "name": "Social Studies", "total_marks": 100},
                {"id": "local_language", "name": "Local Language", "total_marks": 100},
                {"id": "physical_education", "name": "Physical Education", "total_marks": 50},
                {"id": "art", "name": "Art & Craft", "total_marks": 50},
                {"id": "music", "name": "Music", "total_marks": 50},
                {"id": "life_skills", "name": "Life Skills", "total_marks": 50}
            ],
            "grading_system": [
                {"min": 80, "max": 100, "grade": "A", "description": "Outstanding"},
                {"min": 70, "max": 79, "grade": "B", "description": "Very Good"},
                {"min": 60, "max": 69, "grade": "C", "description": "Good"},
                {"min": 50, "max": 59, "grade": "D", "description": "Satisfactory"},
                {"min": 40, "max": 49, "grade": "E", "description": "Needs Improvement"},
                {"min": 0, "max": 39, "grade": "F", "description": "Fail"}
            ],
            "fields": [
                {"id": "marks_obtained", "label": "Marks Obtained", "type": "NUMBER", "required": True},
                {"id": "remarks", "label": "Remarks", "type": "TEXT", "required": False}
            ]
        },
        {
            "id": "summary",
            "title": "Overall Summary",
            "type": "SUMMARY",
            "color": "green",
            "display_order": 3,
            "fields": [
                {"id": "total_marks", "label": "Total Marks", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "marks_obtained", "label": "Total Marks Obtained", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "percentage", "label": "Overall Percentage", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "grade", "label": "Overall Grade", "type": "TEXT", "required": False, "auto_calculate": True},
                {"id": "rank", "label": "Class Rank", "type": "NUMBER", "required": False},
                {"id": "out_of", "label": "Out of (Students)", "type": "NUMBER", "required": False}
            ]
        },
        {
            "id": "conduct",
            "title": "Conduct & Behavior",
            "type": "CHECKLIST",
            "color": "purple",
            "display_order": 4,
            "fields": [
                {"id": "punctuality", "label": "Punctuality", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "discipline", "label": "Discipline", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "participation", "label": "Class Participation", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "homework", "label": "Homework Completion", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "teamwork", "label": "Teamwork", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]}
            ]
        },
        {
            "id": "teacher_comments",
            "title": "Teacher's Comments",
            "type": "NARRATIVE",
            "color": "gray",
            "display_order": 5,
            "fields": [
                {"id": "strengths", "label": "Strengths", "type": "TEXTAREA", "required": False},
                {"id": "areas_for_improvement", "label": "Areas for Improvement", "type": "TEXTAREA", "required": False},
                {"id": "general_comments", "label": "General Comments", "type": "TEXTAREA", "required": False}
            ]
        },
        {
            "id": "signatures",
            "title": "Signatures",
            "type": "SIGNATURES",
            "color": "gray",
            "display_order": 6,
            "fields": [
                {"id": "class_teacher", "label": "Class Teacher", "type": "SIGNATURE", "required": False},
                {"id": "principal", "label": "Principal", "type": "SIGNATURE", "required": False},
                {"id": "parent", "label": "Parent/Guardian", "type": "SIGNATURE", "required": False},
                {"id": "date", "label": "Date", "type": "DATE", "required": False}
            ]
        }
    ]
}

# High School Term Report Card Template
HIGH_SCHOOL_REPORT_CARD = {
    "name": "High School Term Report Card",
    "description": "End of term academic report card for high school students (Grades 8-12)",
    "report_type": "REPORT_CARD",
    "frequency": "TERMLY",
    "applies_to_grade_level": "GRADE_8,GRADE_9,GRADE_10,GRADE_11,GRADE_12",
    "sections": [
        {
            "id": "student_info",
            "title": "Student Information",
            "type": "INFO_DISPLAY",
            "color": "gray",
            "display_order": 1,
            "fields": [
                {"id": "term", "label": "Term", "type": "SELECT", "required": True,
                 "options": ["Term 1", "Term 2", "Term 3", "Term 4"]},
                {"id": "year", "label": "Academic Year", "type": "TEXT", "required": True},
                {"id": "days_present", "label": "Days Present", "type": "NUMBER", "required": False},
                {"id": "days_absent", "label": "Days Absent", "type": "NUMBER", "required": False},
                {"id": "total_days", "label": "Total School Days", "type": "NUMBER", "required": False}
            ]
        },
        {
            "id": "academic_grades",
            "title": "Academic Performance",
            "type": "ACADEMIC_GRADES",
            "color": "blue",
            "display_order": 2,
            "subjects": [
                {"id": "english", "name": "English", "total_marks": 100},
                {"id": "mathematics", "name": "Mathematics", "total_marks": 100},
                {"id": "physics", "name": "Physics", "total_marks": 100},
                {"id": "chemistry", "name": "Chemistry", "total_marks": 100},
                {"id": "biology", "name": "Biology", "total_marks": 100},
                {"id": "history", "name": "History", "total_marks": 100},
                {"id": "geography", "name": "Geography", "total_marks": 100},
                {"id": "economics", "name": "Economics", "total_marks": 100},
                {"id": "computer_science", "name": "Computer Science", "total_marks": 100},
                {"id": "physical_education", "name": "Physical Education", "total_marks": 50}
            ],
            "grading_system": [
                {"min": 90, "max": 100, "grade": "A+", "description": "Outstanding"},
                {"min": 80, "max": 89, "grade": "A", "description": "Excellent"},
                {"min": 70, "max": 79, "grade": "B+", "description": "Very Good"},
                {"min": 60, "max": 69, "grade": "B", "description": "Good"},
                {"min": 50, "max": 59, "grade": "C", "description": "Satisfactory"},
                {"min": 40, "max": 49, "grade": "D", "description": "Pass"},
                {"min": 0, "max": 39, "grade": "F", "description": "Fail"}
            ],
            "fields": [
                {"id": "marks_obtained", "label": "Marks Obtained", "type": "NUMBER", "required": True},
                {"id": "remarks", "label": "Remarks", "type": "TEXT", "required": False}
            ]
        },
        {
            "id": "summary",
            "title": "Overall Summary",
            "type": "SUMMARY",
            "color": "green",
            "display_order": 3,
            "fields": [
                {"id": "total_marks", "label": "Total Marks", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "marks_obtained", "label": "Total Marks Obtained", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "percentage", "label": "Overall Percentage", "type": "NUMBER", "required": False, "auto_calculate": True},
                {"id": "grade", "label": "Overall Grade", "type": "TEXT", "required": False, "auto_calculate": True},
                {"id": "rank", "label": "Class Rank", "type": "NUMBER", "required": False},
                {"id": "out_of", "label": "Out of (Students)", "type": "NUMBER", "required": False}
            ]
        },
        {
            "id": "conduct",
            "title": "Conduct & Behavior",
            "type": "CHECKLIST",
            "color": "purple",
            "display_order": 4,
            "fields": [
                {"id": "punctuality", "label": "Punctuality", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "discipline", "label": "Discipline", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "participation", "label": "Class Participation", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "leadership", "label": "Leadership", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]},
                {"id": "responsibility", "label": "Responsibility", "type": "SELECT", "required": False,
                 "options": ["Excellent", "Good", "Satisfactory", "Needs Improvement"]}
            ]
        },
        {
            "id": "extra_curricular",
            "title": "Extra-Curricular Activities",
            "type": "REPEATABLE_ENTRIES",
            "color": "orange",
            "display_order": 5,
            "fields": [
                {"id": "activity", "label": "Activity", "type": "TEXT", "required": True},
                {"id": "achievement", "label": "Achievement/Level", "type": "TEXT", "required": False},
                {"id": "remarks", "label": "Remarks", "type": "TEXT", "required": False}
            ]
        },
        {
            "id": "teacher_comments",
            "title": "Teacher's Comments",
            "type": "NARRATIVE",
            "color": "gray",
            "display_order": 6,
            "fields": [
                {"id": "strengths", "label": "Strengths", "type": "TEXTAREA", "required": False},
                {"id": "areas_for_improvement", "label": "Areas for Improvement", "type": "TEXTAREA", "required": False},
                {"id": "general_comments", "label": "General Comments", "type": "TEXTAREA", "required": False}
            ]
        },
        {
            "id": "signatures",
            "title": "Signatures",
            "type": "SIGNATURES",
            "color": "gray",
            "display_order": 7,
            "fields": [
                {"id": "class_teacher", "label": "Class Teacher", "type": "SIGNATURE", "required": False},
                {"id": "principal", "label": "Principal", "type": "SIGNATURE", "required": False},
                {"id": "parent", "label": "Parent/Guardian", "type": "SIGNATURE", "required": False},
                {"id": "date", "label": "Date", "type": "DATE", "required": False}
            ]
        }
    ]
}

# Daycare Daily Report Template
DAYCARE_DAILY_REPORT = {
    "name": "Daycare Daily Report",
    "description": "Daily activity report for daycare children including naps, meals, fluids, and bathroom",
    "report_type": "DAILY_ACTIVITY",
    "frequency": "DAILY",
    "applies_to_grade_level": None,
    "sections": [
        {
            "id": "naps",
            "title": "Naps",
            "type": "REPEATABLE_ENTRIES",
            "color": "purple",
            "display_order": 1,
            "fields": [
                {"id": "start_time", "label": "Start Time", "type": "TIME", "required": True},
                {"id": "end_time", "label": "End Time", "type": "TIME", "required": True},
                {"id": "duration", "label": "Duration", "type": "CALCULATED", "required": False}
            ]
        },
        {
            "id": "meals",
            "title": "Meals",
            "type": "MEALS",
            "color": "green",
            "display_order": 2,
            "fields": [
                {"id": "breakfast", "label": "Breakfast", "type": "MEAL_ENTRY", "required": False},
                {"id": "morning_snack", "label": "Morning Snack", "type": "MEAL_ENTRY", "required": False},
                {"id": "lunch", "label": "Lunch", "type": "MEAL_ENTRY", "required": False},
                {"id": "afternoon_snack", "label": "Afternoon Snack", "type": "MEAL_ENTRY", "required": False}
            ],
            "meal_options": ["All", "Most", "Some", "None", "N/A", "Own food"]
        },
        {
            "id": "fluids",
            "title": "Fluids",
            "type": "REPEATABLE_ENTRIES",
            "color": "blue",
            "display_order": 3,
            "fields": [
                {"id": "time", "label": "Time", "type": "TIME", "required": True},
                {"id": "amount", "label": "Amount", "type": "SELECT", "required": False,
                 "options": ["None", "Some", "Half", "Most", "All"]},
                {"id": "type", "label": "Type", "type": "SELECT", "required": True,
                 "options": ["Water", "Milk", "Juice", "Formula", "Other"]},
                {"id": "notes", "label": "Notes", "type": "TEXT", "required": False}
            ]
        },
        {
            "id": "bathroom",
            "title": "Bathroom",
            "type": "REPEATABLE_ENTRIES",
            "color": "orange",
            "display_order": 4,
            "fields": [
                {"id": "time", "label": "Time", "type": "TIME", "required": True},
                {"id": "type", "label": "Type", "type": "SELECT", "required": True,
                 "options": ["Diaper", "Potty", "Toilet"]},
                {"id": "condition", "label": "Condition", "type": "SELECT", "required": True,
                 "options": ["Wet", "Bowel movement", "Both", "Dry"]},
                {"id": "notes", "label": "Notes", "type": "TEXT", "required": False}
            ]
        },
        {
            "id": "activities",
            "title": "Activities",
            "type": "CHECKLIST",
            "color": "blue",
            "display_order": 5,
            "fields": [
                {"id": "outdoor_play", "label": "Outdoor Play", "type": "CHECKBOX", "required": False},
                {"id": "arts_crafts", "label": "Arts & Crafts", "type": "CHECKBOX", "required": False},
                {"id": "music", "label": "Music & Movement", "type": "CHECKBOX", "required": False},
                {"id": "story_time", "label": "Story Time", "type": "CHECKBOX", "required": False},
                {"id": "free_play", "label": "Free Play", "type": "CHECKBOX", "required": False},
                {"id": "sensory_play", "label": "Sensory Play", "type": "CHECKBOX", "required": False},
                {"id": "other_activities", "label": "Other Activities", "type": "TEXTAREA", "required": False}
            ]
        },
        {
            "id": "notes",
            "title": "Teacher's Notes",
            "type": "NARRATIVE",
            "color": "gray",
            "display_order": 6,
            "fields": [
                {"id": "content", "label": "Notes", "type": "TEXTAREA", "required": False}
            ]
        }
    ]
}


def get_default_templates_for_education_type(education_type: str) -> list[dict]:
    """Get default report templates based on education type."""
    templates = []

    if education_type in ("DAYCARE",):
        templates.append(DAYCARE_DAILY_REPORT)

    if education_type in ("PRIMARY_SCHOOL", "K12", "COMBINED"):
        templates.append(PRIMARY_SCHOOL_REPORT_CARD)

    if education_type in ("HIGH_SCHOOL", "K12", "COMBINED"):
        templates.append(HIGH_SCHOOL_REPORT_CARD)

    # For combined/K12, also add daycare template
    if education_type in ("COMBINED", "K12"):
        templates.append(DAYCARE_DAILY_REPORT)

    return templates
