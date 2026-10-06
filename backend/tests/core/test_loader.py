from control_center.core.loader import discover


def by_key():
    return {d.key: d for d in discover("fake_modules")}


def test_good_module_found():
    assert by_key()["good"].spec is not None


def test_broken_import_reported_not_raised():
    d = by_key()["broken"]
    assert d.spec is None and "import failed" in d.error


def test_folder_without_module_is_skipped():
    assert "not_ready" not in by_key()


def test_key_must_match_folder():
    assert "!= folder" in by_key()["wrong_key"].error


def test_sorted_by_order():
    keys = [d.key for d in discover("fake_modules") if d.spec]
    assert keys == ["good", "failing"]
