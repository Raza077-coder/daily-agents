"""Pipeline operations for JOBFLOW — the verbs a job hunt actually uses.

Every mutation goes through :class:`Pipeline`, which appends an :class:`Event`
for anything that changes the world.  Nothing here reads the wall clock: the
caller passes ``today``, which is what makes a run reproducible.

The interesting rules:

* :meth:`Pipeline.move` **refuses to skip rungs**.  Going from ``applied``
  straight to ``offer`` is almost always a typo, and silently allowing it
  corrupts the funnel.  Pass ``force=True`` for the genuine exceptions (an
  internal transfer with no interview loop).
* :meth:`Pipeline.log_stage` exists for backfill: recording that the onsite
  happened last Tuesday, not that it is happening now.
* Rejections are recorded with the stage reached, so a rejection after an
  onsite still counts as having reached the onsite rung in the funnel.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .models import (
    ACTIVE,
    CLOSED,
    INTERVIEW_KINDS,
    PRE,
    SOURCES,
    STAGE_INDEX,
    STAGES,
    STATUSES,
    TERMINAL,
    WORK_MODES,
    Application,
    Event,
    Interview,
    JobFlowError,
    Note,
    NotFoundError,
    ValidationError,
    format_money,
    make_id,
    one_of,
    parse_date,
    parse_money,
    parse_priority,
    slugify,
)
from .store import Vault

#: Status -> allowed next statuses for an unforced move.
_FORWARD = {name: set(STAGES[STAGE_INDEX[name] + 1 :]) for name in STAGES}


class Pipeline:
    """All state-changing operations, on top of a :class:`Vault`."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    # -- internals ---------------------------------------------------------

    def _resolve_today(self, today: Optional[Any]) -> date:
        if today is None:
            return date.today()
        parsed = parse_date(today, "today")
        if parsed is None:
            raise ValidationError("today cannot be empty")
        return parsed

    def _record(self, app: Application, event: Event) -> Application:
        app.events.append(event)
        app.events.sort(key=lambda e: (e.on, e.kind))
        return app

    # -- create ------------------------------------------------------------

    def add(
        self,
        company: str,
        role: str,
        status: str = "wishlist",
        today: Optional[Any] = None,
        applied_on: Optional[Any] = None,
        **fields: Any,
    ) -> Application:
        """Create an application.

        When ``status`` is at or past ``applied`` the application is treated as
        already submitted: an ``applied`` event is written so the funnel and
        the follow-up rules see it immediately.
        """

        when = self._resolve_today(today)
        status = one_of(status, STATUSES, "status")
        app_id = fields.pop("id", None) or make_id(company, role, self.vault.ids())

        created_on = parse_date(fields.pop("created_on", None), "created_on")
        submitted = parse_date(applied_on, "applied_on")
        if submitted is None and status in STAGES[1:]:
            submitted = created_on or when
        if status == "applied" and submitted is None:
            submitted = created_on or when
        # A backdated submission means the record was created *then*, not
        # today.  Without this, adding an application you sent last week
        # fails the "applied before created" invariant.  An explicit
        # created_on still wins so imports can be deliberate; it is only
        # clamped when it would sit after the submission it precedes.
        if created_on is None:
            created_on = submitted or when
        elif submitted is not None and created_on > submitted:
            created_on = submitted

        app = Application(
            id=app_id,
            company=company,
            role=role,
            status=status,
            created_on=created_on.isoformat(),
            applied_on=submitted.isoformat() if submitted else None,
            **fields,
        )

        self._record(
            app,
            Event(on=created_on.isoformat(), kind="created", to_status="wishlist"),
        )
        if submitted is not None:
            events = [e for e in app.events if e.kind == "applied"]
            if not events:
                self._record(
                    app,
                    Event(
                        on=submitted.isoformat(),
                        kind="applied",
                        from_status="wishlist",
                        to_status="applied",
                    ),
                )
        if status not in ("wishlist", "applied"):
            # Jumping straight to an advanced status is accepted on creation
            # (you may be importing history) but it is recorded explicitly.
            self._record(
                app,
                Event(
                    on=(submitted or created_on).isoformat(),
                    kind="moved",
                    from_status="applied",
                    to_status=status,
                    note="imported at creation",
                ),
            )
        app.validate()
        self.vault.add(app)
        return app

    # -- transitions -------------------------------------------------------

    def move(
        self,
        app_id: str,
        to_status: str,
        today: Optional[Any] = None,
        note: str = "",
        force: bool = False,
    ) -> Application:
        """Change the status of an application, recording the event."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        target = one_of(to_status, STATUSES, "to_status")
        current = app.status
        if target == current:
            raise ValidationError(f"{app.id} is already {current!r}")

        if not force:
            self._check_transition(app, current, target, when)

        self._record(
            app,
            Event(
                on=when.isoformat(),
                kind="closed" if target in CLOSED else "moved",
                from_status=current,
                to_status=target,
                note=note,
            ),
        )
        app.status = target

        if target in STAGES[1:] and not app.applied_on:
            applied = [e for e in app.events if e.kind == "applied"]
            app.applied_on = min(e.on for e in applied) if applied else when.isoformat()
        if target in CLOSED and not app.closed_on:
            app.closed_on = when.isoformat()
        if target in ACTIVE:
            app.closed_on = None
        app.validate()
        return app

    def _check_transition(
        self, app: Application, current: str, target: str, when: date
    ) -> None:
        """Reject illogical moves with an explanation of what is allowed."""

        if current in PRE and target in TERMINAL:
            raise ValidationError(
                f"{app.id} was never applied to, so it cannot be marked {target!r} — "
                f"use 'drop' to remove it from the wishlist instead"
            )

        if current in TERMINAL:
            raise ValidationError(
                f"{app.id} is already closed ({current!r}) — reopen it first with "
                f"'reopen' if you truly want to continue"
            )

        if current in STAGE_INDEX and target in STAGE_INDEX:
            if STAGE_INDEX[target] < STAGE_INDEX[current]:
                raise ValidationError(
                    f"{app.id}: cannot move backwards from {current!r} to {target!r}. "
                    "If the employer restarted the loop, log the new stage with "
                    "'stage' and a note, or pass force=True."
                )
            skipped = [
                name
                for name in STAGES[STAGE_INDEX[current] + 1 : STAGE_INDEX[target]]
            ]
            if skipped:
                raise ValidationError(
                    f"{app.id}: moving {current!r} -> {target!r} skips "
                    f"{', '.join(repr(s) for s in skipped)}. If that is real (rare), "
                    "pass force=True; otherwise log the skipped stage first."
                )

        if target in ACTIVE and app.status in ("rejected", "withdrawn"):
            raise ValidationError(
                f"{app.id} was {app.status!r} — reopen it explicitly before moving it forward"
            )

    def log_stage(
        self,
        app_id: str,
        stage: str,
        on: Optional[Any] = None,
        note: str = "",
        today: Optional[Any] = None,
    ) -> Application:
        """Backfill a stage that already happened, without moving the status.

        This is the honest way to record history: ``stage acme-onsite onsite
        --on 2026-09-10`` says "the onsite happened on the 10th", which raises
        the funnel rung even if the application was rejected a week later.
        """

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        target = one_of(stage, STAGES, "stage")
        if target == "applied":
            raise ValidationError(
                "use 'log-apply' to record the application date, not 'stage'"
            )
        happened = parse_date(on, "on") or when
        if happened > when:
            raise ValidationError(
                f"cannot backfill {target!r} on {happened.isoformat()} — that is in the future"
            )
        self._record(
            app,
            Event(
                on=happened.isoformat(),
                kind="moved",
                from_status=None,
                to_status=target,
                note=note,
            ),
        )
        if not app.applied_on:
            app.applied_on = happened.isoformat()
        app.validate()
        return app

    def log_apply(
        self,
        app_id: str,
        on: Optional[Any] = None,
        today: Optional[Any] = None,
        note: str = "",
    ) -> Application:
        """Record that the application was submitted, optionally backdated."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        if app.status in TERMINAL:
            raise ValidationError(f"{app.id} is closed ({app.status!r}) — cannot log an application")
        submitted = parse_date(on, "on") or when
        if submitted > when:
            raise ValidationError("cannot log an application date in the future")
        if app.was_applied and (app.applied_on or "") <= submitted.isoformat():
            raise ValidationError(
                f"{app.id} was already applied to on {app.applied_on or 'an earlier date'}"
            )
        previous = app.status
        # Backfilling a submission also backdates the record's origin, or the
        # "applied before created" invariant would reject a legitimate entry.
        if app.created_on and submitted.isoformat() < app.created_on:
            app.created_on = submitted.isoformat()
            app.events.append(
                Event(
                    on=submitted.isoformat(),
                    kind="created",
                    to_status="wishlist",
                    note="backdated on submission",
                )
            )
            app.events.sort(key=lambda e: (e.on, e.kind))

        self._record(
            app,
            Event(
                on=submitted.isoformat(),
                kind="applied",
                from_status=previous,
                to_status="applied",
                note=note,
            ),
        )
        app.applied_on = submitted.isoformat()
        if previous == "wishlist":
            app.status = "applied"
        app.validate()
        return app

    def reopen(
        self, app_id: str, today: Optional[Any] = None, note: str = ""
    ) -> Application:
        """Reopen a closed application (a rejection that turned into a callback)."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        if not app.is_closed:
            raise ValidationError(f"{app.id} is not closed (status is {app.status!r})")
        target = "applied" if app.applied_on else "wishlist"
        self._record(
            app,
            Event(
                on=when.isoformat(),
                kind="moved",
                from_status=app.status,
                to_status=target,
                note=note or "reopened",
            ),
        )
        app.status = target
        app.closed_on = None
        app.validate()
        return app

    # -- interviews, notes, follow-ups -------------------------------------

    def schedule_interview(
        self,
        app_id: str,
        on: Any,
        kind: str = "other",
        note: str = "",
    ) -> Interview:
        """Add an upcoming interview.  Raise the stage only once it is done."""

        app = self.vault.get(app_id)
        interview = Interview(on=on, kind=kind, note=note, done=False)
        app.interviews = [i for i in app.interviews if i.on != interview.on or i.kind != interview.kind]
        app.interviews.append(interview)
        app.interviews.sort(key=lambda i: (i.on, i.kind))
        return interview

    def complete_interview(
        self,
        app_id: str,
        on: Optional[Any] = None,
        note: str = "",
        today: Optional[Any] = None,
    ) -> Application:
        """Mark an interview as done and record the event.

        Completing a ``technical`` interview advances the pipeline to the
        ``interview`` rung, but only forwards — a finished screen never drags
        an onsite-stage application backwards.
        """

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        target_on = parse_date(on, "on")
        candidates = [
            i
            for i in app.interviews
            if not i.done and (target_on is None or i.date == target_on)
        ]
        if not candidates:
            raise ValidationError(
                f"{app.id} has no pending interview"
                + (f" on {target_on.isoformat()}" if target_on else "")
            )
        interview = candidates[0]
        interview.done = True
        if target_on is not None:
            interview.on = target_on.isoformat()
        if note:
            interview.note = note

        self._record(
            app,
            Event(
                on=interview.on,
                kind="interview",
                to_status=app.status if app.status in STAGE_INDEX else None,
                note=note or f"{interview.kind} interview completed",
            ),
        )

        completed = [i for i in app.interviews if i.done]
        if completed and app.status in ("applied", "screen"):
            app.status = "interview"
        app.validate()
        return app

    def note(self, app_id: str, text: str, on: Optional[Any] = None,
             today: Optional[Any] = None) -> Application:
        """Attach a timestamped free-text note."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        stamp = (parse_date(on, "on") or when).isoformat()
        app.notes.append(Note(on=stamp, text=text))
        app.notes.sort(key=lambda n: n.on)
        self._record(app, Event(on=stamp, kind="note", note=text))
        return app

    def followup(
        self,
        app_id: str,
        today: Optional[Any] = None,
        note: str = "",
        channel: str = "",
    ) -> Application:
        """Record that you chased the employer."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        if app.is_closed:
            raise ValidationError(f"{app.id} is closed ({app.status!r}) — nothing to chase")
        if not app.was_applied:
            raise ValidationError(
                f"{app.id} has not been applied to yet — submit the application first"
            )
        text = note
        if channel:
            text = f"[{channel}] {text}".strip()
        self._record(app, Event(on=when.isoformat(), kind="followup", note=text))
        return app

    def set_next_action(
        self,
        app_id: str,
        action: str,
        on: Optional[Any] = None,
    ) -> Application:
        """Set (or clear) the next concrete step and its date."""

        app = self.vault.get(app_id)
        app.next_action = action
        parsed = parse_date(on, "on")
        app.next_action_on = parsed.isoformat() if parsed else None
        return app

    # -- editing -----------------------------------------------------------

    #: Fields a caller may edit, mapped to their parser.
    EDITABLE = {
        "company": str,
        "role": str,
        "location": str,
        "work_mode": str,
        "source": str,
        "url": str,
        "currency": str,
        "priority": "priority",
        "deadline_on": "date",
        "next_action": str,
        "contact": str,
        "status": str,
        "tags": "tags",
    }

    def edit(self, app_id: str, today: Optional[Any] = None, **changes: Any) -> Application:
        """Update scalar fields.  Unknown fields are refused, not ignored."""

        when = self._resolve_today(today)
        app = self.vault.get(app_id)
        changed: List[str] = []
        for key, value in changes.items():
            if value is None:
                continue
            if key in ("salary_min", "salary_max"):
                setattr(app, key, parse_money(value, key))
                changed.append(key)
                continue
            if key not in self.EDITABLE:
                raise ValidationError(
                    f"cannot edit {key!r} — editable fields are: "
                    + ", ".join(sorted(self.EDITABLE))
                )
            if key == "priority":
                app.priority = parse_priority(value)
            elif key == "deadline_on":
                app.deadline_on = (parse_date(value, key) or None) and parse_date(value, key).isoformat()
            elif key == "tags":
                app.tags = Application._clean_tags(value)
            elif key == "status":
                # Route status changes through move() so the event log stays honest.
                self.move(app.id, value, today=when)
                changed.append(key)
                continue
            else:
                setattr(app, key, value)
            changed.append(key)

        if not changed:
            raise ValidationError("nothing to edit — pass at least one field")
        if app.salary_min is not None and app.salary_max is not None and app.salary_min > app.salary_max:
            raise ValidationError(
                "salary_min cannot exceed salary_max "
                f"({format_money(app.salary_min, app.currency)} > "
                f"{format_money(app.salary_max, app.currency)})"
            )
        app.__post_init__()
        self._record(
            app,
            Event(on=when.isoformat(), kind="edited", note="updated " + ", ".join(sorted(set(changed)))),
        )
        app.validate()
        return app

    def drop(self, app_id: str) -> Application:
        """Delete an application outright.  Returns what was removed."""

        return self.vault.remove(app_id)

    # -- bulk helpers ------------------------------------------------------

    def add_many(self, rows: List[Dict[str, Any]], today: Optional[Any] = None) -> List[Application]:
        """Import a batch.  Validation errors are raised before anything lands."""

        created: List[Application] = []
        for row in rows:
            data = dict(row)
            company = data.pop("company", None)
            role = data.pop("role", None)
            if not company or not role:
                raise ValidationError("every row needs a 'company' and a 'role'")
            created.append(self.add(company, role, today=today, **data))
        return created
