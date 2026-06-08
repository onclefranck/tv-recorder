from tv_recorder.gui import _default_source_value


def test_default_source_value_uses_config_default() -> None:
    values = [
        "abc-news-live - ABC News Live",
        "radio-canada.ca - ICI Radio-Canada Tele",
    ]

    assert (
        _default_source_value({"default": "radio-canada.ca"}, values)
        == "radio-canada.ca - ICI Radio-Canada Tele"
    )


def test_default_source_value_falls_back_to_first_source() -> None:
    values = [
        "abc-news-live - ABC News Live",
        "radio-canada.ca - ICI Radio-Canada Tele",
    ]

    assert _default_source_value({"default": "missing"}, values) == values[0]
