from app.search.tokenize import (
    TOKEN_LIMIT,
    TRIGRAM_LIMIT,
    build,
    tokenize,
    trigrams,
)


def test_identifier_keeps_parts_and_whole():
    assert tokenize("Checkout.Session.Completed") == [
        "checkout",
        "session",
        "completed",
        "checkout.session.completed",
    ]


def test_sentence_does_not_keep_the_whole_string():
    assert tokenize("Payment was declined") == ["payment", "was", "declined"]


def test_single_word_yields_one_token():
    assert tokenize("checkout") == ["checkout"]


def test_empty_and_punctuation_only():
    assert tokenize("") == []
    assert tokenize("  --- ") == []


def test_trigrams_are_padded():
    assert trigrams("abc") == [" ab", "abc", "bc "]


def test_trigrams_skip_short_tokens():
    assert trigrams("ab") == []


def test_typo_shares_most_trigrams_with_the_real_word():
    shared = set(trigrams("chekout")) & set(trigrams("checkout"))
    assert len(shared) / len(trigrams("chekout")) >= 0.4


def test_build_indexes_headers_query_and_body():
    result = build(
        "stripe",
        "checkout.session.completed",
        {"user-agent": "Stripe/1.0"},
        {"attempt": "2"},
        {"data": {"amount": 4200, "currency": "usd"}},
    )
    for expected in ("stripe", "checkout", "usd", "4200", "currency", "attempt"):
        assert expected in result["tokens"]


def test_build_excludes_secrets():
    result = build(
        "stripe",
        None,
        {"authorization": "Bearer supersecret", "stripe-signature": "t=1,v1=deadbeef"},
        {},
        {"api_key": "sk_live_leaked", "safe": "visible"},
    )
    joined = " ".join(result["tokens"])
    for secret in ("supersecret", "deadbeef", "sk_live_leaked"):
        assert secret not in joined
    assert "visible" in result["tokens"]


def test_booleans_are_not_indexed():
    result = build("e", None, {}, {}, {"paid": True, "amount": 10})
    assert "true" not in result["tokens"]
    assert "10" in result["tokens"]


def test_arrays_are_indexed():
    result = build("e", None, {}, {}, {"tags": ["alpha", {"nested": "beta"}]})
    assert {"alpha", "beta", "nested"} <= set(result["tokens"])


def test_terms_are_deduplicated():
    result = build("dup", None, {}, {}, {"a": "dup", "b": "dup"})
    assert result["tokens"].count("dup") == 1


def test_arrays_are_capped():
    body = {f"key{i}": f"value{i}" for i in range(2000)}
    result = build("e", None, {}, {}, body)
    assert len(result["tokens"]) <= TOKEN_LIMIT
    assert len(result["trigrams"]) <= TRIGRAM_LIMIT
