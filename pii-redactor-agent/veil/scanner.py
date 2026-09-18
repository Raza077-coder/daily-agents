"""The scanner: detection, resolution, application, verification.

This is the module the CLI and the API both call, so there is exactly one code
path that produces a redaction. Four steps:

1. **Detect.** Every enabled detector proposes spans (see `detectors`).
2. **Resolve.** Overlapping proposals are reduced to a single winner. Detectors
   genuinely disagree \u2014 an email contains a URL-shaped host, a phone number can
   sit inside a URL's query string \u2014 so the rule has to be explicit rather than
   "whichever ran last".
3. **Apply.** Each distinct value is transformed once (see `actions`), then the
   document is rebuilt from the resolved spans in one pass.
4. **Verify.** The *output* is scanned again and must come back clean. This is
   the part most redaction tools skip, and it is the part that catches the
   failure that matters: a span the replacement text accidentally reintroduced,
   or a value that was reported but never actually removed.

Overlap resolution, in order:

- a longer span beats a shorter one (the whole email beats the bare host);
- a higher-confidence span beats a lower one;
- a higher-risk entity beats a lower-risk one (a card beats a phone);
- on a complete tie, entity name then start offset decide, so the result is
  deterministic rather than dependent on detector iteration order.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import actions as actions_module
from . import detectors, policy as policy_module
from .models import (
    ACTION_KEEP,
    ALL_ENTITIES,
    ENTITY_LABELS,
    OPT_IN_ENTITIES,
    RISK_WEIGHTS,
    Finding,
    RedactionResult,
    RiskSummary,
    Span,
    Verification,
)
from .vault import Vault

#: Score thresholds for the risk bands. Chosen so that a single leaked
#: credential or payment card (weight 9-10) lands in HIGH on its own, while a
#: document of ordinary contact details stays in MODERATE until it accumulates.
#: The first band whose threshold the score reaches wins, so the list must stay
#: in descending order.
RISK_BANDS: Sequence[Tuple[int, str]] = (
    (44, "CRITICAL"),
    (9, "HIGH"),
    (3, "MODERATE"),
    (1, "LOW"),
    (0, "NONE"),
)

#: The same bands the other way up, so a caller can compare severities without
#: re-deriving the order. Index comparisons are meaningful: a higher index is a
#: more severe level.
RISK_LEVELS: Sequence[str] = tuple(name for _, name in reversed(RISK_BANDS))


def _span_sort_key(span: Span) -> tuple:
    """Higher is better. The first element must be negated by the caller."""
    return (
        span.length,
        span.confidence,
        RISK_WEIGHTS.get(span.entity, 1),
        span.entity,
    )


def resolve_spans(spans: Iterable[Span]) -> List[Span]:
    """Reduce overlapping proposals to a non-overlapping, sorted winner list."""
    ordered = sorted(
        spans,
        key=lambda s: (
            -s.length,
            -s.confidence,
            -RISK_WEIGHTS.get(s.entity, 1),
            s.entity,
            s.start,
        ),
    )
    chosen: List[Span] = []
    for candidate in ordered:
        if any(
            candidate.start < existing.end and existing.start < candidate.end
            for existing in chosen
        ):
            continue
        chosen.append(candidate)
    chosen.sort(key=lambda s: (s.start, s.end))
    return chosen


def _apply_spans(text: str, spans: Sequence[Span], policy: policy_module.Policy,
                 tokenizer: Optional[actions_module.Tokenizer],
                 allow: Set[str]) -> Tuple[str, List[Finding]]:
    """Rebuild the document from the resolved spans, one action per value."""
    replacements: Dict[Tuple[str, str], str] = {}
    action_cache: Dict[Tuple[str, str], str] = {}
    findings_by_value: Dict[Tuple[str, str], Finding] = {}
    order: List[Tuple[str, str]] = []

    for span in spans:
        key = (span.entity, span.value)
        if key not in replacements:
            allowed = span.value in allow
            if allowed:
                action = ACTION_KEEP
            else:
                action = policy.action_for(span.entity)
            action_cache[key] = action
            replacements[key] = actions_module.apply_action(
                action,
                span.value,
                span.entity,
                tokenizer=tokenizer,
                keep_first=policy.keep_first,
                keep_last=policy.keep_last,
                mask_char=policy.mask_char,
                salt=policy.hash_salt,
            )
            findings_by_value[key] = Finding(
                entity=span.entity,
                value=span.value,
                action=action,
                replacement=replacements[key],
                count=0,
                detector=span.detector,
                confidence=span.confidence,
                note=span.note,
                preserved=(action == actions_module.ACTION_KEEP),
            )
            order.append(key)
        finding = findings_by_value[key]
        finding.count += 1
        finding.offsets.append((span.start, span.end))

    pieces: List[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span.start])
        pieces.append(replacements[(span.entity, span.value)])
        cursor = span.end
    pieces.append(text[cursor:])

    findings = [findings_by_value[key] for key in order]
    findings.sort(key=lambda f: (-f.risk_weight, f.entity, f.offsets[0][0]))
    return "".join(pieces), findings


def score_risk(findings: Sequence[Finding]) -> RiskSummary:
    """Weighted sensitivity score.

    Each distinct value contributes ``risk_weight x (1 + log2(count))``. The
    logarithm is deliberate: the first occurrence of a card number is the
    damaging one, and a value appearing forty times instead of four is not ten
    times worse. Occurrence counts still matter, just not linearly.
    """
    total = 0.0
    by_entity: Dict[str, int] = {}
    occurrences = 0
    for finding in findings:
        if finding.preserved:
            continue
        contribution = finding.risk_weight * (1 + math.log2(max(finding.count, 1)))
        total += contribution
        by_entity[finding.entity] = by_entity.get(finding.entity, 0) + finding.count
        occurrences += finding.count

    score = int(round(total))
    level = "NONE"
    for threshold, name in RISK_BANDS:
        if score >= threshold:
            level = name
            break

    return RiskSummary(
        score=score,
        level=level,
        distinct_findings=len([f for f in findings if not f.preserved]),
        total_occurrences=occurrences,
        by_entity=dict(sorted(by_entity.items())),
    )


def verify(redacted: str, policy: policy_module.Policy,
           preserved_values: Iterable[str] = ()) -> Verification:
    """Re-scan the redacted text and report anything that survived.

    A finding counts as a residual when the *same entity* is still detectable in
    the output. Values the policy chose to keep are reported separately, because
    "deliberately kept" and "failed to remove" are very different results and
    collapsing them would make a correct run look broken.

    Tokens this tool generated are excluded outright. A token such as
    ``VEIL_SECRET_004`` is a *replacement*, so it can sit on the left of an
    assignment and be read back as an assignment-shaped secret. Reporting our own
    placeholder as a survivor would be a false alarm, and false alarms are how a
    verification step gets ignored \u2014 so the token pattern is derived from the
    policy's own ``token_prefix`` and filtered out.

    One honest caveat about what verification proves. It re-scans the output with
    the same detectors, so it catches a value the policy reported but did not
    actually change, a replacement that re-introduces a detectable value, and a
    span the rebuild dropped. It cannot catch a miss the detectors never made in
    the first place \u2014 if a detector never fired on the input, re-scanning the
    output will not fire either. Verification proves the transform was *applied*;
    correctness of *detection* is a property of the detectors, and that is what
    the test suite is for.
    """
    allow = {v for v in preserved_values if v}
    # Recognise the tool's own tokens so a token is never mistaken for a
    # residual. The alternation is built from the entity catalogue rather than
    # a loose `[A-Z_]+`, because entity names legitimately contain digits
    # (IPV4, IPV6) and a digits-free pattern would silently miss those tokens
    # and report a clean redaction as dirty.
    token_entities = "|".join(sorted(set(ALL_ENTITIES) | {"EXACT_MATCH"}))
    token_pattern = re.compile(
        r"^" + re.escape(policy.token_prefix) + r"_(" + token_entities + r")_\d+$"
    )
    rescan = detectors.detect_all(
        redacted,
        enabled=policy.entities,
        extra_names=policy.names,
    )
    residuals: List[dict] = []
    preserved: List[dict] = []
    for span in rescan:
        if token_pattern.match(span.value):
            continue
        record = {
            "entity": span.entity,
            "value": span.value,
            "start": span.start,
            "detector": span.detector,
        }
        if span.value in allow:
            preserved.append(record)
        else:
            residuals.append(record)

    status = "CLEAN" if not residuals else "RESIDUAL_FOUND"
    return Verification(
        status=status,
        checked_entities=list(policy.entities),
        residuals=residuals,
        preserved=preserved,
    )


class Scanner:
    """Stateless scanner. Construct once, call many times."""

    def __init__(self, policy: Optional[policy_module.Policy] = None) -> None:
        self.policy = policy or policy_module.Policy()

    def detect(self, text: str) -> List[Span]:
        """Resolved spans for the text, honouring denylist literals."""
        if not isinstance(text, str):
            raise TypeError(f"text must be a string, got {type(text).__name__}")

        spans = detectors.detect_all(
            text,
            enabled=self.policy.entities,
            extra_names=self.policy.names,
        )
        spans.extend(self._denylist_spans(text))
        return resolve_spans(spans)

    def _denylist_spans(self, text: str) -> List[Span]:
        """Force exact literals to be redacted, whatever the detectors think."""
        out: List[Span] = []
        for literal in self.policy.denylist:
            if not literal:
                continue
            haystack = text if self.policy.case_sensitive_denylist else text.lower()
            needle = literal if self.policy.case_sensitive_denylist else literal.lower()
            start = haystack.find(needle)
            while start != -1:
                out.append(Span(
                    start=start,
                    end=start + len(needle),
                    entity="EXACT_MATCH",
                    value=text[start:start + len(needle)],
                    detector="denylist",
                    confidence=1.0,
                    note="listed verbatim in the policy denylist",
                ))
                start = haystack.find(needle, start + len(needle))
        return out

    def scan(self, text: str) -> RedactionResult:
        """Detect and report without producing redacted output."""
        findings = self._collect(text)
        return RedactionResult(
            text=text,
            findings=findings,
            risk=score_risk(findings),
            policy_name=self.policy.name,
        )

    def redact(self, text: str, *, verify_output: bool = True) -> RedactionResult:
        """Detect, transform, and (by default) verify the result."""
        tokenizer = actions_module.Tokenizer(prefix=self.policy.token_prefix)
        spans = self.detect(text)
        redacted, findings = _apply_spans(
            text, spans, self.policy, tokenizer, self.policy.allow_set()
        )
        result = RedactionResult(
            text=redacted,
            findings=findings,
            risk=score_risk(findings),
            policy_name=self.policy.name,
            token_map=tokenizer.mapping,
            is_redacted=True,
        )
        if verify_output:
            result.verification = verify(redacted, self.policy, self.policy.allow_set())
        return result

    def _collect(self, text: str) -> List[Finding]:
        """Findings for scan-only mode: same grouping, no transformation."""
        spans = self.detect(text)
        grouped: Dict[Tuple[str, str], Finding] = {}
        order: List[Tuple[str, str]] = []
        for span in spans:
            key = (span.entity, span.value)
            if key not in grouped:
                allowed = span.value in self.policy.allow_set()
                action = ACTION_KEEP if allowed else self.policy.action_for(span.entity)
                grouped[key] = Finding(
                    entity=span.entity,
                    value=span.value,
                    action=action,
                    replacement=actions_module.apply_action(
                        action, span.value, span.entity,
                        tokenizer=None,
                        keep_first=self.policy.keep_first,
                        keep_last=self.policy.keep_last,
                        mask_char=self.policy.mask_char,
                        salt=self.policy.hash_salt,
                    ) if action != "tokenize" else "(assigned at redaction time)",
                    count=0,
                    detector=span.detector,
                    confidence=span.confidence,
                    note=span.note,
                    preserved=(action == ACTION_KEEP),
                )
                order.append(key)
            finding = grouped[key]
            finding.count += 1
            finding.offsets.append((span.start, span.end))

        findings = [grouped[key] for key in order]
        findings.sort(key=lambda f: (-f.risk_weight, f.entity, f.offsets[0][0]))
        return findings

    def detokenize(self, text: str, vault: Vault) -> str:
        """Restore tokenized values.

        Replaces longest tokens first, so `VEIL_EMAIL_001` cannot be partially
        consumed by a shorter overlapping token.
        """
        restored = text
        mapping = vault.to_mapping()
        for token in sorted(mapping, key=len, reverse=True):
            restored = restored.replace(token, mapping[token])
        return restored

    def entity_report(self) -> List[dict]:
        """Which entities this scanner will look for, and what it will do."""
        rows: List[dict] = []
        for entity in ALL_ENTITIES:
            enabled = self.policy.is_enabled(entity)
            rows.append({
                "entity": entity,
                "label": ENTITY_LABELS[entity],
                "enabled": enabled,
                "action": self.policy.action_for(entity) if enabled else None,
                "risk_weight": RISK_WEIGHTS[entity],
                "opt_in": entity in OPT_IN_ENTITIES,
            })
        return rows


def build_vault(result: RedactionResult, entities: Optional[Dict[str, str]] = None,
                created: str = "") -> Vault:
    """Turn a result's token map into a persistable vault."""
    return Vault.from_mapping(
        result.token_map, policy_name=result.policy_name,
        entities=entities, created=created,
    )
