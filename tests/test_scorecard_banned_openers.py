"""Unit tests for kitesforu_qa.scorecard.banned_openers — the hook-axis regex mirror of
narration_rules.yaml's BANNED WIND-UP OPENERS + subordinate-clause-opener rule."""
from __future__ import annotations

from kitesforu_qa.scorecard.banned_openers import banned_opener_hit


def test_clean_self_contained_hook_is_not_flagged() -> None:
    assert banned_opener_hit("Your brain deletes your dreams on purpose.") is None


def test_windup_opener_imagine_this_is_flagged() -> None:
    assert banned_opener_hit("Imagine this: you wake up and it's gone.") == "imagine this"


def test_windup_opener_have_you_ever_is_flagged() -> None:
    assert banned_opener_hit("Have you ever wondered why?") == "have you ever"


def test_windup_opener_in_this_video_is_flagged() -> None:
    assert banned_opener_hit("In this video we explore memory.") == "in this video"


def test_subordinate_clause_despite_is_flagged() -> None:
    assert banned_opener_hit("Despite the billion-dollar industry.") == "despite"


def test_subordinate_clause_even_though_two_word_phrase_is_flagged() -> None:
    assert banned_opener_hit("Even though everyone said no, she did it.") == "even though"


def test_banned_phrase_mid_sentence_is_not_flagged() -> None:
    # "while" mid-hook is fine — only a LEADING subordinate clause is banned.
    assert banned_opener_hit("Everything changed while everyone watched.") is None


def test_case_and_punctuation_insensitive_matching() -> None:
    assert banned_opener_hit('"IMAGINE THIS," she said.') == "imagine this"


def test_empty_hook_is_not_flagged() -> None:
    assert banned_opener_hit("") is None
    assert banned_opener_hit("   ") is None


def test_curly_apostrophe_normalized() -> None:
    assert banned_opener_hit("We’ve all been there before.") == "we've all"
