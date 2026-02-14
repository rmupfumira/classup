#!/usr/bin/env python3
"""
Development seed data script.

Creates test data for development:
- 1 tenant (Sunshine Daycare)
- 1 school admin
- 2 teachers
- 1 class (Butterfly Room)
- 5 students

Usage:
    python scripts/seed.py

All test users have password: "password123"
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, engine
from app.models import Tenant, User, Student, SchoolClass, TeacherClass, ParentStudent
from app.models.report import ReportTemplate
from app.models.tenant import EducationType, get_default_tenant_settings
from app.models.user import Role
from app.models.student import AgeGroup, Gender
from app.utils.security import hash_password


# Primary School Term Report Card Template
PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE = {
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
HIGH_SCHOOL_REPORT_CARD_TEMPLATE = {
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
DAYCARE_DAILY_REPORT_TEMPLATE = {
    "name": "Daycare Daily Report",
    "description": "Daily activity report for daycare children including naps, meals, fluids, and bathroom",
    "report_type": "DAILY_ACTIVITY",
    "frequency": "DAILY",
    "applies_to_grade_level": "INFANT,TODDLER,PRESCHOOL",
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
                {"id": "duration", "label": "Duration", "type": "TEXT", "required": False}
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
                {"id": "amount", "label": "Amount (mL)", "type": "NUMBER", "required": True},
                {"id": "type", "label": "Type", "type": "SELECT", "required": True,
                 "options": ["Water", "Milk", "Juice", "Formula", "Other"]}
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
                {"id": "sensory_play", "label": "Sensory Play", "type": "CHECKBOX", "required": False}
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


# Default password for all test users
TEST_PASSWORD = "password123"


async def seed_database():
    """Seed the database with test data."""
    print("\n" + "=" * 50)
    print("ClassUp v2 - Seeding Development Data")
    print("=" * 50 + "\n")

    async with async_session_factory() as session:
        # Check if tenant already exists
        result = await session.execute(
            select(Tenant).where(Tenant.slug == "sunshine-daycare")
        )
        existing_tenant = result.scalar_one_or_none()

        if existing_tenant:
            print("Seed data already exists (tenant 'sunshine-daycare' found).")
            confirm = input("Delete and recreate? (y/n): ").strip().lower()
            if confirm != "y":
                print("Aborting.")
                return False

            # Manually delete all related records to avoid FK constraint issues
            print("Deleting existing data...")
            tenant_id = existing_tenant.id

            # Delete in proper order to avoid FK constraints
            from sqlalchemy import text
            await session.execute(text(f"DELETE FROM parent_students WHERE parent_id IN (SELECT id FROM users WHERE tenant_id = '{tenant_id}')"))
            await session.execute(text(f"DELETE FROM teacher_classes WHERE teacher_id IN (SELECT id FROM users WHERE tenant_id = '{tenant_id}')"))
            await session.execute(text(f"DELETE FROM teacher_classes WHERE class_id IN (SELECT id FROM school_classes WHERE tenant_id = '{tenant_id}')"))
            await session.execute(text(f"DELETE FROM daily_reports WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM report_templates WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM attendance_records WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM students WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM school_classes WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM users WHERE tenant_id = '{tenant_id}'"))
            await session.execute(text(f"DELETE FROM tenants WHERE id = '{tenant_id}'"))
            await session.commit()
            print("Existing data deleted.\n")

        # Create tenant
        print("Creating tenant: Sunshine Daycare...")
        tenant = Tenant(
            name="Sunshine Daycare",
            slug="sunshine-daycare",
            email="admin@sunshinedaycare.co.za",
            phone="+27 21 555 1234",
            address="123 Rainbow Street, Cape Town, 8001",
            education_type=EducationType.DAYCARE,
            settings=get_default_tenant_settings(EducationType.DAYCARE),
            is_active=True,
            onboarding_completed=True
        )
        session.add(tenant)
        await session.flush()  # Get the tenant ID

        # Create school admin
        print("Creating school admin: admin@sunshinedaycare.co.za...")
        admin = User(
            tenant_id=tenant.id,
            email="admin@sunshinedaycare.co.za",
            password_hash=hash_password(TEST_PASSWORD),
            first_name="Sarah",
            last_name="Johnson",
            phone="+27 82 555 0001",
            role=Role.SCHOOL_ADMIN,
            is_active=True,
            language="en"
        )
        session.add(admin)

        # Create teachers
        print("Creating teachers...")
        teacher1 = User(
            tenant_id=tenant.id,
            email="jane.smith@sunshinedaycare.co.za",
            password_hash=hash_password(TEST_PASSWORD),
            first_name="Jane",
            last_name="Smith",
            phone="+27 82 555 0002",
            role=Role.TEACHER,
            is_active=True,
            language="en"
        )
        session.add(teacher1)

        teacher2 = User(
            tenant_id=tenant.id,
            email="mary.williams@sunshinedaycare.co.za",
            password_hash=hash_password(TEST_PASSWORD),
            first_name="Mary",
            last_name="Williams",
            phone="+27 82 555 0003",
            role=Role.TEACHER,
            is_active=True,
            language="en"
        )
        session.add(teacher2)

        await session.flush()  # Get user IDs

        # Create class
        print("Creating class: Butterfly Room...")
        butterfly_class = SchoolClass(
            tenant_id=tenant.id,
            name="Butterfly Room",
            description="Toddler class for ages 2-3 years",
            age_group=AgeGroup.TODDLER,
            capacity=15,
            is_active=True
        )
        session.add(butterfly_class)
        await session.flush()  # Get class ID

        # Assign teachers to class
        print("Assigning teachers to class...")
        teacher_class1 = TeacherClass(
            teacher_id=teacher1.id,
            class_id=butterfly_class.id,
            is_primary=True
        )
        session.add(teacher_class1)

        teacher_class2 = TeacherClass(
            teacher_id=teacher2.id,
            class_id=butterfly_class.id,
            is_primary=False
        )
        session.add(teacher_class2)

        # Create students
        print("Creating students...")
        students_data = [
            {
                "first_name": "Emma",
                "last_name": "Brown",
                "date_of_birth": date.today() - timedelta(days=365 * 2 + 180),  # ~2.5 years old
                "gender": Gender.FEMALE,
                "allergies": "Peanuts",
                "emergency_contacts": [
                    {"name": "John Brown", "phone": "+27 82 123 4001", "relationship": "Father"}
                ]
            },
            {
                "first_name": "Noah",
                "last_name": "Davis",
                "date_of_birth": date.today() - timedelta(days=365 * 2 + 90),  # ~2.25 years old
                "gender": Gender.MALE,
                "medical_info": "Mild asthma, has inhaler",
                "emergency_contacts": [
                    {"name": "Lisa Davis", "phone": "+27 82 123 4002", "relationship": "Mother"}
                ]
            },
            {
                "first_name": "Olivia",
                "last_name": "Miller",
                "date_of_birth": date.today() - timedelta(days=365 * 3 - 30),  # ~2.9 years old
                "gender": Gender.FEMALE,
                "emergency_contacts": [
                    {"name": "James Miller", "phone": "+27 82 123 4003", "relationship": "Father"},
                    {"name": "Grace Miller", "phone": "+27 82 123 4004", "relationship": "Mother"}
                ]
            },
            {
                "first_name": "Liam",
                "last_name": "Wilson",
                "date_of_birth": date.today() - timedelta(days=365 * 2 + 270),  # ~2.75 years old
                "gender": Gender.MALE,
                "allergies": "Dairy",
                "notes": "Lactose intolerant, please provide dairy-free alternatives",
                "emergency_contacts": [
                    {"name": "Susan Wilson", "phone": "+27 82 123 4005", "relationship": "Mother"}
                ]
            },
            {
                "first_name": "Ava",
                "last_name": "Taylor",
                "date_of_birth": date.today() - timedelta(days=365 * 2 + 45),  # ~2.1 years old
                "gender": Gender.FEMALE,
                "emergency_contacts": [
                    {"name": "Michael Taylor", "phone": "+27 82 123 4006", "relationship": "Father"}
                ]
            }
        ]

        created_students = []
        for student_data in students_data:
            student = Student(
                tenant_id=tenant.id,
                first_name=student_data["first_name"],
                last_name=student_data["last_name"],
                date_of_birth=student_data["date_of_birth"],
                gender=student_data["gender"],
                age_group=AgeGroup.TODDLER,
                class_id=butterfly_class.id,
                medical_info=student_data.get("medical_info"),
                allergies=student_data.get("allergies"),
                emergency_contacts=student_data["emergency_contacts"],
                notes=student_data.get("notes"),
                is_active=True
            )
            session.add(student)
            created_students.append(student)

        await session.flush()

        # Create parents (one for each student)
        print("Creating parents...")
        parents_data = [
            {"email": "john.brown@email.com", "first_name": "John", "last_name": "Brown", "phone": "+27 82 123 4001"},
            {"email": "lisa.davis@email.com", "first_name": "Lisa", "last_name": "Davis", "phone": "+27 82 123 4002"},
            {"email": "james.miller@email.com", "first_name": "James", "last_name": "Miller", "phone": "+27 82 123 4003"},
            {"email": "susan.wilson@email.com", "first_name": "Susan", "last_name": "Wilson", "phone": "+27 82 123 4005"},
            {"email": "michael.taylor@email.com", "first_name": "Michael", "last_name": "Taylor", "phone": "+27 82 123 4006"},
        ]

        for i, parent_data in enumerate(parents_data):
            parent = User(
                tenant_id=tenant.id,
                email=parent_data["email"],
                password_hash=hash_password(TEST_PASSWORD),
                first_name=parent_data["first_name"],
                last_name=parent_data["last_name"],
                phone=parent_data["phone"],
                role=Role.PARENT,
                is_active=True,
                language="en"
            )
            session.add(parent)
            await session.flush()

            # Link parent to student
            parent_student = ParentStudent(
                parent_id=parent.id,
                student_id=created_students[i].id,
                relationship_type="PARENT",
                is_primary=True
            )
            session.add(parent_student)

        # Create default report templates
        print("Creating report templates...")

        # Daycare daily report template
        daycare_template = ReportTemplate(
            tenant_id=tenant.id,
            name=DAYCARE_DAILY_REPORT_TEMPLATE["name"],
            description=DAYCARE_DAILY_REPORT_TEMPLATE["description"],
            report_type=DAYCARE_DAILY_REPORT_TEMPLATE["report_type"],
            frequency=DAYCARE_DAILY_REPORT_TEMPLATE["frequency"],
            applies_to_grade_level=DAYCARE_DAILY_REPORT_TEMPLATE["applies_to_grade_level"],
            sections=DAYCARE_DAILY_REPORT_TEMPLATE["sections"],
            display_order=1,
            is_active=True
        )
        session.add(daycare_template)

        # Primary school report card template
        primary_school_template = ReportTemplate(
            tenant_id=tenant.id,
            name=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["name"],
            description=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["description"],
            report_type=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["report_type"],
            frequency=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["frequency"],
            applies_to_grade_level=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["applies_to_grade_level"],
            sections=PRIMARY_SCHOOL_REPORT_CARD_TEMPLATE["sections"],
            display_order=2,
            is_active=True
        )
        session.add(primary_school_template)

        # High school report card template
        high_school_template = ReportTemplate(
            tenant_id=tenant.id,
            name=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["name"],
            description=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["description"],
            report_type=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["report_type"],
            frequency=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["frequency"],
            applies_to_grade_level=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["applies_to_grade_level"],
            sections=HIGH_SCHOOL_REPORT_CARD_TEMPLATE["sections"],
            display_order=3,
            is_active=True
        )
        session.add(high_school_template)

        await session.commit()

        print("\n" + "=" * 50)
        print("Seed Data Created Successfully!")
        print("=" * 50)
        print("\nTenant:")
        print(f"  Name: {tenant.name}")
        print(f"  Slug: {tenant.slug}")
        print(f"  ID: {tenant.id}")
        print("\nUsers (all have password: password123):")
        print(f"  Admin: admin@sunshinedaycare.co.za")
        print(f"  Teacher 1: jane.smith@sunshinedaycare.co.za")
        print(f"  Teacher 2: mary.williams@sunshinedaycare.co.za")
        print(f"  Parent 1: john.brown@email.com")
        print(f"  Parent 2: lisa.davis@email.com")
        print(f"  Parent 3: james.miller@email.com")
        print(f"  Parent 4: susan.wilson@email.com")
        print(f"  Parent 5: michael.taylor@email.com")
        print("\nClass:")
        print(f"  Name: {butterfly_class.name}")
        print(f"  Students: {len(created_students)}")
        print("\nReport Templates:")
        print(f"  - {daycare_template.name} (Daily Activity)")
        print(f"  - {primary_school_template.name} (Report Card)")
        print(f"  - {high_school_template.name} (Report Card)")
        print("=" * 50 + "\n")

        return True


async def main():
    """Main entry point."""
    try:
        success = await seed_database()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
