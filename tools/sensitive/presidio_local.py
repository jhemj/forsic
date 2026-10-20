"""Selected Presidio wheel modules for a dedicated, offline helper process.

Do not import presidio_analyzer normally: its package initializer imports NLP
engines. This loader executes a fixed list of unmodified, hash-checked wheel
modules with an isolated package initializer. No AnalyzerEngine, spaCy, model,
registry or remote recognizer is imported. This is intentionally not a general
replacement for the supported upstream package initializer.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

BASE = Path(__file__).resolve().parent
CORE = (
    ("analysis_explanation", "AnalysisExplanation"),
    ("recognizer_result", "RecognizerResult"),
    ("entity_recognizer", "EntityRecognizer"),
    ("local_recognizer", "LocalRecognizer"),
    ("pattern", "Pattern"),
    ("pattern_recognizer", "PatternRecognizer"),
)
SELECTED = (
    ("country_specific.korea.kr_rrn_recognizer", "KrRrnRecognizer"),
    ("country_specific.korea.kr_frn_recognizer", "KrFrnRecognizer"),
    ("country_specific.korea.kr_passport_recognizer", "KrPassportRecognizer"),
    ("country_specific.korea.kr_driver_license_recognizer", "KrDriverLicenseRecognizer"),
    ("country_specific.korea.kr_brn_recognizer", "KrBrnRecognizer"),
    ("generic.credit_card_recognizer", "CreditCardRecognizer"),
)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_core():
    """Return minimal upstream core; must run in a dedicated helper process."""
    previous = sys.modules.get("presidio_analyzer")
    if previous is not None:
        if getattr(previous, "_forsic_minimal", False):
            return previous
        raise RuntimeError("Presidio already initialized outside minimal loader")
    root = BASE / "python" / "presidio_analyzer"
    manifest = json.loads((BASE / "presidio-source-sha256.json").read_text())
    for relative, digest in manifest.items():
        if hashlib.sha256((BASE / "python" / relative).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Presidio source integrity mismatch")
    sys.path.insert(0, str(BASE / "python"))
    package = types.ModuleType("presidio_analyzer")
    package.__path__ = [str(root)]
    package._forsic_minimal = True
    sys.modules[package.__name__] = package
    # entity_recognizer imports this small standard-library-only helper.
    _module("presidio_analyzer.score_thresholds", root / "score_thresholds.py")
    for filename, exported in CORE:
        module = _module("presidio_analyzer." + filename, root / (filename + ".py"))
        setattr(package, exported, getattr(module, exported))
    return package


def load_recognizers():
    """Return entity->recognizer; direct analyze(..., nlp_artifacts=None) only.

    PHONE_NUMBER and EMAIL_ADDRESS are local bounded format candidates. They
    do not validate assignment, DNS, ownership or a real person. Upstream
    format/checksum validation remains in the selected Korean/card recognizers.
    Context enhancement from AnalyzerEngine is deliberately absent.
    """
    package = load_core()
    root = BASE / "python" / "presidio_analyzer" / "predefined_recognizers"
    # Empty namespace parents avoid predefined_recognizers/__init__.py imports.
    for suffix in ("predefined_recognizers", "predefined_recognizers.country_specific",
                   "predefined_recognizers.country_specific.korea", "predefined_recognizers.generic"):
        name = "presidio_analyzer." + suffix
        if name not in sys.modules:
            namespace = types.ModuleType(name)
            namespace.__path__ = [str(BASE / "python" / "presidio_analyzer" / suffix.replace(".", "/"))]
            sys.modules[name] = namespace
    recognizers = {}
    for suffix, exported in SELECTED:
        name = "presidio_analyzer.predefined_recognizers." + suffix
        module = sys.modules.get(name) or _module(name, root / (suffix.replace(".", "/") + ".py"))
        recognizer = getattr(module, exported)()
        recognizer.forsic_provenance = "presidio_upstream"
        recognizers[recognizer.supported_entities[0]] = recognizer
    patterns = {
        "EMAIL_ADDRESS": (r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?){1,10}(?![\w.-])", 0.5),
        "PHONE_NUMBER": (r"(?<!\d)(?:\+82[- .]?(?:10|2|[3-6][1-5])|0(?:10|2|[3-6][1-5]))[- .]?\d{3,4}[- .]?\d{4}(?!\d)", 0.4),
    }
    for entity, (pattern, score) in patterns.items():
        recognizer = package.PatternRecognizer(supported_entity=entity,
            supported_language="ko", name="ForsicLocal" + entity,
            patterns=[package.Pattern(entity + " bounded local format v1", pattern, score)])
        recognizer.forsic_provenance = "adapter_local_pattern"
        recognizers[entity] = recognizer
    return recognizers
