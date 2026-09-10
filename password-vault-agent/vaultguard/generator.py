"""VAULTGUARD deterministic-format secret generation.

Three generators, all seeded from :mod:`secrets` (CSPRNG):

``generate_password``    character-class aware random string with a guarantee
                         that every requested class appears in the output.
``generate_passphrase``  EFF-style word list joining for memorable secrets.
``generate_pin``         numeric PIN of a given length.

Every generator accepts an optional ``rng`` so tests can inject
``random.Random(seed)`` for reproducible output.
"""

from __future__ import annotations

import random
import secrets
import string
from typing import List, Optional, Sequence

LOWER = string.ascii_lowercase
UPPER = string.ascii_uppercase
DIGITS = string.digits
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"

AMBIGUOUS = set("Il1O0oB8S5Z2G6")
WORDS_MIN = 3
ABSOLUTE_MIN_LENGTH = 8

DEFAULT_WORDLIST: Sequence[str] = (
    "amber", "anchor", "atlas", "aurora", "basalt", "beacon", "bison", "bramble",
    "breeze", "cactus", "canyon", "cedar", "cinder", "cobalt", "comet", "copper",
    "coral", "crater", "crystal", "cypress", "dahlia", "delta", "dune", "ember",
    "falcon", "fathom", "fern", "fjord", "flint", "fossil", "galaxy", "garnet",
    "geyser", "glacier", "granite", "harbor", "hazel", "heron", "hollow", "indigo",
    "ivory", "jasmine", "juniper", "kestrel", "lagoon", "lantern", "larch", "lichen",
    "lotus", "lunar", "magnet", "mango", "marble", "meadow", "mesa", "meteor",
    "mosaic", "nectar", "nimbus", "nordic", "obsidian", "onyx", "orchid", "osprey",
    "oxide", "pebble", "penguin", "pepper", "phoenix", "pine", "plasma", "pollen",
    "prairie", "prism", "quartz", "quill", "raven", "reef", "ridge", "river",
    "saffron", "sage", "sapphire", "savanna", "sequoia", "shadow", "sierra", "silver",
    "solstice", "sparrow", "spruce", "stellar", "summit", "sunset", "talon", "tempo",
    "thistle", "thunder", "tundra", "umber", "valley", "velvet", "vertex", "violet",
    "walnut", "willow", "winter", "zenith", "zephyr", "zircon",
)


class GeneratorError(ValueError):
    """Raised when generation parameters are impossible to satisfy."""


def _coerce_rng(rng: Optional[random.Random]):
    """Return an RNG exposing ``choice``/``randbelow``/``sample``."""
    return rng if rng is not None else secrets.SystemRandom()


def _randbelow(rng, n: int) -> int:
    if hasattr(rng, "randbelow"):
        return rng.randbelow(n)
    return rng.randrange(n)


def _choice(rng, seq):
    return seq[_randbelow(rng, len(seq))]


def generate_password(
    length: int = 20,
    *,
    use_lower: bool = True,
    use_upper: bool = True,
    use_digits: bool = True,
    use_symbols: bool = True,
    avoid_ambiguous: bool = False,
    exclude: str = "",
    require_each_class: bool = True,
    rng: Optional[random.Random] = None,
) -> str:
    """Generate a random password guaranteeing class coverage."""
    if length < ABSOLUTE_MIN_LENGTH:
        raise GeneratorError(f"length must be at least {ABSOLUTE_MIN_LENGTH}")

    rng = _coerce_rng(rng)
    excluded = set(exclude or "")

    pools: List[str] = []
    for enabled, pool in (
        (use_lower, LOWER),
        (use_upper, UPPER),
        (use_digits, DIGITS),
        (use_symbols, SYMBOLS),
    ):
        if not enabled:
            continue
        chars = [c for c in pool if c not in excluded]
        if avoid_ambiguous:
            chars = [c for c in chars if c not in AMBIGUOUS]
        if chars:
            pools.append("".join(chars))

    if not pools:
        raise GeneratorError("no character classes selected")

    if require_each_class and length < len(pools):
        raise GeneratorError(
            f"length {length} is too short to include all {len(pools)} required classes"
        )

    combined = "".join(pools)
    out = [_choice(rng, pool) for pool in pools] if require_each_class else []
    while len(out) < length:
        out.append(_choice(rng, combined))
    # Fisher-Yates with a CSPRNG so the guarantee does not leak position order
    for i in range(len(out) - 1, 0, -1):
        j = _randbelow(rng, i + 1)
        out[i], out[j] = out[j], out[i]
    return "".join(out[:length])


def generate_passphrase(
    words: int = 4,
    *,
    separator: str = "-",
    capitalize: bool = False,
    add_number: bool = False,
    wordlist: Optional[Sequence[str]] = None,
    rng: Optional[random.Random] = None,
) -> str:
    """Generate a memorable multi-word passphrase."""
    if words < WORDS_MIN:
        raise GeneratorError(f"use at least {WORDS_MIN} words")
    rng = _coerce_rng(rng)
    pool = list(wordlist or DEFAULT_WORDLIST)
    if len(pool) < 8:
        raise GeneratorError("wordlist must contain at least 8 distinct words")

    chosen = [_choice(rng, pool) for _ in range(words)]
    if capitalize:
        chosen = [w.capitalize() for w in chosen]
    phrase = separator.join(chosen)
    if add_number:
        phrase = f"{phrase}{separator}{_randbelow(rng, 9000) + 1000}"
    return phrase


def generate_pin(length: int = 6, *, rng: Optional[random.Random] = None) -> str:
    """Generate a numeric PIN."""
    if length < 4:
        raise GeneratorError("PIN length must be at least 4")
    if length > 12:
        raise GeneratorError("PIN length above 12 adds little entropy, cap it at 12")
    rng = _coerce_rng(rng)
    first = str(_randbelow(rng, 9) + 1)
    rest = "".join(str(_randbelow(rng, 10)) for _ in range(length - 1))
    return first + rest


def suggest_for(
    length: int = 20,
    *,
    use_symbols: bool = True,
    avoid_ambiguous: bool = False,
    rng: Optional[random.Random] = None,
) -> str:
    """Convenience wrapper used by the engine's ``suggest`` command."""
    return generate_password(
        length,
        use_symbols=use_symbols,
        avoid_ambiguous=avoid_ambiguous,
        rng=rng,
    )
