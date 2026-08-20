"""Tests for SCORM package validation stage."""

import os
import tempfile
import zipfile

from kitesforu_qa.stages.scorm_validation import validate_scorm_package


def _create_scorm_zip(
    files: dict[str, str | bytes],
    include_manifest: bool = True,
    manifest_content: str | None = None,
) -> str:
    """Helper to create a SCORM ZIP for testing.

    Args:
        files: Dict of filename -> content pairs
        include_manifest: Whether to include imsmanifest.xml
        manifest_content: Custom manifest XML (overrides auto-generation)

    Returns:
        Path to the temporary ZIP file
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_manifest:
            if manifest_content:
                zf.writestr("imsmanifest.xml", manifest_content)
            else:
                # Default valid SCORM 1.2 manifest
                zf.writestr("imsmanifest.xml", _BASIC_MANIFEST)
        for name, content in files.items():
            if isinstance(content, bytes):
                zf.writestr(name, content)
            else:
                zf.writestr(name, content)
    tmp.close()
    return tmp.name


_BASIC_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="test-manifest" version="1.0"
  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2">
  <organizations default="org1">
    <organization identifier="org1">
      <title>Test Course</title>
      <item identifier="item1" identifierref="res1">
        <title>Lesson 1</title>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="res1" type="webcontent" adlcp:scormtype="sco" href="lesson1/index.html">
      <file href="lesson1/index.html"/>
    </resource>
  </resources>
</manifest>"""

_SCO_HTML_WITH_API = """<!DOCTYPE html>
<html>
<head><title>Lesson 1</title></head>
<body>
<script>
var API = null;
function findAPI(win) {
  if (win.API) return win.API;
  if (win.parent && win.parent !== win) return findAPI(win.parent);
  return null;
}
API = findAPI(window);
if (API) API.LMSInitialize("");
</script>
<h1>Lesson 1</h1>
<audio src="audio/lesson1.mp3" controls></audio>
</body>
</html>"""

_SCO_HTML_NO_API = """<!DOCTYPE html>
<html>
<head><title>Lesson 1</title></head>
<body>
<h1>Lesson 1</h1>
<audio src="audio/lesson1.mp3" controls></audio>
</body>
</html>"""


class TestValidScormPackage:
    """Tests for valid SCORM packages."""

    def test_valid_package(self, tmp_path):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
            "lesson1/audio/lesson1.mp3": b"fake-mp3-data",
        })
        try:
            result = validate_scorm_package(path)
            assert result.passed
            assert result.data["manifest_found"]
            assert result.data["manifest_valid"]
            assert result.data["sco_count"] >= 1
            assert result.data["resources_valid"]
            assert len(result.issues) == 0
        finally:
            os.unlink(path)

    def test_valid_package_with_expected_sco_count(self):
        # _SCO_HTML_WITH_API references <audio src="audio/lesson1.mp3">, so a
        # package calling itself VALID has to actually contain that file. This
        # fixture omitted it, which made the test assert that a package missing a
        # referenced asset still passes -- the opposite of what the validator is
        # for. (It was red before the relative-path fix too, just for a
        # confusing reason: the raw src never matched any ZIP entry.)
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
            "lesson1/audio/lesson1.mp3": b"fake-mp3-data",
        })
        try:
            result = validate_scorm_package(path, expected_sco_count=1)
            assert result.passed
        finally:
            os.unlink(path)


class TestInvalidScormPackage:
    """Tests for invalid SCORM packages."""

    def test_nonexistent_file(self):
        result = validate_scorm_package("/nonexistent/path.zip")
        assert not result.passed
        assert "does not exist" in result.issues[0]

    def test_not_a_zip(self, tmp_path):
        bad_file = tmp_path / "not_a_zip.zip"
        bad_file.write_text("this is not a zip file")
        result = validate_scorm_package(str(bad_file))
        assert not result.passed
        assert "not a valid ZIP" in result.issues[0]

    def test_missing_manifest(self):
        path = _create_scorm_zip(
            {"lesson1/index.html": "<html></html>"},
            include_manifest=False,
        )
        try:
            result = validate_scorm_package(path)
            assert not result.passed
            assert any("imsmanifest.xml" in i for i in result.issues)
        finally:
            os.unlink(path)

    def test_invalid_manifest_xml(self):
        path = _create_scorm_zip(
            {"lesson1/index.html": "<html></html>"},
            manifest_content="<not valid xml>>>",
        )
        try:
            result = validate_scorm_package(path)
            assert not result.passed
            assert any("not valid XML" in i for i in result.issues)
        finally:
            os.unlink(path)

    def test_missing_resource_file(self):
        manifest = """<?xml version="1.0" encoding="UTF-8"?>
        <manifest identifier="test" xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2">
          <resources>
            <resource identifier="r1" type="webcontent" href="missing.html">
              <file href="missing.html"/>
            </resource>
          </resources>
        </manifest>"""
        path = _create_scorm_zip({}, manifest_content=manifest)
        try:
            result = validate_scorm_package(path)
            assert not result.passed
            assert result.data["resources_valid"] is False
            assert "missing.html" in result.data["missing_resources"]
        finally:
            os.unlink(path)

    def test_wrong_sco_count(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
        })
        try:
            result = validate_scorm_package(path, expected_sco_count=5)
            assert not result.passed
            assert any("Expected 5 SCOs" in i for i in result.issues)
        finally:
            os.unlink(path)


class TestApiStubValidation:
    """Tests for SCORM API stub detection."""

    def test_sco_without_api_reference(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_NO_API,
        })
        try:
            result = validate_scorm_package(path, check_api_stub=True)
            assert not result.passed
            assert result.data["api_stub_present"] is False
            assert any("SCORM API reference" in i for i in result.issues)
        finally:
            os.unlink(path)

    def test_sco_with_api_reference(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
        })
        try:
            result = validate_scorm_package(path, check_api_stub=True)
            assert result.data["api_stub_present"]
        finally:
            os.unlink(path)

    def test_skip_api_check(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_NO_API,
        })
        try:
            result = validate_scorm_package(path, check_api_stub=False)
            # Should pass without API check
            assert result.data["api_stub_present"]  # default value
        finally:
            os.unlink(path)


class TestAudioFileValidation:
    """Tests for audio file reference checking."""

    def test_missing_audio_files(self):
        html_with_audio = """<html><body>
        <audio src="audio/lesson1.mp3" controls></audio>
        </body></html>"""
        manifest = """<?xml version="1.0" encoding="UTF-8"?>
        <manifest identifier="test" xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2">
          <resources>
            <resource identifier="r1" type="webcontent" href="lesson1.html">
              <file href="lesson1.html"/>
            </resource>
          </resources>
        </manifest>"""
        path = _create_scorm_zip(
            {"lesson1.html": html_with_audio},
            manifest_content=manifest,
        )
        try:
            result = validate_scorm_package(path, check_audio_files=True)
            # Audio file referenced but not in zip
            assert any("audio" in i.lower() for i in result.issues)
        finally:
            os.unlink(path)

    def test_audio_files_present(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
            "lesson1/audio/lesson1.mp3": b"fake-mp3",
        })
        try:
            result = validate_scorm_package(path, check_audio_files=True)
            # No missing audio issues
            assert not any("audio file" in i.lower() for i in result.issues)
        finally:
            os.unlink(path)


class TestStageResultFormat:
    """Tests for result format compliance."""

    def test_result_stage_name(self):
        result = validate_scorm_package("/nonexistent.zip")
        assert result.stage_name == "scorm_validation"

    def test_result_to_dict(self):
        path = _create_scorm_zip({
            "lesson1/index.html": _SCO_HTML_WITH_API,
        })
        try:
            result = validate_scorm_package(path)
            d = result.to_dict()
            assert "passed" in d
            assert isinstance(d["passed"], bool)
        finally:
            os.unlink(path)
