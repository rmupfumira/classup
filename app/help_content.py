"""Structured help content for the in-app user guide.

Edit this file to update the help articles. Each topic has:
- slug: URL-safe identifier (used in /help/{slug})
- title: page heading
- short: one-line description shown on the index card
- icon: heroicon name (matching the SVG in templates/help/topic.html)
- roles: which roles see this article — any of: school_admin, super_admin
- category: section grouping on the help index
- overview: intro paragraph shown at the top of the article
- steps: list of {title, body, tip?} dicts — numbered step-by-step
- examples: list of {title, body} dicts — worked examples
- related: list of slugs to show as 'Related topics'
"""

from typing import Any


HELP_TOPICS: dict[str, dict[str, Any]] = {
    # ==================== GETTING STARTED ====================
    "getting-started": {
        "title": "Getting Started",
        "short": "First steps after logging in — the onboarding wizard, school info, and adding your first class.",
        "icon": "sparkles",
        "roles": ["school_admin"],
        "category": "Basics",
        "overview": (
            "When you first log in, ClassUp guides you through a 5-step onboarding wizard. "
            "Once onboarding is complete, your school is ready to use all the features your plan includes."
        ),
        "steps": [
            {
                "title": "Complete the onboarding wizard",
                "body": (
                    "After your first login, you'll see the onboarding wizard automatically. "
                    "Fill in your school name, upload a logo, choose a timezone, and pick a primary colour. "
                    "Click Next through each step. You can't skip steps, but you can come back later from Settings."
                ),
                "tip": "Use a PNG or JPG logo under 1MB. The primary colour is used across emails and the login page.",
            },
            {
                "title": "Pick your education type",
                "body": (
                    "On step 2, choose Daycare, Primary School, High School, K-12, or Combined. "
                    "This automatically turns on the right feature toggles and adjusts terminology (e.g. \"child\" vs. \"student\")."
                ),
                "tip": None,
            },
            {
                "title": "Create at least one class",
                "body": (
                    "Step 3 requires you to add at least one class. Enter a name (e.g. \"Grade 1A\" or \"Butterfly Room\"), "
                    "pick a grade level, and set a capacity if you want. You can add more classes later from the Classes tab."
                ),
                "tip": None,
            },
            {
                "title": "Invite teachers (optional)",
                "body": (
                    "Step 4 lets you invite teachers by email. They'll get a link to set their own password. "
                    "You can skip this and do it later from the Teachers tab."
                ),
                "tip": None,
            },
            {
                "title": "Finish the wizard",
                "body": (
                    "Review your setup on step 5 and click Finish. You'll be taken to your dashboard, and the wizard won't appear again."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["students", "classes", "teachers"],
    },
    # ==================== STUDENTS ====================
    "students": {
        "title": "Managing Students",
        "short": "Add, edit, search, and export your student list. Link parents via invitation.",
        "icon": "users",
        "roles": ["school_admin"],
        "category": "People",
        "overview": (
            "The Students tab is where you manage every child enrolled at your school. "
            "You can add them one at a time, import many from a spreadsheet, search and filter, and export to CSV or PDF."
        ),
        "steps": [
            {
                "title": "Add a single student",
                "body": (
                    "Go to Students → click the purple + Add Student button. Fill in at least First Name, "
                    "Last Name, and Class. Date of birth, medical info, allergies, and emergency contacts are optional but recommended. Click Save."
                ),
                "tip": "Medical info and allergies are highly visible on the student detail page — use them for anything a teacher needs to know immediately.",
            },
            {
                "title": "Import many students at once",
                "body": (
                    "Go to Imports → Students. Download the CSV template if you need one, fill it in with your students' details, then upload it. "
                    "You'll see a preview screen to map your columns. Confirm and the import runs in the background."
                ),
                "tip": "The only required columns are first_name and last_name. Everything else is optional.",
            },
            {
                "title": "Search and filter",
                "body": (
                    "On the Students page, use the search box to filter by name. Use the dropdowns to filter by Class or Grade Level. "
                    "Leave them blank to see everyone."
                ),
                "tip": None,
            },
            {
                "title": "Export the list",
                "body": (
                    "Use the CSV or PDF buttons at the top of the Students page to download your current filtered list. "
                    "The Print button opens the browser print dialog with a cleaned-up layout."
                ),
                "tip": "Filter first, then export — the exported file matches what you see on screen.",
            },
            {
                "title": "Invite a parent",
                "body": (
                    "Click a student to open their detail page, then click Invite Parent. Enter the parent's name and email. "
                    "They'll get a link to set their own password. Once they sign up, they're automatically linked to this student."
                ),
                "tip": "Parents only see their own children — they can never see other families' data.",
            },
        ],
        "examples": [
            {
                "title": "Example: mid-term transfer",
                "body": (
                    "A new learner joins in Term 2. Go to Students → + Add Student → fill in their name and assign them to Grade 3B. "
                    "Then open their detail and click Invite Parent to send login instructions to the parent's email."
                ),
            }
        ],
        "related": ["classes", "attendance", "parents"],
    },
    # ==================== CLASSES ====================
    "classes": {
        "title": "Classes & Teachers",
        "short": "Create classes, assign teachers, and manage class rosters.",
        "icon": "academic-cap",
        "roles": ["school_admin"],
        "category": "People",
        "overview": (
            "A class is a group of students taught together. Each class can have a primary teacher and any number of additional teachers. "
            "Students belong to exactly one class."
        ),
        "steps": [
            {
                "title": "Create a class",
                "body": (
                    "Go to Classes → + Add Class. Enter a name (e.g. \"Grade 7A\"), pick a grade level, and set capacity if you want to limit enrolment. Save."
                ),
                "tip": None,
            },
            {
                "title": "Assign teachers",
                "body": (
                    "Open a class → Teachers tab → Add Teacher. Pick from the dropdown. Mark one as Primary so the system knows which teacher is the default for auto-generated timetables and reports."
                ),
                "tip": "A teacher can be assigned to as many classes as you want. They'll see a class-picker in the navbar to switch between them.",
            },
            {
                "title": "Move students between classes",
                "body": (
                    "Edit the student → change their Class dropdown → Save. Attendance history, reports, and messages stay intact."
                ),
                "tip": None,
            },
            {
                "title": "Delete a class safely",
                "body": (
                    "Open the class → Edit → Delete. Any students in the class are automatically unassigned (not deleted), so you can reassign them to another class afterwards."
                ),
                "tip": "The class is soft-deleted — attendance and report history is preserved.",
            },
        ],
        "examples": [],
        "related": ["students", "teachers", "timetable"],
    },
    # ==================== ATTENDANCE ====================
    "attendance": {
        "title": "Attendance",
        "short": "Mark daily attendance, view history, and get stats. Parents are automatically notified of absences.",
        "icon": "check-badge",
        "roles": ["school_admin"],
        "category": "Daily Operations",
        "overview": (
            "Teachers mark attendance every day with a simple toggle interface. Parents are automatically notified "
            "of absences or late arrivals by email and in-app notification."
        ),
        "steps": [
            {
                "title": "Mark attendance for a class",
                "body": (
                    "Go to Attendance. Pick the class (teachers only see their own classes) and the date — today by default. "
                    "You'll see every student in the class. Tap each one to cycle through PRESENT → LATE → ABSENT → EXCUSED. "
                    "The time is auto-recorded when you mark PRESENT."
                ),
                "tip": "Use 'Mark all Present' to save time — then just change the exceptions.",
            },
            {
                "title": "Add a note",
                "body": (
                    "Tap the note icon next to a student's row to add context (e.g. \"arrived at 9:15 — traffic\"). Notes are visible to the school admin on the attendance history."
                ),
                "tip": None,
            },
            {
                "title": "View history",
                "body": (
                    "Click a student to open their detail page → Attendance tab. You'll see a calendar view of every day with the status and any notes."
                ),
                "tip": None,
            },
            {
                "title": "Check class stats",
                "body": (
                    "School admins can go to Attendance → Stats to see attendance rates per class, flag chronic absenteeism, and identify trends."
                ),
                "tip": None,
            },
        ],
        "examples": [
            {
                "title": "Example: a sick child",
                "body": (
                    "Sipho is absent on Monday. Teacher marks him ABSENT. The system sends his parents an email and an in-app notification: \"Sipho Dlamini was marked absent today.\" Parents can reply to the message to explain."
                ),
            }
        ],
        "related": ["messaging", "reports"],
    },
    # ==================== REPORTS ====================
    "reports": {
        "title": "Reports & Templates",
        "short": "Daily reports (daycare), report cards (school), and customisable templates.",
        "icon": "document-text",
        "roles": ["school_admin"],
        "category": "Daily Operations",
        "overview": (
            "Reports are built from templates. Daycare tenants get a daily activity template; primary/high schools "
            "get a report card template with subjects and grading. Admins can customise every template."
        ),
        "steps": [
            {
                "title": "Create a report for a student",
                "body": (
                    "From a student's detail page, click Create Report. Pick the template that matches (daily activity or report card). "
                    "Fill in each section. You can save as a Draft to come back to it."
                ),
                "tip": "Drafts are private to staff — parents don't see them until you finalise.",
            },
            {
                "title": "Finalise the report",
                "body": (
                    "Open a draft → click Finalise. Parents are notified by email and in-app. The report becomes read-only (you can still edit by re-opening the draft)."
                ),
                "tip": None,
            },
            {
                "title": "Customise a template",
                "body": (
                    "Go to Reports → Templates → Edit. Add or remove sections, change field types (checklist, narrative, academic grades), "
                    "or create a whole new template. Each template can be scoped to specific grade levels."
                ),
                "tip": "Start from the default template and tweak — don't build from scratch unless you need to.",
            },
            {
                "title": "Academic grades section",
                "body": (
                    "For report cards, add an Academic Grades section. Pick your grading system (e.g. 0-100% with A/B/C/D/E grades). "
                    "The system pulls the class's subjects automatically — just enter each student's marks."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["academic", "students"],
    },
    # ==================== MESSAGING ====================
    "messaging": {
        "title": "Messaging & Announcements",
        "short": "Send school-wide or class announcements. Parents can reply. Share photos and documents too.",
        "icon": "chat-bubble",
        "roles": ["school_admin"],
        "category": "Communication",
        "overview": (
            "ClassUp has a full messaging system: school-wide announcements, class announcements, individual student messages, "
            "and two-way replies with parents."
        ),
        "steps": [
            {
                "title": "Send a school-wide announcement",
                "body": (
                    "Go to Messages → Compose → choose Announcement. Type your subject and body. Hit Send. "
                    "Every parent and staff member receives it in-app and by email."
                ),
                "tip": "Use this sparingly — for sports day, public holidays, emergencies. Everyday stuff belongs in class announcements.",
            },
            {
                "title": "Send a class announcement",
                "body": (
                    "Compose → Class Announcement → pick the class. Only parents of that class will receive it."
                ),
                "tip": None,
            },
            {
                "title": "Message one student's parents",
                "body": (
                    "Compose → Student Message → pick the student. Only their linked parents see it."
                ),
                "tip": None,
            },
            {
                "title": "Share photos or documents",
                "body": (
                    "Compose → Class Photo (or School Document). Drag and drop the files. Add a caption. Send. "
                    "Parents see them in their inbox and in the Photos/Documents gallery."
                ),
                "tip": "Photos are compressed automatically. Documents up to 10MB are supported.",
            },
            {
                "title": "Reply to a parent",
                "body": (
                    "Open a message in your inbox. Type a reply in the text box at the bottom and send. The parent gets notified."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["photos", "attendance"],
    },
    # ==================== PHOTOS ====================
    "photos": {
        "title": "Photos & Documents",
        "short": "Upload photos and documents to share with parents.",
        "icon": "photo",
        "roles": ["school_admin"],
        "category": "Communication",
        "overview": (
            "Separate galleries for photos and documents. Admin can share school-wide; teachers can share with their own class."
        ),
        "steps": [
            {
                "title": "Upload a photo",
                "body": (
                    "Go to Photos → + Upload. Drag and drop (or click to browse). "
                    "Photos are compressed and resized automatically; each one is under 1MB after processing."
                ),
                "tip": "Supported formats: JPG, PNG, WebP, HEIC. Max 5MB before compression.",
            },
            {
                "title": "Upload a document",
                "body": (
                    "Go to Documents → + Upload. Supported: PDF, Word. Max 10MB."
                ),
                "tip": None,
            },
            {
                "title": "Share a file via a message",
                "body": (
                    "From Messages → Compose → pick Photo or Document type → attach your file. Parents get a link they can download."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["messaging"],
    },
    # ==================== BILLING ====================
    "billing": {
        "title": "Billing",
        "short": "Fee items, invoices, payments, arrears reporting, and parent statements.",
        "icon": "currency",
        "roles": ["school_admin"],
        "category": "Finance",
        "overview": (
            "Billing lets you charge parents for school fees. Create reusable fee items (monthly, termly, or annual), "
            "generate invoices in bulk, record payments, and see who's in arrears."
        ),
        "steps": [
            {
                "title": "Set up fee items",
                "body": (
                    "Go to Billing → Fee Items → + New. Enter a name (e.g. \"Monthly Tuition — Grade 5\"), amount, and frequency. "
                    "Choose whether it applies to all students or just a specific class."
                ),
                "tip": "Set up all your fee items first, before you start invoicing.",
            },
            {
                "title": "Generate invoices in bulk",
                "body": (
                    "Go to Billing → Invoices → Generate Invoices. Pick the fee items you want to include and the billing period. "
                    "The system creates one invoice per eligible student, numbered INV-YYYY-NNNN."
                ),
                "tip": None,
            },
            {
                "title": "Send an invoice to parents",
                "body": (
                    "Open a DRAFT invoice → click Send. Parents receive an email with a PDF attached, and the invoice moves to SENT status."
                ),
                "tip": None,
            },
            {
                "title": "Record a payment",
                "body": (
                    "Open the invoice → Record Payment → enter the amount, method (Cash, EFT, Card, etc), and date. "
                    "The balance updates automatically. Once fully paid, status changes to PAID."
                ),
                "tip": "Partial payments are supported — status becomes PARTIALLY_PAID.",
            },
            {
                "title": "Check arrears",
                "body": (
                    "Go to Billing → Arrears Report. You'll see each student's Total Due, Paid, Outstanding, and ageing breakdown "
                    "(Current, 30 days, 60 days, 90+ days). Filter by class or export to CSV."
                ),
                "tip": "Set up recurring overdue reminders in Settings → Billing to automate this.",
            },
        ],
        "examples": [
            {
                "title": "Example: monthly tuition",
                "body": (
                    "Create a fee item called \"Monthly Tuition\" at R3,500, frequency MONTHLY, applies to ALL. "
                    "On the 1st of each month, go to Invoices → Generate → tick Monthly Tuition → billing period 1st–30th. "
                    "Hit Generate. Every active student gets a R3,500 invoice. Send them all with one click."
                ),
            }
        ],
        "related": ["students"],
    },
    # ==================== TIMETABLE ====================
    "timetable": {
        "title": "Timetable",
        "short": "Set up a school day, auto-generate class timetables, and view per-teacher schedules.",
        "icon": "calendar",
        "roles": ["school_admin"],
        "category": "Daily Operations",
        "overview": (
            "The timetable module (primary/high school only) lets you configure your school day (days + periods + breaks), "
            "then auto-generate a weekly timetable for each class. Teachers see their own schedule; parents see their child's."
        ),
        "steps": [
            {
                "title": "Configure the school day",
                "body": (
                    "Go to Timetable → Day Config. Set the days of the week, then add period rows. "
                    "Each row has an index, a label (e.g. \"Period 1\" or \"Break\"), a start and end time, and a break checkbox. Save."
                ),
                "tip": "Breaks won't get lessons assigned — they're shown as empty grey cells in the grid.",
            },
            {
                "title": "Create a timetable for a class",
                "body": (
                    "Go to Timetable → + New Timetable → pick a class that has subjects assigned → give it a name like \"Grade 7A - Term 1\". "
                    "You'll land on an empty grid."
                ),
                "tip": "The class must have subjects assigned first — go to Class → Subjects if not.",
            },
            {
                "title": "Auto-generate the draft",
                "body": (
                    "On the timetable grid, click Auto-Generate Draft. You can leave the weekly-hours blank for even distribution, "
                    "or enter a specific count for each subject (e.g. Maths: 8 periods/week). Hit Generate. "
                    "The grid fills with subject + teacher cells, spread across days."
                ),
                "tip": "The generator picks the class's primary teacher by default, and avoids double-booking teachers across classes.",
            },
            {
                "title": "Edit a cell manually",
                "body": (
                    "Click any cell in the grid. A popup lets you change the subject or teacher, or clear the cell. AJAX save — no reload."
                ),
                "tip": "Conflict badges appear on cells where the teacher is double-booked elsewhere.",
            },
            {
                "title": "Teacher's view",
                "body": (
                    "Teachers log in and go to Timetable → they see their own schedule across all classes, read-only."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["academic", "classes"],
    },
    # ==================== ACADEMIC ====================
    "academic": {
        "title": "Subjects & Grading",
        "short": "Set up subjects, assign them to classes, and configure grading scales.",
        "icon": "book-open",
        "roles": ["school_admin"],
        "category": "School Setup",
        "overview": (
            "For primary and high schools, you'll set up subjects (e.g. Maths, English), assign them to classes, "
            "and configure grading systems (e.g. 80-100 = A)."
        ),
        "steps": [
            {
                "title": "Quick setup with defaults",
                "body": (
                    "Go to Settings → Academic → Setup Defaults. Pick Primary (9 subjects) or High School (10 subjects). "
                    "The system creates a complete set of common subjects and a default grading system in one click."
                ),
                "tip": None,
            },
            {
                "title": "Add a subject manually",
                "body": (
                    "Settings → Academic → Subjects → + New. Enter name (e.g. Geography), code (must be unique), default total marks (usually 100)."
                ),
                "tip": None,
            },
            {
                "title": "Assign subjects to a class",
                "body": (
                    "Open a class → Subjects tab → Add. Pick subjects and set per-class totals if they differ from the default. "
                    "Mark as compulsory if required."
                ),
                "tip": "Use Bulk Assign to copy subjects from another class if they have the same curriculum.",
            },
            {
                "title": "Configure grading",
                "body": (
                    "Settings → Academic → Grading Systems. Create or edit bands (e.g. 80-100 A, 70-79 B, etc). "
                    "Mark one as default — it's used on report cards unless overridden."
                ),
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["reports", "timetable"],
    },
    # ==================== TEACHERS ====================
    "teachers": {
        "title": "Inviting Teachers",
        "short": "Invite teachers by email so they can access the classes they teach.",
        "icon": "user-plus",
        "roles": ["school_admin"],
        "category": "People",
        "overview": "Teachers are invited by email. Once they sign up, assign them to their classes.",
        "steps": [
            {
                "title": "Send an invitation",
                "body": "Go to Teachers → Invite Teacher → enter name + email → Send. The teacher gets an email with a link to set their own password.",
                "tip": None,
            },
            {
                "title": "Resend or cancel",
                "body": "Pending invitations are listed on the Teachers page. Use the Resend button if they didn't get the email, or Cancel to revoke.",
                "tip": None,
            },
            {
                "title": "Assign to classes",
                "body": "Once the teacher has signed up, go to Classes → pick a class → Teachers tab → Add Teacher.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["classes"],
    },
    # ==================== PARENTS ====================
    "parents": {
        "title": "Inviting Parents",
        "short": "Invite parents to sign up and link them to their child.",
        "icon": "user-group",
        "roles": ["school_admin"],
        "category": "People",
        "overview": (
            "Parents are invited from a specific student's detail page. They register with their own password and are automatically "
            "linked to that student. They can only ever see their own children's data."
        ),
        "steps": [
            {
                "title": "Send an invitation",
                "body": "Open a student → Invite Parent → fill in name + email → Send. The email contains a secure one-time link.",
                "tip": "You can invite multiple parents per student (e.g. mom and dad). Just repeat for each.",
            },
            {
                "title": "Parent registration",
                "body": "The parent clicks the link → their name and email are pre-filled (read-only) → they set a password → submit. They're auto-logged-in and linked to their child.",
                "tip": None,
            },
            {
                "title": "Link to additional children",
                "body": "If a family has more than one child, invite the parent from each child's page using the same email. The system links them all.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["students", "messaging"],
    },
    # ==================== SUBSCRIPTION ====================
    "subscription": {
        "title": "Your Subscription",
        "short": "View your plan, change plans, and understand which features your plan unlocks.",
        "icon": "credit-card",
        "roles": ["school_admin"],
        "category": "Account",
        "overview": (
            "ClassUp is a subscription service. Each plan unlocks a specific set of features — "
            "billing, photo sharing, document sharing, timetable, subjects & grading, and WhatsApp "
            "notifications. Switching plans changes which features you can access immediately. "
            "New schools start on a free trial; pick a plan before the trial ends or your access "
            "will be paused until you do."
        ),
        "steps": [
            {
                "title": "Check your trial",
                "body": "The dashboard shows a banner with days remaining on your trial. Click View Plans on the banner to see your options.",
                "tip": None,
            },
            {
                "title": "Pick a plan",
                "body": (
                    "Go to Subscription → browse the available plans → click Select on the one you want. "
                    "If you're on a trial, you switch immediately without being charged; you'll be charged when the trial ends. "
                    "The features included in each plan are listed on the plan card — pick the one that has everything you need."
                ),
                "tip": "If you need just one more feature (e.g. billing), switch to a plan that includes it rather than asking support to 'turn it on' — plans are the source of truth.",
            },
            {
                "title": "What happens when you switch plans",
                "body": (
                    "As soon as you activate a new plan, its features take effect. Features the new plan includes are enabled automatically, "
                    "and features it doesn't include are hidden from your sidebar and blocked on the API. Existing data (invoices, photos, etc.) "
                    "is preserved — you just can't create new entries for disabled features until you switch back to a plan that includes them."
                ),
                "tip": None,
            },
            {
                "title": "Pay via Paystack",
                "body": (
                    "When you're ready to pay, click Initialize Payment on the Subscription page. You'll be sent to Paystack's secure checkout "
                    "(card or EFT). Once paid, your account activates immediately."
                ),
                "tip": None,
            },
            {
                "title": "Why did a feature disappear?",
                "body": (
                    "If a menu item has vanished from your sidebar or a page shows the yellow 'not included in your current plan' banner, "
                    "your current plan doesn't include that feature. Go to Subscription and switch to a plan that does."
                ),
                "tip": None,
            },
        ],
        "examples": [
            {
                "title": "Example: needing the timetable module",
                "body": (
                    "Your school is on the Essentials plan but you realise you need to publish weekly timetables. "
                    "Go to /subscription → see which plans include Timetable → select Professional. Timetable appears "
                    "in the sidebar immediately, and teachers + parents can now see their schedules."
                ),
            }
        ],
        "related": ["locked-features", "settings"],
    },
    # ==================== LOCKED FEATURES (both roles) ====================
    "locked-features": {
        "title": "Locked Features & Plans",
        "short": "Why some menu items are missing, and how plan gating works in ClassUp.",
        "icon": "credit-card",
        "roles": ["school_admin", "super_admin"],
        "category": "Account",
        "overview": (
            "ClassUp controls access to premium features at the plan level. When you're subscribed to a "
            "plan, only that plan's features are available to your staff and parents. If you try to use a "
            "feature not in your plan, you'll be redirected to the Subscription page with a helpful banner."
        ),
        "steps": [
            {
                "title": "Core features are always available",
                "body": (
                    "No plan ever takes away the basics: attendance, messaging, announcements, reports, students, classes, "
                    "teachers, invitations, settings, profile, and the help centre. These work regardless of subscription status."
                ),
                "tip": None,
            },
            {
                "title": "Premium features are plan-controlled",
                "body": (
                    "Billing, Photo Sharing, Document Sharing, Timetable, Subjects & Grading, and WhatsApp are each unlocked by specific plans. "
                    "A super admin configures which features each plan includes; school admins choose which plan fits their school."
                ),
                "tip": None,
            },
            {
                "title": "How enforcement works",
                "body": (
                    "Three layers keep locked features inaccessible:\n"
                    "1. Sidebar and mobile nav hide the menu items automatically.\n"
                    "2. Web pages redirect to /subscription with a banner explaining which feature is locked.\n"
                    "3. API endpoints return 402 Payment Required with the feature name so front-end code can show an upgrade prompt."
                ),
                "tip": None,
            },
            {
                "title": "Existing data is preserved",
                "body": (
                    "If you downgrade to a plan that doesn't include billing, your invoices aren't deleted — they just become inaccessible until "
                    "you upgrade again. Same for photos, timetables, etc. You never lose historical data."
                ),
                "tip": None,
            },
            {
                "title": "Super admin bypass",
                "body": (
                    "Super admins can access every feature on every tenant for support purposes, even if the tenant's plan doesn't include it. "
                    "This is intentional — support needs full visibility."
                ),
                "tip": None,
            },
        ],
        "examples": [
            {
                "title": "Example: a locked page",
                "body": (
                    "A teacher clicks Photos in an old bookmark. The page redirects to /subscription with the message "
                    "\"Photo Sharing is not included in your current plan.\" The school admin sees the banner, picks a plan "
                    "that includes Photo Sharing, and after activation the Photos link reappears in the sidebar."
                ),
            }
        ],
        "related": ["subscription", "settings", "subscription-plans"],
    },
    # ==================== SETTINGS ====================
    "settings": {
        "title": "Settings",
        "short": "Branding, features, terminology, and tenant preferences.",
        "icon": "cog",
        "roles": ["school_admin"],
        "category": "School Setup",
        "overview": "The Settings area is where you tweak how ClassUp looks and behaves for your school.",
        "steps": [
            {
                "title": "School info",
                "body": "Settings → School Info. Update name, address, phone, timezone, and upload a logo.",
                "tip": None,
            },
            {
                "title": "Feature toggles",
                "body": (
                    "Settings → Features. Some features are 'Plan-managed' — they're controlled by your subscription "
                    "plan and show as locked with a 'Plan-managed' label. These include Billing, Photo Sharing, "
                    "Document Sharing, Timetable, Subjects & Grading, and WhatsApp. To change them, go to Subscription "
                    "and switch to a plan that matches what you need. The non-plan-managed toggles (like report section "
                    "trackers: nap, bathroom, meals, etc.) you can flip yourself."
                ),
                "tip": "If a toggle is locked and you need it on, switch plans — don't ask support to enable it. Plans are the source of truth.",
            },
            {
                "title": "Branding",
                "body": "Settings → Branding. Change your primary and secondary colours. These affect email templates and the login page.",
                "tip": None,
            },
            {
                "title": "Terminology",
                "body": "Settings → Terminology. Switch labels like \"student\" ↔ \"child\", or \"class\" ↔ \"room\" to match your setting.",
                "tip": "Daycares often prefer \"child\" and \"room\"; schools prefer \"student\" and \"class\".",
            },
            {
                "title": "Billing settings",
                "body": "Settings → Billing. Set your currency, banking details, payment instructions, and configure recurring overdue reminders.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["billing"],
    },
    # ==================== PROFILE ====================
    "profile": {
        "title": "Your Profile",
        "short": "Update your personal details and change your password.",
        "icon": "user",
        "roles": ["school_admin", "super_admin"],
        "category": "Account",
        "overview": "Every user has a profile where they can update their own name, phone, language, and password.",
        "steps": [
            {
                "title": "Open your profile",
                "body": "Click your avatar top-right → Profile. Or visit /profile directly.",
                "tip": None,
            },
            {
                "title": "Update your details",
                "body": "Change your first name, last name, phone, language (English or Afrikaans), WhatsApp number, or opt-in for WhatsApp notifications. Click Save.",
                "tip": "Only a school admin can change your email — contact them if you need to.",
            },
            {
                "title": "Change your password",
                "body": "Enter your current password, then type a new one (min 8 characters) twice. Click Update Password.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": [],
    },
    # ============================================================
    # ==================== SUPER ADMIN ONLY =====================
    # ============================================================
    "tenants": {
        "title": "Managing Tenants",
        "short": "Create schools, edit their details, change their login URL slug.",
        "icon": "building",
        "roles": ["super_admin"],
        "category": "Platform",
        "overview": (
            "A tenant is one school or daycare on the platform. Each tenant is fully isolated — no school can see another's data."
        ),
        "steps": [
            {
                "title": "Create a new tenant",
                "body": (
                    "Admin → Tenants → + Add Tenant. Fill in name, contact details, education type. "
                    "The URL slug is auto-generated from the name — you can override it before saving."
                ),
                "tip": "The slug becomes part of the tenant's login URL (e.g. classup.co.za/sunshine-daycare).",
            },
            {
                "title": "Edit a tenant's slug",
                "body": (
                    "Admin → Tenants → [tenant] → Edit. The slug field is now editable. "
                    "Reserved names (login, students, dashboard, etc) are rejected automatically."
                ),
                "tip": "Changing the slug breaks any existing bookmarks to the old URL — warn the school first.",
            },
            {
                "title": "Deactivate a tenant",
                "body": "Edit the tenant → set Status to Inactive → Save. All users of that tenant are locked out until you reactivate.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["subscription-plans"],
    },
    "subscription-plans": {
        "title": "Subscription Plans",
        "short": "Create plans with feature toggles that automatically enforce access per tenant.",
        "icon": "credit-card",
        "roles": ["super_admin"],
        "category": "Platform",
        "overview": (
            "Plans define what features each tenant gets, at what price. Ticking a feature on a plan doesn't just "
            "document the tier — it automatically controls what tenants on that plan can actually access. The system "
            "enforces every feature toggle across sidebar, pages, and API simultaneously."
        ),
        "steps": [
            {
                "title": "Create a plan",
                "body": (
                    "Admin → Subscriptions → + Create Plan. Enter name, monthly + annual price, trial days, and tick the features "
                    "you want the plan to include. The description is auto-generated from the checked features."
                ),
                "tip": None,
            },
            {
                "title": "Features that are plan-gated",
                "body": (
                    "These six features are strictly enforced by the plan:\n"
                    "• Billing — invoices, payments, arrears reports\n"
                    "• Photo Sharing — the /photos gallery and uploads\n"
                    "• Document Sharing — the /documents library\n"
                    "• Timetable — the full timetable module\n"
                    "• Subjects & Grading — academic subjects + grading systems\n"
                    "• WhatsApp Notifications — outbound WhatsApp send API\n\n"
                    "Other toggles (attendance, messaging, announcements, reports, report section trackers) "
                    "aren't plan-gated — they're always available to every tenant."
                ),
                "tip": "Keep core features (attendance, messaging, reports) always on — they're what every school needs regardless of tier.",
            },
            {
                "title": "Edit a plan's features",
                "body": (
                    "Click Edit on any plan card. Toggle features on/off and save. Any tenant currently on that plan gets "
                    "the updated feature set applied the next time their subscription is re-synced (typically on their next page load). "
                    "New tenants selecting this plan get the current feature set immediately."
                ),
                "tip": "Features controlled by a plan are locked on the tenant's Settings page — they can't override. Plans are the single source of truth.",
            },
            {
                "title": "How enforcement actually works",
                "body": (
                    "When a tenant activates a plan, ClassUp copies the plan's features dict into the tenant's settings. "
                    "From then on, every request on that tenant runs through three enforcement layers: (1) sidebar + nav hides disabled items, "
                    "(2) web routes for disabled features redirect to /subscription with an upgrade banner, (3) API endpoints return "
                    "402 Payment Required JSON. Super admins bypass all three for support access."
                ),
                "tip": None,
            },
            {
                "title": "Extend a tenant's trial",
                "body": (
                    "Admin → Subscriptions → on any trialing tenant row, click Extend → pick 7, 14, 30, or 90 days → Submit. "
                    "The trial end date jumps forward. Suspended subscriptions are reactivated in the process."
                ),
                "tip": "Use this for concessions — e.g. a school needs another week to onboard students.",
            },
            {
                "title": "Delete a plan",
                "body": "Click Delete on a plan card. You can't delete a plan if any tenant is currently subscribed — first move them to another plan.",
                "tip": None,
            },
        ],
        "examples": [
            {
                "title": "Example: offering a Professional plan",
                "body": (
                    "Create a plan \"Professional\" at R999/month. Tick: Billing, Photo Sharing, Document Sharing, Timetable, "
                    "Subjects & Grading, WhatsApp Notifications. When any school selects this plan, all six modules "
                    "light up in their sidebar and become accessible to their teachers and parents. If they later downgrade "
                    "to a cheaper plan that doesn't include Billing, the /billing menu item disappears and /billing URLs "
                    "redirect to /subscription — but their existing invoices are preserved."
                ),
            },
            {
                "title": "Example: building a tiered structure",
                "body": (
                    "Free: (nothing premium ticked — just attendance, messaging, reports by default).\n"
                    "Essentials R299/mo: + Photo Sharing + Document Sharing.\n"
                    "Professional R999/mo: + Billing + Timetable + Subjects & Grading.\n"
                    "Enterprise R2,499/mo: + WhatsApp + API access (when added).\n"
                    "Each tier is strictly enforced — schools can only access what they're paying for."
                ),
            },
        ],
        "related": ["tenants", "locked-features"],
    },
    "email-settings": {
        "title": "Email Configuration",
        "short": "Configure the email provider — SMTP or Resend — used for all platform emails.",
        "icon": "envelope",
        "roles": ["super_admin"],
        "category": "Platform",
        "overview": (
            "ClassUp can send emails via SMTP or Resend. You configure this once at the platform level; "
            "individual tenants inherit it (though the sender name shows the tenant's name)."
        ),
        "steps": [
            {
                "title": "Pick a provider",
                "body": "Admin → Email Settings. Choose SMTP or Resend.",
                "tip": "Resend is easier to set up and has better deliverability. SMTP is fine if you already have an SMTP server.",
            },
            {
                "title": "Fill in credentials",
                "body": (
                    "For Resend: enter your API key. "
                    "For SMTP: enter host, port (465 or 587), username, password, and whether to use TLS."
                ),
                "tip": None,
            },
            {
                "title": "Send a test email",
                "body": "Click Send Test Email. If it arrives, you're done. If not, check the error message — usually it's the credentials or a DNS issue on your domain.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": [],
    },
    "platform-stats": {
        "title": "Platform Stats & Revenue",
        "short": "See overall platform health: active tenants, revenue, invoices.",
        "icon": "chart",
        "roles": ["super_admin"],
        "category": "Platform",
        "overview": "The super admin dashboard shows platform-wide metrics and recent activity.",
        "steps": [
            {
                "title": "View the super admin dashboard",
                "body": "Admin → Dashboard. You'll see: total tenants, active tenants, total students across the platform, and revenue for the current month.",
                "tip": None,
            },
            {
                "title": "Browse platform invoices",
                "body": "Admin → Subscriptions → Recent Platform Invoices. See what you've billed each school and their payment status.",
                "tip": None,
            },
        ],
        "examples": [],
        "related": ["subscription-plans"],
    },
}


def get_topics_for_role(role: str) -> list[dict[str, Any]]:
    """Return topics visible to a given role, with their slug attached."""
    role_key = "super_admin" if role in ("SUPER_ADMIN", "super_admin") else "school_admin"
    result = []
    for slug, topic in HELP_TOPICS.items():
        if role_key in topic["roles"]:
            result.append({**topic, "slug": slug})
    return result


def get_topic(slug: str) -> dict[str, Any] | None:
    """Return a single topic by slug, or None."""
    topic = HELP_TOPICS.get(slug)
    if not topic:
        return None
    return {**topic, "slug": slug}


def get_related_topics(slugs: list[str]) -> list[dict[str, Any]]:
    """Return topic metadata for a list of slugs (skipping unknown ones)."""
    return [
        {**HELP_TOPICS[s], "slug": s}
        for s in slugs
        if s in HELP_TOPICS
    ]
