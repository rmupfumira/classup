-- =====================================================================
-- Shumba High School — complete demo tenant for staging walk-throughs.
--
-- Creates a Zimbabwean ZIMSEC O-Level school with:
--   - 100 students across 8 classes (Form 1A/B … Form 4A/B)
--   - 10 ZIMSEC O-Level subjects + ZIMSEC grading system (default)
--   - 1 school admin, 2 teachers, 100 parents (login creds below)
--   - 3 academic years × 3 terms = 9 terms of finalised report cards
--   - 40 sessions of attendance for the current term
--   - 3 fee items (Tuition, Boarding, Books) + 3 terms of invoices
--   - ~70% of invoices paid in full, ~15% partial, rest open
--   - Default chart of accounts + a few expense transactions
--   - MIS / Billing / Accounting features all switched on
--
-- Idempotent: re-running updates the tenant in place and tops up any
-- missing rows without duplicating. Student / subject / report rows
-- are identified by deterministic UUIDs (md5 over a stable key) so
-- a re-run finds them rather than inserting fresh ones.
--
-- Login (all users share the password):
--   Password:   Demo@2026
--   Admin:      head@shumba.co.zw            (SCHOOL_ADMIN)
--   Teacher 1:  teacher1@shumba.co.zw        (TEACHER, Form 3A primary)
--   Teacher 2:  teacher2@shumba.co.zw        (TEACHER, Form 4A primary)
--   Parents:    parent001@shumba.co.zw …
--               parent100@shumba.co.zw       (PARENT)
--
-- Reset parent passwords in bulk:
--   UPDATE users SET password_hash =
--     '$2b$12$inPaf.SdETNGRUenoiDCPOMYD2MiBdJu3elOiqcUgQEPMAzWanuNa'
--   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high');
-- =====================================================================

DO $BLOCK$
DECLARE
    -- ────────────────────────────────────────────────────────────────
    -- Core identifiers
    -- ────────────────────────────────────────────────────────────────
    v_tenant_id          uuid := md5('shumba-tenant')::uuid;
    v_admin_id           uuid := md5('shumba-admin')::uuid;
    v_teacher1_id        uuid := md5('shumba-teacher1')::uuid;
    v_teacher2_id        uuid := md5('shumba-teacher2')::uuid;
    v_grading_system_id  uuid := md5('shumba-grading')::uuid;
    v_template_id        uuid := md5('shumba-template')::uuid;
    v_bank_id            uuid := md5('shumba-bank')::uuid;
    v_section_id         text := 'sec-shumba-grades';
    v_password_hash      text := '$2b$12$inPaf.SdETNGRUenoiDCPOMYD2MiBdJu3elOiqcUgQEPMAzWanuNa';

    -- ZIMSEC O-Level grading bands (2019+ scheme — C or better = pass)
    v_grading_bands      jsonb := '[
        {"min": 70, "max": 100, "grade": "A", "description": "Distinction",    "points": 1},
        {"min": 60, "max": 69,  "grade": "B", "description": "Merit",          "points": 2},
        {"min": 50, "max": 59,  "grade": "C", "description": "Pass",           "points": 3},
        {"min": 45, "max": 49,  "grade": "D", "description": "Lower Pass",     "points": 4},
        {"min": 40, "max": 44,  "grade": "E", "description": "Marginal Pass",  "points": 5},
        {"min": 0,  "max": 39,  "grade": "U", "description": "Ungraded",       "points": 0}
    ]'::jsonb;

    -- Tenant settings JSONB
    v_tenant_settings    jsonb := jsonb_build_object(
        'country', 'ZW',
        'curriculum_pack', 'ZW_ZIMSEC',
        'school_type', 'MIXED',
        'billing_currency', 'USD',
        'education_type', 'HIGH_SCHOOL',
        'features', jsonb_build_object(
            'mis', true,
            'billing', true,
            'accounting', true,
            'attendance_tracking', true,
            'messaging', true,
            'photo_sharing', true,
            'document_sharing', true,
            'parent_communication', true,
            'grade_tracking', true,
            'subject_management', true,
            'exam_management', true,
            'timetable_management', true,
            'disciplinary_records', true,
            'whatsapp_enabled', false,
            'whatsapp_ai_enabled', false
        ),
        'terminology', jsonb_build_object(
            'student', 'Student', 'students', 'Students',
            'teacher', 'Teacher', 'teachers', 'Teachers',
            'class', 'Class', 'classes', 'Classes',
            'parent', 'Parent', 'parents', 'Parents'
        ),
        'branding', jsonb_build_object('primary_color', '#1B3A6B', 'secondary_color', '#C9962A')
    );

    -- Working variables
    v_class_id           uuid;
    v_student_id         uuid;
    v_parent_id          uuid;
    v_subject_id         uuid;
    v_invoice_id         uuid;
    v_report_id          uuid;
    v_marks_obtained     int;
    v_total_marks        int;
    v_target_mean        numeric;
    v_subject_bias       numeric;
    v_term_offset        numeric;
    v_subject_data       jsonb;
    v_report_data        jsonb;
    v_report_date        date;
    v_invoice_number     text;
    v_due_date           date;
    v_amount_paid        numeric;
    v_pay_scenario       int;
    v_class_names        text[] := ARRAY['Form 1A','Form 1B','Form 2A','Form 2B','Form 3A','Form 3B','Form 4A','Form 4B'];
    v_class_count        int := array_length(v_class_names, 1);
    v_grade_level_names  text[] := ARRAY['Form 1','Form 1','Form 2','Form 2','Form 3','Form 3','Form 4','Form 4'];
    v_subject_names      text[] := ARRAY[
        'English Language','Literature in English','Mathematics','Combined Science',
        'Geography','History','Shona','Heritage Studies','Computer Science','Agriculture'
    ];
    v_subject_codes      text[] := ARRAY['SHS-ENG','SHS-LIT','SHS-MAT','SHS-CSC','SHS-GEO','SHS-HIS','SHS-SHO','SHS-HER','SHS-COS','SHS-AGR'];
    v_subject_totals     int[]  := ARRAY[100, 100, 100, 100, 100, 100, 100, 100, 100, 100];
    v_first_names        text[] := ARRAY[
        'Tendai','Chipo','Tatenda','Rutendo','Tafadzwa','Rudo','Farai','Nyasha','Takudzwa','Shamiso',
        'Blessing','Munashe','Simba','Tanaka','Panashe','Vimbai','Ropafadzo','Tapiwa','Mazvita','Kudzai',
        'Tinashe','Chiedza','Tadiwa','Michelle','Prince','Natasha','Walter','Patience','Edmund','Kelsey',
        'Isabella','Mitchell','Garikai','Natalie','Nkosana','Melody','Lloyd','Caroline','Takura','Faith'
    ];
    v_last_names         text[] := ARRAY[
        'Moyo','Ncube','Dube','Nkomo','Sibanda','Mpofu','Mukasa','Chiwanza','Mutasa','Mugabe',
        'Mahachi','Ngwenya','Chabata','Marufu','Zulu','Masuku','Mandaza','Chirwa','Ndlovu','Mavhunga',
        'Chikafu','Kahari','Mangwanda','Ruvere','Mutero','Rupende','Chikomba','Mazarura','Magondo','Zimunya'
    ];
    v_class_ids          uuid[] := ARRAY[]::uuid[];
    v_subject_ids        uuid[] := ARRAY[]::uuid[];
    v_student_ids        uuid[] := ARRAY[]::uuid[];
    v_year               int;
    v_term               int;
    i                    int;
    j                    int;
    k                    int;
    v_total_reports      int := 0;
    v_total_attendance   int := 0;
    v_total_invoices     int := 0;
    v_total_payments     int := 0;
    v_first_name         text;
    v_last_name          text;
    v_dob                date;
    v_class_idx          int;
    v_random_roll        numeric;
    v_att_status         text;
    v_att_date           date;
    v_target_attendance  numeric;
BEGIN
    -- Postgres needs pgcrypto for gen_random_uuid() but we're using
    -- md5-derived UUIDs to keep the script idempotent. No extension
    -- needed.

    -- ─────────────────────── 1. Tenant ───────────────────────
    INSERT INTO tenants (id, name, slug, email, phone, address,
                         education_type, settings, is_active, onboarding_completed)
    VALUES (v_tenant_id,
            'Shumba High School',
            'shumba-high',
            'office@shumba.co.zw',
            '+263 772 000 000',
            '1 Independence Ave, Harare, Zimbabwe',
            'HIGH_SCHOOL',
            v_tenant_settings,
            true, true)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        slug = EXCLUDED.slug,
        email = EXCLUDED.email,
        settings = EXCLUDED.settings,
        is_active = true;

    -- ─────────────────────── 2. Users ────────────────────────
    INSERT INTO users (id, tenant_id, email, password_hash, first_name, last_name,
                       phone, role, is_active, language, whatsapp_opted_in)
    VALUES (v_admin_id, v_tenant_id, 'head@shumba.co.zw', v_password_hash,
            'Headmaster', 'Mandizvidza', '+263 772 100 100',
            'SCHOOL_ADMIN', true, 'en', false)
    ON CONFLICT (id) DO UPDATE SET password_hash = v_password_hash,
        is_active = true, role = 'SCHOOL_ADMIN';

    INSERT INTO users (id, tenant_id, email, password_hash, first_name, last_name,
                       phone, role, is_active, language, whatsapp_opted_in)
    VALUES (v_teacher1_id, v_tenant_id, 'teacher1@shumba.co.zw', v_password_hash,
            'Patience', 'Chimanimani', '+263 772 200 200',
            'TEACHER', true, 'en', false)
    ON CONFLICT (id) DO UPDATE SET password_hash = v_password_hash, is_active = true;

    INSERT INTO users (id, tenant_id, email, password_hash, first_name, last_name,
                       phone, role, is_active, language, whatsapp_opted_in)
    VALUES (v_teacher2_id, v_tenant_id, 'teacher2@shumba.co.zw', v_password_hash,
            'Edmund', 'Mangwanda', '+263 772 200 201',
            'TEACHER', true, 'en', false)
    ON CONFLICT (id) DO UPDATE SET password_hash = v_password_hash, is_active = true;

    -- ─────────────────── 3. Grade levels ────────────────────
    -- grade_level_ids are optional on classes; we create the four Form
    -- levels so they can be used elsewhere (merit list by grade).
    INSERT INTO grade_levels (id, tenant_id, code, name, display_order, is_active)
    VALUES
        (md5('shumba-gl-f1')::uuid, v_tenant_id, 'FORM_1', 'Form 1', 1, true),
        (md5('shumba-gl-f2')::uuid, v_tenant_id, 'FORM_2', 'Form 2', 2, true),
        (md5('shumba-gl-f3')::uuid, v_tenant_id, 'FORM_3', 'Form 3', 3, true),
        (md5('shumba-gl-f4')::uuid, v_tenant_id, 'FORM_4', 'Form 4', 4, true)
    ON CONFLICT (id) DO NOTHING;

    -- ─────────────────── 4. Grading system ───────────────────
    INSERT INTO grading_systems (id, tenant_id, name, description, is_default, is_active, grades)
    VALUES (v_grading_system_id, v_tenant_id, 'ZIMSEC O-Level',
            'Zimbabwe ZIMSEC O-Level grading (2019+ scheme). C or better = pass.',
            true, true, v_grading_bands)
    ON CONFLICT (id) DO UPDATE SET
        grades = EXCLUDED.grades,
        is_default = true,
        is_active = true;

    -- Clear is_default on anything else so our scale wins
    UPDATE grading_systems SET is_default = false
    WHERE tenant_id = v_tenant_id AND id <> v_grading_system_id;

    -- ─────────────────── 5. Classes ─────────────────────────
    FOR i IN 1..v_class_count LOOP
        v_class_id := md5('shumba-class-' || i)::uuid;
        v_class_ids := v_class_ids || v_class_id;
        INSERT INTO school_classes (id, tenant_id, name, description, grade_level_id,
                                    grade_level, capacity, is_active, grading_system_id)
        VALUES (v_class_id, v_tenant_id, v_class_names[i],
                v_grade_level_names[i] || ' classroom',
                CASE v_grade_level_names[i]
                     WHEN 'Form 1' THEN md5('shumba-gl-f1')::uuid
                     WHEN 'Form 2' THEN md5('shumba-gl-f2')::uuid
                     WHEN 'Form 3' THEN md5('shumba-gl-f3')::uuid
                     WHEN 'Form 4' THEN md5('shumba-gl-f4')::uuid
                END,
                v_grade_level_names[i],
                30, true, v_grading_system_id)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            grading_system_id = v_grading_system_id,
            is_active = true;
    END LOOP;

    -- ─────────────────── 6. Teacher ↔ class ─────────────────
    INSERT INTO teacher_classes (id, teacher_id, class_id, is_primary, assigned_at)
    VALUES
        (md5('shumba-tc-1')::uuid, v_teacher1_id, v_class_ids[5], true, now()),  -- Form 3A primary
        (md5('shumba-tc-2')::uuid, v_teacher1_id, v_class_ids[6], false, now()),
        (md5('shumba-tc-3')::uuid, v_teacher2_id, v_class_ids[7], true, now()),  -- Form 4A primary
        (md5('shumba-tc-4')::uuid, v_teacher2_id, v_class_ids[8], false, now())
    ON CONFLICT (id) DO NOTHING;

    -- ─────────────────── 7. Subjects ───────────────────────
    FOR i IN 1..array_length(v_subject_names, 1) LOOP
        v_subject_id := md5('shumba-subj-' || v_subject_codes[i])::uuid;
        v_subject_ids := v_subject_ids || v_subject_id;
        INSERT INTO subjects (id, tenant_id, name, code, description,
                              default_total_marks, category, display_order, is_active,
                              curriculum_pack_code)
        VALUES (v_subject_id, v_tenant_id, v_subject_names[i], v_subject_codes[i],
                NULL, v_subject_totals[i], 'SECONDARY_O', i, true, 'ZW_ZIMSEC')
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            default_total_marks = EXCLUDED.default_total_marks,
            is_active = true;
    END LOOP;

    -- ─────────────────── 8. Class ↔ subject ─────────────────
    FOR i IN 1..v_class_count LOOP
        FOR j IN 1..array_length(v_subject_ids, 1) LOOP
            INSERT INTO class_subjects (id, class_id, subject_id,
                                        total_marks, is_compulsory, display_order)
            VALUES (md5('shumba-cs-' || i || '-' || j)::uuid,
                    v_class_ids[i], v_subject_ids[j],
                    v_subject_totals[j], true, j)
            ON CONFLICT (id) DO NOTHING;
        END LOOP;
    END LOOP;

    -- ─────────────────── 9. Report template ─────────────────
    -- Build the ACADEMIC_GRADES section with the subject list inside the
    -- template so the MIS walker can find per-subject total_marks.
    INSERT INTO report_templates (id, tenant_id, name, description, report_type, frequency,
                                  sections, grading_system_id, display_order, is_active)
    VALUES (v_template_id, v_tenant_id, 'ZIMSEC Term Report',
            'Standard termly report card for Form 1 to Form 4.',
            'REPORT_CARD', 'TERMLY',
            jsonb_build_array(
                jsonb_build_object(
                    'id', v_section_id,
                    'type', 'ACADEMIC_GRADES',
                    'title', 'Academic grades',
                    'display_order', 1,
                    'subjects', (
                        SELECT jsonb_agg(jsonb_build_object(
                            'id', v_subject_ids[sub_idx]::text,
                            'name', v_subject_names[sub_idx],
                            'total_marks', v_subject_totals[sub_idx]
                        ) ORDER BY sub_idx)
                        FROM generate_subscripts(v_subject_ids, 1) AS sub_idx
                    ),
                    'grading_system', v_grading_bands
                )
            ),
            v_grading_system_id, 1, true)
    ON CONFLICT (id) DO UPDATE SET
        sections = EXCLUDED.sections,
        grading_system_id = v_grading_system_id,
        is_active = true;

    -- ─────────────────── 10. Students + parents ─────────────
    FOR i IN 1..100 LOOP
        v_first_name := v_first_names[1 + ((i - 1) % array_length(v_first_names, 1))];
        v_last_name := v_last_names[1 + ((i * 7 + 3) % array_length(v_last_names, 1))];
        v_student_id := md5('shumba-student-' || i)::uuid;
        v_student_ids := v_student_ids || v_student_id;
        v_class_idx := 1 + ((i - 1) % v_class_count);
        v_dob := date '2013-01-01' + ((i * 11) % (4 * 365));  -- spread across 4 years

        INSERT INTO students (id, tenant_id, first_name, last_name,
                              date_of_birth, gender, class_id, grade_level,
                              enrollment_date, is_active)
        VALUES (v_student_id, v_tenant_id, v_first_name, v_last_name,
                v_dob,
                CASE WHEN i % 2 = 0 THEN 'FEMALE' ELSE 'MALE' END,
                v_class_ids[v_class_idx],
                v_grade_level_names[v_class_idx],
                date '2024-01-15', true)
        ON CONFLICT (id) DO UPDATE SET
            class_id = v_class_ids[v_class_idx],
            grade_level = v_grade_level_names[v_class_idx],
            is_active = true;

        -- Parent user (lpad to 3 so emails sort nicely)
        v_parent_id := md5('shumba-parent-' || i)::uuid;
        INSERT INTO users (id, tenant_id, email, password_hash, first_name, last_name,
                           phone, role, is_active, language, whatsapp_opted_in)
        VALUES (v_parent_id, v_tenant_id,
                'parent' || lpad(i::text, 3, '0') || '@shumba.co.zw',
                v_password_hash,
                'Mr/Mrs', v_last_name || ' (Parent)',
                '+263 7' || lpad((70 + i)::text, 2, '0') || ' ' || lpad(i::text, 3, '0') || ' ' || lpad((i * 7)::text, 3, '0'),
                'PARENT', true, 'en', false)
        ON CONFLICT (id) DO UPDATE SET
            password_hash = v_password_hash,
            is_active = true;

        -- Link parent to student
        INSERT INTO parent_students (id, parent_id, student_id, relationship, is_primary)
        VALUES (md5('shumba-ps-' || i)::uuid, v_parent_id, v_student_id, 'PARENT', true)
        ON CONFLICT (id) DO NOTHING;
    END LOOP;

    -- ─────────────────── 11. Reports (3 years × 3 terms) ────
    -- Mid-term dates: Feb / May / Aug for each year. 100 × 9 = 900 reports.
    FOR i IN 1..100 LOOP
        v_class_idx := 1 + ((i - 1) % v_class_count);
        -- Per-student baseline + slope so trend lines + rankings move
        v_target_mean := 45 + ((i * 17) % 40);   -- range 45..85
        v_term_offset := (((i * 13) % 11) - 5) * 0.5;  -- -2.5..+2.5 per term
        FOR v_year IN 2024..2026 LOOP
            FOR v_term IN 1..3 LOOP
                v_report_date := make_date(v_year,
                                           CASE v_term WHEN 1 THEN 2 WHEN 2 THEN 5 ELSE 8 END,
                                           15);
                v_report_id := md5('shumba-rep-' || i || '-' || v_year || '-' || v_term)::uuid;
                -- Build per-subject marks as JSONB
                v_subject_data := '{}'::jsonb;
                FOR j IN 1..array_length(v_subject_ids, 1) LOOP
                    v_total_marks := v_subject_totals[j];
                    -- Deterministic "random" so re-runs give the same numbers
                    v_subject_bias := (((i * 31 + j * 7) % 20) - 10);
                    v_marks_obtained := round(
                        v_target_mean
                        + v_subject_bias
                        + (v_term_offset * ((v_year - 2024) * 3 + v_term - 1))
                        + ((((i * 97 + j * 53 + v_year * 11 + v_term * 7) % 15) - 7))
                    )::int;
                    v_marks_obtained := greatest(0, least(v_total_marks, v_marks_obtained));
                    v_subject_data := v_subject_data
                        || jsonb_build_object(
                            v_subject_ids[j]::text,
                            jsonb_build_object('marks_obtained', v_marks_obtained, 'remarks', '')
                        );
                END LOOP;
                v_report_data := jsonb_build_object('sections',
                    jsonb_build_object(v_section_id, v_subject_data));

                INSERT INTO daily_reports (id, tenant_id, student_id, class_id,
                                           template_id, report_date, status,
                                           report_data, finalized_at, created_by)
                VALUES (v_report_id, v_tenant_id,
                        v_student_ids[i], v_class_ids[v_class_idx],
                        v_template_id, v_report_date, 'FINALIZED',
                        v_report_data, v_report_date::timestamp + time '15:00',
                        v_admin_id)
                ON CONFLICT (id) DO UPDATE SET
                    report_data = EXCLUDED.report_data,
                    status = 'FINALIZED';
                v_total_reports := v_total_reports + 1;
            END LOOP;
        END LOOP;
    END LOOP;

    -- ─────────────────── 12. Attendance (current term) ──────
    -- 30 sessions per student across the last 60 days.
    FOR i IN 1..100 LOOP
        v_class_idx := 1 + ((i - 1) % v_class_count);
        v_target_attendance := 60 + ((i * 17) % 38);   -- 60..98 %
        FOR k IN 0..29 LOOP
            v_att_date := CURRENT_DATE - (k * 2);
            v_random_roll := ((i * 31 + k * 17) % 100)::numeric;
            v_att_status :=
                CASE
                    WHEN v_random_roll <= v_target_attendance - 4 THEN 'PRESENT'
                    WHEN v_random_roll <= v_target_attendance     THEN 'LATE'
                    WHEN v_random_roll <= v_target_attendance + 2 THEN 'EXCUSED'
                    ELSE 'ABSENT'
                END;
            INSERT INTO attendance_records (id, tenant_id, student_id, class_id,
                                            date, status, recorded_by)
            VALUES (md5('shumba-att-' || i || '-' || k)::uuid,
                    v_tenant_id, v_student_ids[i], v_class_ids[v_class_idx],
                    v_att_date, v_att_status, v_admin_id)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status;
            v_total_attendance := v_total_attendance + 1;
        END LOOP;
    END LOOP;

    -- ─────────────────── 13. Fee items ──────────────────────
    INSERT INTO billing_fee_items (id, tenant_id, name, description, amount,
                                   frequency, applies_to, class_id, is_active, display_order)
    VALUES
        (md5('shumba-fee-tuition')::uuid, v_tenant_id,
         'Tuition', 'Termly tuition fee', 450.00,
         'TERMLY', 'ALL', NULL, true, 1),
        (md5('shumba-fee-boarding')::uuid, v_tenant_id,
         'Boarding', 'Boarding fee (termly)', 650.00,
         'TERMLY', 'ALL', NULL, true, 2),
        (md5('shumba-fee-books')::uuid, v_tenant_id,
         'Books & stationery', 'Annual textbook levy', 120.00,
         'ANNUALLY', 'ALL', NULL, true, 3)
    ON CONFLICT (id) DO UPDATE SET amount = EXCLUDED.amount, is_active = true;

    -- ─────────────────── 14. Invoices + payments ────────────
    -- Current year, three terms. Each invoice carries tuition + boarding.
    FOR i IN 1..100 LOOP
        FOR v_term IN 1..3 LOOP
            v_invoice_id := md5('shumba-inv-' || i || '-' || v_term)::uuid;
            v_invoice_number := 'INV-2026-' || lpad(((i - 1) * 3 + v_term)::text, 4, '0');
            v_due_date := make_date(2026,
                                    CASE v_term WHEN 1 THEN 2 WHEN 2 THEN 5 ELSE 8 END, 28);

            -- Pay scenario: 70% paid, 15% partial, 15% sent (unpaid)
            v_pay_scenario := (i + v_term) % 10;
            v_amount_paid := CASE
                WHEN v_pay_scenario < 7 THEN 1100.00   -- fully paid
                WHEN v_pay_scenario < 9 THEN 500.00    -- partial
                ELSE 0.00
            END;

            INSERT INTO billing_invoices (id, tenant_id, student_id, invoice_number,
                                          billing_period_start, billing_period_end,
                                          due_date, subtotal, total_amount,
                                          amount_paid, balance, status,
                                          notes, created_by,
                                          sent_at)
            VALUES (v_invoice_id, v_tenant_id, v_student_ids[i], v_invoice_number,
                    make_date(2026, CASE v_term WHEN 1 THEN 1 WHEN 2 THEN 4 ELSE 7 END, 1),
                    v_due_date, v_due_date,
                    1100.00, 1100.00,
                    v_amount_paid, 1100.00 - v_amount_paid,
                    CASE WHEN v_amount_paid >= 1100 THEN 'PAID'
                         WHEN v_amount_paid > 0 THEN 'PARTIALLY_PAID'
                         ELSE 'SENT'
                    END,
                    'Term ' || v_term || ' 2026 — tuition + boarding',
                    v_admin_id,
                    v_due_date::timestamp - interval '30 days')
            ON CONFLICT (id) DO UPDATE SET
                amount_paid = EXCLUDED.amount_paid,
                balance = EXCLUDED.balance,
                status = EXCLUDED.status;
            v_total_invoices := v_total_invoices + 1;

            -- Invoice line items (tuition + boarding)
            INSERT INTO billing_invoice_items (id, invoice_id, fee_item_id,
                                               description, quantity, unit_amount, total_amount)
            VALUES
                (md5('shumba-ili-' || i || '-' || v_term || '-tuition')::uuid,
                 v_invoice_id, md5('shumba-fee-tuition')::uuid,
                 'Tuition Term ' || v_term, 1, 450.00, 450.00),
                (md5('shumba-ili-' || i || '-' || v_term || '-boarding')::uuid,
                 v_invoice_id, md5('shumba-fee-boarding')::uuid,
                 'Boarding Term ' || v_term, 1, 650.00, 650.00)
            ON CONFLICT (id) DO NOTHING;

            -- Payment record where applicable
            IF v_amount_paid > 0 THEN
                INSERT INTO billing_payments (id, tenant_id, invoice_id, student_id,
                                              amount, payment_method, reference_number,
                                              payment_date, notes, recorded_by)
                VALUES (md5('shumba-pay-' || i || '-' || v_term)::uuid,
                        v_tenant_id, v_invoice_id, v_student_ids[i],
                        v_amount_paid, 'BANK_TRANSFER',
                        'TXN-' || lpad(i::text, 3, '0') || '-' || v_term,
                        v_due_date - 5, 'Term ' || v_term || ' payment',
                        v_admin_id)
                ON CONFLICT (id) DO UPDATE SET amount = EXCLUDED.amount;
                v_total_payments := v_total_payments + 1;
            END IF;
        END LOOP;
    END LOOP;

    -- ─────────────────── 15. Chart of accounts ──────────────
    -- Seed the ZIMSEC/Zimbabwe-flavoured starter chart. The app's own
    -- seeder would do the same on first dashboard load; this just makes
    -- sure it's in place before the accounting page is opened.
    INSERT INTO chart_accounts (id, tenant_id, code, name, type, description,
                                is_active, is_system, invoice_only, display_order)
    VALUES
        (md5('shumba-ca-4000')::uuid, v_tenant_id, '4000', 'Tuition Fees', 'INCOME', NULL, true, true, true, 1),
        (md5('shumba-ca-4010')::uuid, v_tenant_id, '4010', 'Registration Fees', 'INCOME', NULL, true, false, false, 2),
        (md5('shumba-ca-4020')::uuid, v_tenant_id, '4020', 'Uniform Sales', 'INCOME', NULL, true, false, false, 3),
        (md5('shumba-ca-4030')::uuid, v_tenant_id, '4030', 'Donations', 'INCOME', NULL, true, false, false, 4),
        (md5('shumba-ca-5000')::uuid, v_tenant_id, '5000', 'Salaries & Wages', 'EXPENSE', NULL, true, false, false, 5),
        (md5('shumba-ca-5010')::uuid, v_tenant_id, '5010', 'Rent', 'EXPENSE', NULL, true, false, false, 6),
        (md5('shumba-ca-5020')::uuid, v_tenant_id, '5020', 'Utilities', 'EXPENSE', NULL, true, false, false, 7),
        (md5('shumba-ca-5030')::uuid, v_tenant_id, '5030', 'Stationery & Supplies', 'EXPENSE', NULL, true, false, false, 8),
        (md5('shumba-ca-5100')::uuid, v_tenant_id, '5100', 'Catering', 'EXPENSE', NULL, true, false, false, 9),
        (md5('shumba-ca-1000')::uuid, v_tenant_id, '1000', 'Cash on Hand', 'ASSET', NULL, true, false, false, 10)
    ON CONFLICT (id) DO UPDATE SET is_active = true;

    -- ─────────────────── 16. Bank account ───────────────────
    INSERT INTO bank_accounts (id, tenant_id, name, bank_name, account_number,
                               branch_code, account_type, currency,
                               opening_balance, opening_balance_date,
                               is_default, is_active)
    VALUES (v_bank_id, v_tenant_id, 'Operating Account',
            'CBZ Bank', '01234567890', 'CBZHZWHA',
            'OPERATING', 'USD',
            10000.00, date '2026-01-01', true, true)
    ON CONFLICT (id) DO UPDATE SET is_active = true, is_default = true;

    -- ─────────────────── 17. Accounting transactions ────────
    -- Seed a few expense transactions so the P&L + Cash position have
    -- something to show alongside the auto-linked billing income.
    INSERT INTO accounting_transactions (id, tenant_id, date, type, amount, currency,
                                         account_id, bank_account_id,
                                         description, reference, created_by)
    VALUES
        (md5('shumba-tx-exp-1')::uuid, v_tenant_id, date '2026-09-15',
         'EXPENSE', 1850.00, 'USD',
         md5('shumba-ca-5000')::uuid, v_bank_id,
         'September salaries', 'SAL-2026-09', v_admin_id),
        (md5('shumba-tx-exp-2')::uuid, v_tenant_id, date '2026-10-05',
         'EXPENSE', 420.00, 'USD',
         md5('shumba-ca-5020')::uuid, v_bank_id,
         'ZESA electricity', 'ZESA-2026-10', v_admin_id),
        (md5('shumba-tx-exp-3')::uuid, v_tenant_id, date '2026-10-20',
         'EXPENSE', 310.00, 'USD',
         md5('shumba-ca-5030')::uuid, v_bank_id,
         'Textbooks + printing', 'PO-2026-45', v_admin_id),
        (md5('shumba-tx-exp-4')::uuid, v_tenant_id, date '2026-11-02',
         'EXPENSE', 950.00, 'USD',
         md5('shumba-ca-5100')::uuid, v_bank_id,
         'Boarders catering — November', 'CAT-2026-11', v_admin_id)
    ON CONFLICT (id) DO UPDATE SET amount = EXCLUDED.amount;

    -- Billing payments → INCOME transactions.
    -- Each paid-up invoice gets a matching Tuition Fees income entry so the
    -- P&L lines up with billing. Uses billing_payment_id for the UNIQUE
    -- idempotency guard the accounting service relies on.
    INSERT INTO accounting_transactions (id, tenant_id, date, type, amount, currency,
                                         account_id, bank_account_id,
                                         student_id, billing_payment_id,
                                         description, reference, created_by)
    SELECT md5('shumba-tx-income-' || bp.id::text)::uuid,
           v_tenant_id, bp.payment_date, 'INCOME', bp.amount, 'USD',
           md5('shumba-ca-4000')::uuid, v_bank_id,
           bp.student_id, bp.id,
           'Tuition payment — ' || bi.invoice_number,
           bp.reference_number, v_admin_id
    FROM billing_payments bp
    JOIN billing_invoices bi ON bi.id = bp.invoice_id
    WHERE bp.tenant_id = v_tenant_id
    ON CONFLICT (id) DO UPDATE SET amount = EXCLUDED.amount;

    RAISE NOTICE 'Shumba High School seed complete:';
    RAISE NOTICE '  Tenant id: %', v_tenant_id;
    RAISE NOTICE '  Reports inserted/updated:  %', v_total_reports;
    RAISE NOTICE '  Attendance rows:            %', v_total_attendance;
    RAISE NOTICE '  Invoices:                   %', v_total_invoices;
    RAISE NOTICE '  Payments:                   %', v_total_payments;
    RAISE NOTICE 'Login: head@shumba.co.zw / Demo@2026';
END;
$BLOCK$;

-- ─────────────────── Verification queries ─────────────────────
-- Run these in DBeaver after the DO block to confirm everything landed.
-- Expected totals per tenant 'shumba-high':
--   students:      100
--   classes:         8
--   subjects:       10
--   templates:       1
--   reports:       900
--   attendance: ~3000
--   invoices:      300
--   payments:    ~240

SELECT 'students'     AS entity, COUNT(*) FROM students
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'classes',     COUNT(*) FROM school_classes
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'subjects',    COUNT(*) FROM subjects
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'templates',   COUNT(*) FROM report_templates
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'reports',     COUNT(*) FROM daily_reports
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'attendance',  COUNT(*) FROM attendance_records
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high')
UNION ALL
SELECT 'invoices',    COUNT(*) FROM billing_invoices
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'payments',    COUNT(*) FROM billing_payments
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL
UNION ALL
SELECT 'users (all roles)', COUNT(*) FROM users
   WHERE tenant_id = (SELECT id FROM tenants WHERE slug = 'shumba-high') AND deleted_at IS NULL;
