"""Tests for the poker range parser."""
from hab.mcp_server.tools.range_parser import (
    hand_class_to_combos,
    list_named_ranges,
    parse_range,
    range_density,
    range_to_combos,
)


def test_pair_combos():
    combos = hand_class_to_combos("AA")
    assert len(combos) == 6  # C(4,2) = 6 pair combos
    assert all(c[0][0] == "A" and c[1][0] == "A" for c in combos)


def test_suited_combos():
    combos = hand_class_to_combos("AKs")
    assert len(combos) == 4
    # All same suit
    assert all(c[0][1] == c[1][1] for c in combos)


def test_offsuit_combos():
    combos = hand_class_to_combos("AKo")
    assert len(combos) == 12  # 4*3 ordered, but matching pattern


def test_total_combos_AKany():
    """AK combined = AKs + AKo = 4 + 12 = 16."""
    s = len(hand_class_to_combos("AKs"))
    o = len(hand_class_to_combos("AKo"))
    assert s + o == 16


def test_parse_pair_plus():
    classes = parse_range("TT+")
    assert classes == ["TT", "JJ", "QQ", "KK", "AA"]


def test_parse_pair_dash():
    classes = parse_range("88-66")
    assert classes == ["66", "77", "88"] or classes == ["88", "77", "66"]
    assert set(classes) == {"66", "77", "88"}


def test_parse_axs_plus():
    classes = parse_range("A2s+")
    # A2s, A3s, A4s, ..., AKs (12 hands)
    assert len(classes) == 12
    assert "A2s" in classes
    assert "AKs" in classes


def test_parse_csv():
    classes = parse_range("AA,KK,AKs")
    assert classes == ["AA", "KK", "AKs"]


def test_parse_random():
    assert parse_range("random") == []
    assert parse_range("") == []


def test_parse_named_preset():
    classes = parse_range("HU_SB_open")
    # HU SB open is wide (~85%)
    assert "AA" in classes
    assert "22" in classes
    assert "AKs" in classes
    assert range_density("HU_SB_open") > 0.6  # solver-derived ~65-80%


def test_parse_named_tight():
    density = range_density("tight")
    assert 0.02 < density < 0.10  # ~5%


def test_parse_any_pair():
    combos = range_to_combos("any_pair")
    assert len(combos) == 13 * 6  # 13 pairs × 6 combos each


def test_parse_any_two():
    combos = range_to_combos("any_two")
    assert len(combos) == 1326  # full deck of starting hands


def test_bare_AK_expands_both():
    classes = parse_range("AK")
    assert "AKs" in classes
    assert "AKo" in classes


def test_list_named_ranges_includes_HU():
    names = list_named_ranges()
    assert "HU_SB_open" in names
    assert "HU_BB_3bet" in names
    assert "6M_UTG_open" in names
