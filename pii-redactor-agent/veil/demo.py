"""The built-in sample bundle.

Every value here is synthetic. The card numbers are valid **Luhn** numbers
(because that is the only way to exercise the validator) but they belong to the
reserved test range, and the IBANs use country codes with deliberately invalid
account bodies where possible. Nothing here identifies a real person or account.

The documents are written to exercise every detector, the overlap resolver (an
email inside a URL query string, a phone-like run inside a date-shaped string),
and all six actions, so `veil demo` is a real self-test rather than a toy.
"""

from __future__ import annotations

import os
from typing import Dict, List

#: Synthetic values, exported so tests can assert on them without duplicating
#: the literals and letting the two drift apart.
SAMPLE_CARD = "4111 1111 1111 1111"          # Luhn-valid, reserved test range
SAMPLE_CARD_2 = "4242 4242 4242 4242"       # Luhn-valid, universally published test PAN
SAMPLE_IBAN = "GB82 WEST 1234 5698 7654 32"  # the canonical mod-97 test IBAN
SAMPLE_SSN = "214-55-9876"
SAMPLE_CNIC = "35202-1234567-1"
SAMPLE_EMAIL = "alice.chen@brightpath-consulting.com"
SAMPLE_EMAIL_2 = "ops@brightpath-consulting.com"
SAMPLE_PHONE = "+92 300 1234567"
SAMPLE_PHONE_2 = "(415) 555-0132"
SAMPLE_IPV4 = "192.168.14.22"
SAMPLE_IPV6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
SAMPLE_MAC = "00:1B:44:11:3A:B7"
SAMPLE_URL = "https://internal.brightpath-consulting.com/admin/audit?token=abc123"
#: Every credential fixture below spells out its own syntheticness in the
#: value itself. A random-looking string in a public repo is a liability even
#: when it is fake: GitHub's secret scanning cannot tell a fixture from a leak,
#: and neither can a reader. These match the detectors' shapes without ever
#: being mistakable for a real credential. `tools/fixture_guard.py` enforces it.
SAMPLE_AWS_KEY = "AKIASYNTHETICKEY0000"
SAMPLE_GH_TOKEN = "ghp_SYNTHETICPLACEHOLDER000000000000000000"
SAMPLE_PASSWORD = "testkey-ZephyrCove-7193"
SAMPLE_API_KEY = "sk_test_SYNTHETICPLACEHOLDER00"
SAMPLE_DOB = "1988-04-12"
SAMPLE_PERSON = "Dr. Nadia Rehman"


_FIRST_DOCUMENT = f"""From: {SAMPLE_EMAIL}
To: {SAMPLE_EMAIL_2}
Subject: Q3 vendor reconciliation \u2014 please review before Friday

Hi,

Attaching the reconciliation notes from Tuesday's call. Some of this is
sensitive so please keep it inside the finance group.

Primary contact for the vendor is {SAMPLE_PERSON}, reachable on
{SAMPLE_PHONE} during business hours or {SAMPLE_PHONE_2} after 6pm.
Her date of birth is on file as {SAMPLE_DOB} (needed for the KYC form).

Card on file (do NOT forward this email):
    {SAMPLE_CARD}
Backup corporate card: {SAMPLE_CARD_2}

Wire details for the refund:
    IBAN {SAMPLE_IBAN}

The audit trail lives at {SAMPLE_URL} \u2014 the query string carries a
short-lived token, so treat the whole link as secret.

Internal notes:
    taxpayer id {SAMPLE_CNIC}
    SSN for the US entity: {SAMPLE_SSN}
    app server {SAMPLE_IPV4}
    ipv6 {SAMPLE_IPV6}
    nic {SAMPLE_MAC}

Ops handover (rotate these after the migration):
    aws_access_key_id = {SAMPLE_AWS_KEY}
    github_token = {SAMPLE_GH_TOKEN}
    stripe_key = {SAMPLE_API_KEY}
    db_password = {SAMPLE_PASSWORD}

Thanks,
Alice
"""

_CONFIG_DOCUMENT = f"""# deployment.env \u2014 staging
# Some of these are placeholders and must NOT be reported as secrets.

AWS_ACCESS_KEY_ID={SAMPLE_AWS_KEY}
GITHUB_TOKEN={SAMPLE_GH_TOKEN}
STRIPE_SECRET_KEY={SAMPLE_API_KEY}
DB_PASSWORD={SAMPLE_PASSWORD}
API_KEY=changeme
OTHER_API_KEY=your_api_key
TOKEN=REPLACE_ME

# published, intentionally public support address
SUPPORT_EMAIL=support@brightpath-consulting.com

# internal endpoints
METRICS_HOST={SAMPLE_IPV4}
GATEWAY={SAMPLE_IPV6}
CALLBACK=https://hooks.brightpath-consulting.com/v1/callback

# release schedule \u2014 a bare date must not be read as a phone number
RELEASE_DATE=2026-09-18
BUILD_STAMP=2026-09-18T09:00:00Z
"""

_LOG_DOCUMENT = f"""2026-09-18T04:12:07Z INFO  auth   login ok user={SAMPLE_EMAIL} ip={SAMPLE_IPV4}
2026-09-18T04:12:09Z DEBUG http   GET {SAMPLE_URL} -> 200 in 42ms
2026-09-18T04:13:01Z WARN  pay    card auth failed for {SAMPLE_CARD}
2026-09-18T04:13:02Z INFO  pay    falling back to {SAMPLE_CARD_2}
2026-09-18T04:14:55Z ERROR mail   smtp refused recipient {SAMPLE_EMAIL_2}
2026-09-18T04:15:00Z INFO  notify paged on-call at {SAMPLE_PHONE}
2026-09-18T04:15:31Z DEBUG net    peer {SAMPLE_MAC} on {SAMPLE_IPV6}
2026-09-18T04:16:02Z FATAL conf   refusing to start: db_password={SAMPLE_PASSWORD}
2026-09-18T04:16:40Z INFO  conf   using stripe_key={SAMPLE_API_KEY}
"""


DOCUMENTS: Dict[str, str] = {
    "vendor_email.txt": _FIRST_DOCUMENT,
    "deployment.env": _CONFIG_DOCUMENT,
    "app.log": _LOG_DOCUMENT,
}

#: Policies shipped with the demo, showing three different postures.
POLICIES: Dict[str, str] = {
    "strict.yaml": """# Strict: remove or obliterate everything.
name: strict
entities: ALL
actions:
  SECRET: remove
  CREDIT_CARD: redact
  SSN: redact
  IBAN: redact
  NATIONAL_ID: redact
  EMAIL: redact
  PHONE: redact
  DATE_OF_BIRTH: redact
  IPV4: redact
  IPV6: redact
  MAC: redact
  URL: redact
  PERSON: redact
keep_first: 0
keep_last: 0
""",
    "pseudonymize.yaml": """# Pseudonymize: reversible tokens, so the document can be restored exactly.
# Every entity is tokenized, SECRET included \u2014 leaving credentials on a
# non-reversible action here would make the policy's whole promise false,
# since you could never get the original file back.
name: pseudonymize
entities: ALL
actions:
  SECRET: tokenize
  CREDIT_CARD: tokenize
  SSN: tokenize
  IBAN: tokenize
  NATIONAL_ID: tokenize
  EMAIL: tokenize
  PHONE: tokenize
  DATE_OF_BIRTH: tokenize
  IPV4: tokenize
  IPV6: tokenize
  MAC: tokenize
  URL: tokenize
  PERSON: tokenize
token_prefix: VEIL
""",
    "shareable.yaml": """# Shareable: masks contact details but keeps them readable, and
# leaves the published support address alone.
name: shareable
entities: ALL
actions:
  SECRET: remove
  CREDIT_CARD: mask
  SSN: mask
  IBAN: mask
  NATIONAL_ID: mask
  EMAIL: mask
  PHONE: mask
  DATE_OF_BIRTH: mask
  IPV4: mask
  IPV6: mask
  MAC: mask
  URL: redact
  PERSON: mask
keep_first: 2
keep_last: 4
allowlist:
  - support@brightpath-consulting.com
""",
}

DEFAULT_POLICY = "shareable.yaml"


def sample_paths(directory: str) -> Dict[str, str]:
    """Absolute paths the demo would write to, without writing them."""
    return {name: os.path.join(directory, name) for name in DOCUMENTS}


def write_samples(directory: str, overwrite: bool = False) -> Dict[str, str]:
    """Materialise the sample bundle. Returns name -> absolute path.

    Refuses to clobber an existing file unless asked, so running the demo in a
    working directory cannot destroy something the user cares about.
    """
    os.makedirs(directory, exist_ok=True)
    written: Dict[str, str] = {}
    for name, body in list(DOCUMENTS.items()) + list(POLICIES.items()):
        path = os.path.join(directory, name)
        if os.path.exists(path) and not overwrite:
            written[name] = path
            continue
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        written[name] = path
    return written


def document_summary() -> List[dict]:
    """What is in the bundle, for the docs and the web HUD."""
    return [
        {
            "name": "vendor_email.txt",
            "kind": "Email",
            "why": "Exercises every detector at once, plus the overlap resolver.",
        },
        {
            "name": "deployment.env",
            "kind": "Config",
            "why": "Shows placeholder suppression and the bare-date false-positive guard.",
        },
        {
            "name": "app.log",
            "kind": "Log",
            "why": "The realistic case: secrets and PII scattered through structured lines.",
        },
    ]


def policy_summary() -> List[dict]:
    return [
        {
            "name": "strict.yaml",
            "posture": "Remove or obliterate",
            "reversible": False,
            "when": "Handing a document to someone outside the organisation.",
        },
        {
            "name": "pseudonymize.yaml",
            "posture": "Reversible tokens",
            "reversible": True,
            "when": "Working with the data yourself but not in the clear. Every "
                    "entity is tokenized, so the original can be restored exactly.",
        },
        {
            "name": "shareable.yaml",
            "posture": "Mask, keep readable",
            "reversible": False,
            "when": "Sharing internally, where context still matters.",
        },
    ]
