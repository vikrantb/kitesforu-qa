"""SCORM package validation stage.

Validates SCORM 1.2 and 2004 packages for structural correctness,
manifest validity, and resource integrity.
"""

import os
import zipfile
from typing import List, Optional
from xml.etree import ElementTree as ET

from ..models.results import StageResult


# SCORM namespace mappings
SCORM_NAMESPACES = {
    "imscp": "http://www.imsproject.org/xsd/imscp_rootv1p1p2",
    "imscp12": "http://www.imsglobal.org/xsd/imscp_v1p1",
    "adlcp": "http://www.adlnet.org/xsd/adlcp_rootv1p2",
    "adlcp2004": "http://www.adlnet.org/xsd/adlcp_v1p3",
}


def validate_scorm_package(
    zip_path: str,
    expected_sco_count: Optional[int] = None,
    check_audio_files: bool = True,
    check_api_stub: bool = True,
) -> StageResult:
    """Validate a SCORM package ZIP file.

    Checks:
    - ZIP is valid and readable
    - imsmanifest.xml exists at root
    - Manifest XML is well-formed
    - All SCO resources referenced in manifest exist in ZIP
    - Audio files referenced in SCOs exist (optional)
    - SCORM API stub JavaScript is present in SCO HTML files (optional)

    Args:
        zip_path: Path to the SCORM ZIP package
        expected_sco_count: Expected number of SCOs (optional)
        check_audio_files: Whether to verify audio file references
        check_api_stub: Whether to check for SCORM API JavaScript

    Returns:
        StageResult with validation findings
    """
    issues: List[str] = []
    data = {
        "zip_path": zip_path,
        "manifest_found": False,
        "manifest_valid": False,
        "sco_count": 0,
        "resources_valid": True,
        "missing_resources": [],
        "audio_files_found": [],
        "api_stub_present": True,
    }

    # 1. Check ZIP is valid
    if not os.path.exists(zip_path):
        return StageResult(
            stage_name="scorm_validation",
            passed=False,
            data=data,
            issues=["ZIP file does not exist"],
            error=f"File not found: {zip_path}",
        )

    if not zipfile.is_zipfile(zip_path):
        return StageResult(
            stage_name="scorm_validation",
            passed=False,
            data=data,
            issues=["File is not a valid ZIP archive"],
            error="Invalid ZIP file",
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            file_list = zf.namelist()

            # 2. Check imsmanifest.xml at root
            if "imsmanifest.xml" not in file_list:
                issues.append("imsmanifest.xml not found at ZIP root")
                return StageResult(
                    stage_name="scorm_validation",
                    passed=False,
                    data=data,
                    issues=issues,
                )
            data["manifest_found"] = True

            # 3. Parse and validate manifest XML
            manifest_xml = zf.read("imsmanifest.xml")
            try:
                tree = ET.fromstring(manifest_xml)
            except ET.ParseError as e:
                issues.append(f"imsmanifest.xml is not valid XML: {e}")
                return StageResult(
                    stage_name="scorm_validation",
                    passed=False,
                    data=data,
                    issues=issues,
                )
            data["manifest_valid"] = True

            # 4. Extract resource references from manifest
            resources = _extract_resources(tree)
            data["sco_count"] = len(resources)

            if expected_sco_count is not None and len(resources) != expected_sco_count:
                issues.append(
                    f"Expected {expected_sco_count} SCOs but found {len(resources)}"
                )

            # 5. Verify all referenced resources exist in ZIP
            missing = []
            for href in resources:
                if href and href not in file_list:
                    # Try with path normalization
                    normalized = href.replace("\\", "/")
                    if normalized not in file_list:
                        missing.append(href)

            if missing:
                data["resources_valid"] = False
                data["missing_resources"] = missing
                issues.append(
                    f"{len(missing)} resource(s) referenced in manifest not found in ZIP: "
                    + ", ".join(missing[:5])
                )

            # 6. Check audio files in SCO HTML files
            if check_audio_files:
                audio_files = _find_audio_references(zf, resources, file_list)
                data["audio_files_found"] = audio_files
                missing_audio = [f for f in audio_files if f not in file_list]
                if missing_audio:
                    issues.append(
                        f"{len(missing_audio)} audio file(s) referenced but not in ZIP: "
                        + ", ".join(missing_audio[:5])
                    )

            # 7. Check for SCORM API stub in SCO HTML files
            if check_api_stub:
                html_resources = [r for r in resources if r.endswith((".html", ".htm"))]
                scos_without_api = []
                for html_path in html_resources:
                    if html_path in file_list:
                        html_content = zf.read(html_path).decode("utf-8", errors="ignore")
                        if not _has_scorm_api_reference(html_content):
                            scos_without_api.append(html_path)

                if scos_without_api:
                    data["api_stub_present"] = False
                    issues.append(
                        f"{len(scos_without_api)} SCO(s) missing SCORM API reference: "
                        + ", ".join(scos_without_api[:3])
                    )

    except zipfile.BadZipFile as e:
        return StageResult(
            stage_name="scorm_validation",
            passed=False,
            data=data,
            issues=["ZIP file is corrupted"],
            error=str(e),
        )

    passed = len(issues) == 0
    return StageResult(
        stage_name="scorm_validation",
        passed=passed,
        data=data,
        issues=issues,
    )


def _extract_resources(manifest_root: ET.Element) -> List[str]:
    """Extract resource hrefs from the manifest.

    Handles both SCORM 1.2 and 2004 namespace variants.
    """
    hrefs = []

    # Try with various namespace prefixes
    for ns_prefix in ["imscp", "imscp12", ""]:
        if ns_prefix:
            ns = SCORM_NAMESPACES.get(ns_prefix, "")
            resource_xpath = f".//{{{ns}}}resource"
        else:
            resource_xpath = ".//resource"

        resources = manifest_root.findall(resource_xpath)
        if resources:
            for resource in resources:
                href = resource.get("href")
                if href:
                    hrefs.append(href)
                # Also check <file> elements
                for ns_try in [ns_prefix, ""]:
                    if ns_try and ns_try in SCORM_NAMESPACES:
                        file_ns = SCORM_NAMESPACES[ns_try]
                        files = resource.findall(f"{{{file_ns}}}file")
                    else:
                        files = resource.findall("file")
                    for file_el in files:
                        file_href = file_el.get("href")
                        if file_href and file_href not in hrefs:
                            hrefs.append(file_href)
            break

    # Fallback: try without namespace
    if not hrefs:
        for resource in manifest_root.iter():
            if resource.tag.endswith("resource"):
                href = resource.get("href")
                if href:
                    hrefs.append(href)

    return hrefs


def _find_audio_references(
    zf: zipfile.ZipFile, html_resources: List[str], file_list: List[str]
) -> List[str]:
    """Find audio file references in SCO HTML files."""
    audio_files = []
    audio_extensions = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")

    for html_path in html_resources:
        if not html_path.endswith((".html", ".htm")):
            continue
        if html_path not in file_list:
            continue

        html_content = zf.read(html_path).decode("utf-8", errors="ignore")
        # Simple pattern matching for audio src attributes
        for ext in audio_extensions:
            idx = 0
            while True:
                idx = html_content.find(ext, idx)
                if idx == -1:
                    break
                # Walk backward to find the start of the path
                start = idx
                while start > 0 and html_content[start - 1] not in ('"', "'", "=", " ", ">"):
                    start -= 1
                audio_path = html_content[start : idx + len(ext)]
                if audio_path and audio_path not in audio_files:
                    audio_files.append(audio_path)
                idx += len(ext)

    return audio_files


def _has_scorm_api_reference(html_content: str) -> bool:
    """Check if HTML content references a SCORM API.

    Checks for both SCORM 1.2 (API) and SCORM 2004 (API_1484_11).
    """
    scorm_indicators = [
        "API_1484_11",       # SCORM 2004
        "API.LMSInitialize", # SCORM 1.2
        "API.LMSGetValue",   # SCORM 1.2
        "LMSInitialize",     # SCORM 1.2 direct
        "Initialize(",       # SCORM 2004 direct
        "scormAPI",          # Common wrapper name
        "pipwerks",          # Popular SCORM wrapper library
    ]
    for indicator in scorm_indicators:
        if indicator in html_content:
            return True
    return False
