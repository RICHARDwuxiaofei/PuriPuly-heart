from scripts.preview.build_portable import copy_payload_file, extended_path


def test_runtime_files_with_windows_long_names_are_copied(tmp_path):
    source = tmp_path / ("source-" + "s" * 80)
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    filename = "dependency-" + "d" * 170 + ".py"
    original = source / filename
    copied = destination / filename
    with open(extended_path(original), "wb") as handle:
        handle.write(b"verified runtime payload")
    copy_payload_file(original, copied)
    with open(extended_path(copied), "rb") as handle:
        assert handle.read() == b"verified runtime payload"
