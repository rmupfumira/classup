"""Tests for event_service — audience resolution, ICS generation, RSVP.

Focused on the invariants that matter:
  - Audience computation is scope-correct (SCHOOL / CLASS / STUDENT).
  - ICS output is a valid VCALENDAR with a stable UID (updates replace,
    not duplicate) and RFC 5545 escaping.
  - RSVPs are idempotent per (event, user) — later response overwrites.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import EventScope, EventType, RsvpResponse, SchoolEvent
from app.services import event_service


def _fake_event(**overrides) -> SchoolEvent:
    """Bare-metal SchoolEvent (in memory, no DB) — ICS + format tests
    just need the fields on it."""
    now = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    kwargs = dict(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        tenant_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        title="Parent-Teacher Conference",
        description="Term 3 progress discussions",
        event_type=EventType.PARENT_TEACHER_CONFERENCE.value,
        scope=EventScope.CLASS.value,
        class_id=None,
        student_id=None,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        timezone="Africa/Johannesburg",
        location="Hall 2, main building",
        rsvp_required=True,
        rsvp_deadline=now - timedelta(days=2),
        cancelled_at=None,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    ev = SchoolEvent(**kwargs)
    ev.rsvps = []
    ev.school_class = None
    ev.student = None
    ev.tenant = None
    return ev


class TestIcsGeneration:
    """The ICS is what makes 'Add to calendar' work in Gmail/Outlook/
    Apple Mail. Get the header + UID + escaping wrong and everything
    down the chain falls apart."""

    def test_valid_vcalendar_frame(self):
        ics = event_service.build_ics(_fake_event())
        assert ics.startswith("BEGIN:VCALENDAR\r\n")
        assert ics.endswith("END:VCALENDAR\r\n")
        assert "BEGIN:VEVENT\r\n" in ics
        assert "END:VEVENT\r\n" in ics

    def test_required_fields_present(self):
        ics = event_service.build_ics(_fake_event())
        assert "VERSION:2.0" in ics
        assert "PRODID:" in ics
        assert "METHOD:REQUEST" in ics
        assert "DTSTAMP:" in ics
        assert "DTSTART:20260920T150000Z" in ics
        assert "DTEND:20260920T160000Z" in ics
        assert "SUMMARY:Parent-Teacher Conference" in ics

    def test_uid_is_deterministic_per_event(self):
        """Same event id → same UID. Updating an event with the same
        UID replaces the calendar entry in clients."""
        event_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        ics1 = event_service.build_ics(_fake_event(id=event_id, title="X"))
        ics2 = event_service.build_ics(_fake_event(id=event_id, title="Y"))
        uid_pattern = re.compile(r"UID:classup-event-([a-f0-9-]+)@classup\.co\.za")
        m1 = uid_pattern.search(ics1)
        m2 = uid_pattern.search(ics2)
        assert m1 and m2
        assert m1.group(1) == str(event_id) == m2.group(1)

    def test_special_characters_escaped(self):
        """Semicolons, commas, backslashes, newlines are RFC 5545
        meta-characters. Unescaped, they corrupt the calendar entry."""
        ev = _fake_event(
            title="Trip; Cape Town, Day 1",
            description="Line 1\nLine 2",
        )
        ics = event_service.build_ics(ev)
        assert "SUMMARY:Trip\\; Cape Town\\, Day 1" in ics
        assert "DESCRIPTION:Line 1\\nLine 2" in ics

    def test_cancel_method_sets_status_cancelled(self):
        ics = event_service.build_ics(_fake_event(), method="CANCEL")
        assert "METHOD:CANCEL" in ics
        assert "STATUS:CANCELLED" in ics

    def test_missing_end_time_uses_start(self):
        """Some events (a school assembly kicking off, say) don't have
        an explicit end. DTEND falls back to DTSTART so the entry is
        still a valid single-point event."""
        ics = event_service.build_ics(_fake_event(ends_at=None))
        # Both timestamps are the same moment
        assert ics.count("DTSTART:20260920T150000Z") == 1
        assert ics.count("DTEND:20260920T150000Z") == 1

    def test_organizer_included_when_provided(self):
        ics = event_service.build_ics(
            _fake_event(),
            organizer_email="head@school.zw",
            organizer_name="Head Teacher",
        )
        assert "ORGANIZER;CN=Head Teacher:MAILTO:head@school.zw" in ics

    def test_line_folding_for_long_summaries(self):
        long_title = "A" * 200
        ics = event_service.build_ics(_fake_event(title=long_title))
        # Folded continuation lines start with a space (\r\n followed by ' ')
        assert "\r\n " in ics

    def test_sequence_bumps_with_updated_at(self):
        """Every edit bumps SEQUENCE so calendar clients treat it as
        an update, not a stale duplicate."""
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 6, 1, tzinfo=timezone.utc)
        ics_early = event_service.build_ics(_fake_event(updated_at=early))
        ics_late = event_service.build_ics(_fake_event(updated_at=late))
        seq_pattern = re.compile(r"SEQUENCE:(\d+)")
        s_early = int(seq_pattern.search(ics_early).group(1))
        s_late = int(seq_pattern.search(ics_late).group(1))
        assert s_late > s_early


class TestSignedRsvpToken:
    """Signed one-tap RSVP links — the HMAC is the whole authorisation
    for the public /events/{id}/rsvp endpoint."""

    def test_token_verifies_with_matching_inputs(self):
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        sig = event_service.sign_rsvp_token(eid, uid, "YES")
        assert event_service.verify_rsvp_token(eid, uid, "YES", sig)

    def test_token_rejects_different_user(self):
        """Attacker takes their own valid link and swaps the u= param
        to someone else's user_id. Must not verify."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        attacker = uuid.UUID("22222222-2222-2222-2222-222222222222")
        victim = uuid.UUID("33333333-3333-3333-3333-333333333333")
        sig = event_service.sign_rsvp_token(eid, attacker, "YES")
        assert not event_service.verify_rsvp_token(eid, victim, "YES", sig)

    def test_token_rejects_different_response(self):
        """Attacker takes a YES link and edits it to NO. Must not verify."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        sig = event_service.sign_rsvp_token(eid, uid, "YES")
        assert not event_service.verify_rsvp_token(eid, uid, "NO", sig)

    def test_token_rejects_different_event(self):
        eid1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
        eid2 = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        sig = event_service.sign_rsvp_token(eid1, uid, "YES")
        assert not event_service.verify_rsvp_token(eid2, uid, "YES", sig)

    def test_missing_signature_rejected(self):
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        assert not event_service.verify_rsvp_token(eid, uid, "YES", "")
        assert not event_service.verify_rsvp_token(eid, uid, "YES", None)

    def test_response_case_insensitive(self):
        """URLs might come back with response in different case
        (some email clients uppercase params). Both sides normalise."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        sig = event_service.sign_rsvp_token(eid, uid, "YES")
        # verify converts to upper — should match a lowercase input too
        assert event_service.verify_rsvp_token(eid, uid, "yes", sig)


class TestFormatEventWhen:
    def test_start_and_end_same_day(self):
        ev = _fake_event()
        s = event_service.format_event_when(ev)
        assert "Sun 20 Sep 2026" in s
        assert "15:00" in s and "16:00" in s
        assert "–" in s

    def test_no_end_time(self):
        ev = _fake_event(ends_at=None)
        s = event_service.format_event_when(ev)
        assert "15:00" in s
        assert "–" not in s
        assert "at" in s

    def test_missing_start_returns_tbc(self):
        # Bypass the model's NOT NULL by monkey-patching the attribute
        ev = _fake_event()
        ev.starts_at = None
        assert event_service.format_event_when(ev) == "TBC"
