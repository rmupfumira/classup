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
from app.models.tenant import EducationType, get_default_tenant_settings
from app.models.user import Role
from app.models.student import AgeGroup, Gender
from app.utils.security import hash_password


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
            # Delete existing data (cascade should handle related records)
            await session.delete(existing_tenant)
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
