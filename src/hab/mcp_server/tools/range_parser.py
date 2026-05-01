"""Parse standard poker range notation into concrete hand combos.

Examples:
  parse_range("AA")          → 6 combos (AcAd, AcAh, ..., AhAs)
  parse_range("AKs")         → 4 combos (AcKc, AdKd, AhKh, AsKs)
  parse_range("AKo")         → 12 combos (offsuit AK)
  parse_range("AK")          → 16 combos (s + o)
  parse_range("TT+")         → TT,JJ,QQ,KK,AA = 30 combos
  parse_range("88-66")       → 88,77,66 = 18 combos
  parse_range("A2s+")        → A2s,A3s,...,AKs = 48 combos
  parse_range("AA,KK,AKs")   → comma-separated union
  parse_range("any_pair")    → all 13 pocket pairs
"""
from __future__ import annotations

import itertools

RANK_ORDER = "23456789TJQKA"
SUITS = "cdhs"
RANDOM_RANGE_ALIASES = {"", "random", "any", "all", "*"}


def _rank_idx(r: str) -> int:
    return RANK_ORDER.index(r)


def _valid_rank(r: str) -> bool:
    return len(r) == 1 and r in RANK_ORDER


def _valid_kind(kind: str) -> bool:
    return kind in {"s", "o"}


def _all_pairs() -> list[str]:
    return [r + r for r in RANK_ORDER]


def _all_suited() -> list[str]:
    out = []
    for hi in RANK_ORDER:
        for lo in RANK_ORDER:
            if _rank_idx(hi) > _rank_idx(lo):
                out.append(f"{hi}{lo}s")
    return out


def _all_offsuit() -> list[str]:
    out = []
    for hi in RANK_ORDER:
        for lo in RANK_ORDER:
            if _rank_idx(hi) > _rank_idx(lo):
                out.append(f"{hi}{lo}o")
    return out


def hand_class_to_combos(class_str: str) -> list[tuple[str, str]]:
    """'AKs' -> [('Ac','Kc'), ('Ad','Kd'), ('Ah','Kh'), ('As','Ks')]
    'AA' -> 6 pairs of unordered pair combos
    """
    if len(class_str) == 2 and class_str[0] == class_str[1]:
        # pair
        r = class_str[0]
        if not _valid_rank(r):
            raise ValueError(f"unparseable hand class: {class_str}")
        return [(r + s1, r + s2) for s1, s2 in itertools.combinations(SUITS, 2)]
    if len(class_str) == 3:
        hi, lo, kind = class_str[0], class_str[1], class_str[2]
        if not (_valid_rank(hi) and _valid_rank(lo) and _valid_kind(kind)):
            raise ValueError(f"unparseable hand class: {class_str}")
        if hi == lo:
            raise ValueError(f"pairs cannot be suited/offsuit: {class_str}")
        if kind == "s":
            return [(hi + s, lo + s) for s in SUITS]
        if kind == "o":
            return [
                (hi + s1, lo + s2)
                for s1, s2 in itertools.product(SUITS, SUITS)
                if s1 != s2
            ]
    raise ValueError(f"unparseable hand class: {class_str}")


def _expand_token(token: str) -> list[str]:
    """Single token like 'TT+', '88-66', 'AKs', 'A5s-A2s', 'A2s+'."""
    token = token.strip()
    if not token:
        return []

    # Plus notation
    if token.endswith("+"):
        base = token[:-1]
        return _expand_plus(base)
    # Range notation A-B
    if "-" in token:
        a, b = token.split("-", 1)
        return _expand_dash(a.strip(), b.strip())
    # Plain class
    return [_normalize_class(token)]


def _normalize_class(token: str) -> str:
    """Normalize 'AK' -> 'AK' (means both suited and offsuit treated separately later)
    Actually: 'AK' alone is ambiguous; we treat it as both suited+offsuit combos.
    """
    if len(token) == 2:
        if not (_valid_rank(token[0]) and _valid_rank(token[1])):
            raise ValueError(f"bad class token: {token}")
        if token[0] == token[1]:
            return token  # pair
        # 'AK' without s/o suffix — return as-is, expanded by caller
        return token
    if len(token) == 3:
        if not (_valid_rank(token[0]) and _valid_rank(token[1]) and _valid_kind(token[2])):
            raise ValueError(f"bad class token: {token}")
        if token[0] == token[1]:
            raise ValueError(f"pairs cannot be suited/offsuit: {token}")
        return token
    raise ValueError(f"bad class token: {token}")


def _expand_plus(base: str) -> list[str]:
    """'TT+' -> [TT, JJ, QQ, KK, AA]
    'A2s+' -> [A2s, A3s, ..., AKs]
    'KJo+' -> [KJo, KQo]
    """
    if len(base) == 2 and base[0] == base[1]:
        # pair plus: TT+ → TT and higher
        if not _valid_rank(base[0]):
            raise ValueError(f"bad + token: {base}+")
        idx = _rank_idx(base[0])
        return [r + r for r in RANK_ORDER[idx:]]
    if len(base) == 3:
        hi, lo, kind = base[0], base[1], base[2]
        if not (_valid_rank(hi) and _valid_rank(lo) and _valid_kind(kind)):
            raise ValueError(f"bad + token: {base}+")
        if hi == lo:
            raise ValueError(f"bad + token: {base}+")
        # A2s+ → A2s, A3s, A4s, ..., AKs (i.e. lo card moves up but stays below hi)
        hi_idx = _rank_idx(hi)
        lo_idx = _rank_idx(lo)
        if lo_idx >= hi_idx:
            raise ValueError(f"bad + token: {base}+")
        return [
            f"{hi}{RANK_ORDER[i]}{kind}"
            for i in range(lo_idx, hi_idx)
        ]
    raise ValueError(f"bad + token: {base}+")


def _expand_dash(a: str, b: str) -> list[str]:
    """'88-66' -> [88, 77, 66]
    'A5s-A2s' -> [A5s, A4s, A3s, A2s]
    """
    if len(a) == 2 and len(b) == 2 and a[0] == a[1] and b[0] == b[1]:
        # pair range
        if not (_valid_rank(a[0]) and _valid_rank(b[0])):
            raise ValueError(f"bad dash range: {a}-{b}")
        ai = _rank_idx(a[0])
        bi = _rank_idx(b[0])
        lo, hi = sorted([ai, bi])
        return [r + r for r in RANK_ORDER[lo:hi + 1]]
    if len(a) == 3 and len(b) == 3 and a[0] == b[0] and a[2] == b[2]:
        # AxKs - AxJs style
        hi, kind = a[0], a[2]
        if not (_valid_rank(hi) and _valid_kind(kind) and _valid_rank(a[1]) and _valid_rank(b[1])):
            raise ValueError(f"bad dash range: {a}-{b}")
        if a[1] == hi or b[1] == hi:
            raise ValueError(f"bad dash range: {a}-{b}")
        a_lo = _rank_idx(a[1])
        b_lo = _rank_idx(b[1])
        lo, top = sorted([a_lo, b_lo])
        return [f"{hi}{RANK_ORDER[i]}{kind}" for i in range(lo, top + 1)]
    raise ValueError(f"bad dash range: {a}-{b}")


# Special preset names users can pass instead of strings
_NAMED_RANGES = {
    "any_pair": ",".join(_all_pairs()),
    "any_suited": ",".join(_all_suited()),
    "any_offsuit": ",".join(_all_offsuit()),
    "any_two": "any_pair,any_suited,any_offsuit",
    # === Heads-up SB opening range (~80% of hands; solver-derived ballpark) ===
    "HU_SB_open": (
        "22+,"
        "A2s+,K2s+,Q2s+,J2s+,T4s+,95s+,85s+,74s+,64s+,53s+,43s,32s,"
        "A2o+,K2o+,Q4o+,J6o+,T7o+,96o+,86o+,76o,65o"
    ),
    # === HU BB defense vs SB open: call ~65%, 3-bet ~10%, fold ~25% ===
    "HU_BB_3bet": (
        "TT+,AJs+,KQs,AKo"
    ),
    "HU_BB_call": (
        "22-99,A2s-ATs,K2s-KJs,Q4s-QJs,J6s-JTs,T6s-T9s,96s-98s,85s+,"
        "74s+,64s+,53s+,43s,32s,A2o-ATo,K6o-KJo,Q9o-QTo,J9o-JTo,T9o,98o"
    ),
    # === 6-max opening ranges (rough but reasonable) ===
    "6M_UTG_open": "77+,ATs+,KTs+,QTs+,JTs,T9s,98s,AQo+",
    "6M_HJ_open": "55+,A8s+,K9s+,QTs+,J9s+,T9s,98s,87s,AJo+,KQo",
    "6M_CO_open": (
        "22+,A2s+,K7s+,Q9s+,J9s+,T8s+,97s+,86s+,76s,65s,54s,"
        "ATo+,KTo+,QTo+,JTo"
    ),
    "6M_BTN_open": (
        "22+,A2s+,K2s+,Q5s+,J7s+,T7s+,97s+,86s+,75s+,64s+,53s+,"
        "A2o+,K8o+,Q9o+,J9o+,T9o,98o"
    ),
    "6M_SB_open": (
        "22+,A2s+,K2s+,Q5s+,J7s+,T7s+,97s+,86s+,75s+,64s+,54s,"
        "A2o+,K7o+,Q9o+,J9o+,T9o"
    ),
    # === Common defensive / villain ranges ===
    "tight":  "TT+,AJs+,KQs,AQo+",        # ~5% range
    "loose":  "22+,A2s+,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,65s,A8o+,KJo+,QJo,JTo",  # ~30%
    "value_only": "TT+,AKs,AKo",          # ~3% — what they barrel for value
    "polarized_river_bet": "TT+,AKs,AKo,72o,T9s,87s,76s,65s",  # value + bluffs
}


def is_random_range(spec: str) -> bool:
    """Whether the spec intentionally means an unrestricted random range."""
    return (spec or "").strip().lower() in RANDOM_RANGE_ALIASES


def parse_range(spec: str, *, strict: bool = False) -> list[str]:
    """Parse a range spec into a list of unique hand classes (e.g. ['AA', 'AKs']).

    Accepts:
      - Named presets: 'HU_SB_open', 'tight', 'any_pair', 'random' (returns [])
      - Comma-separated tokens: 'AA,KK,AKs'
      - Plus notation: 'TT+', 'A2s+'
      - Range notation: '88-66', 'A5s-A2s'
      - Composite: 'TT+,AJs+,KQo,A5s-A2s'

    Returns empty list for 'random' (caller should treat as full deck).
    """
    spec = (spec or "").strip()
    if is_random_range(spec):
        return []
    # Named preset substitution (recursive)
    if spec in _NAMED_RANGES:
        return parse_range(_NAMED_RANGES[spec], strict=strict)

    out: list[str] = []
    errors: list[str] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        # Expand named preset inline
        if token in _NAMED_RANGES:
            out.extend(parse_range(_NAMED_RANGES[token], strict=strict))
            continue
        try:
            classes = _expand_token(token)
        except ValueError:
            errors.append(token)
            continue
        for c in classes:
            if len(c) == 2 and c[0] != c[1]:
                # Bare 'AK' — expand to AKs + AKo
                out.append(c + "s")
                out.append(c + "o")
            else:
                out.append(c)
    if strict and errors:
        raise ValueError(f"invalid range token(s): {', '.join(errors)}")
    # Deduplicate while preserving order
    seen = set()
    result = []
    for h in out:
        if h not in seen:
            seen.add(h)
            result.append(h)
    return result


def range_to_combos(spec: str, *, strict: bool = False) -> list[tuple[str, str]]:
    """Expand a range spec into all concrete (card1, card2) combos."""
    classes = parse_range(spec, strict=strict)
    combos: list[tuple[str, str]] = []
    errors: list[str] = []
    for cls in classes:
        try:
            combos.extend(hand_class_to_combos(cls))
        except ValueError:
            errors.append(cls)
            continue
    if strict and errors:
        raise ValueError(f"invalid hand class(es): {', '.join(errors)}")
    return combos


def list_named_ranges() -> list[str]:
    """All preset names available."""
    return sorted(_NAMED_RANGES.keys())


def named_range(name: str) -> str | None:
    """Return the spec string for a named range (or None)."""
    return _NAMED_RANGES.get(name)


def range_density(spec: str) -> float:
    """Fraction of all 1326 starting hands this range covers."""
    return len(range_to_combos(spec)) / 1326.0
