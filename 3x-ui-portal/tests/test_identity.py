from app.identity import build_identity, normalize_name


def test_name_is_normalized_and_identity_is_stable() -> None:
    assert normalize_name("  John   Smith  ") == "John Smith"

    first = build_identity(" John Smith ", "a" * 32)
    second = build_identity("John Smith", "a" * 32)

    assert first == second
    assert first.email.startswith("john-smith-")
    assert len(first.sub_id) == 20
    assert first.comment == "John Smith"


def test_name_rejects_non_english_markup_and_invalid_length() -> None:
    for value in ("", "A", "Иван", "John 2", "<script>alert(1)</script>", "x" * 65):
        try:
            normalize_name(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for {value!r}")
