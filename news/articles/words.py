"""Word analysis of the deep dive's German summaries, in a subprocess.

spaCy and simplemma's dictionary take about 400 MB. Loaded in the worker, that memory would
stay for good (Python rarely hands freed memory back), and Railway bills memory by the
minute. So each analysis runs `words_subprocess.py` as its own process, which exits and frees
it all. Loading takes about a second, next to the minute or so the Sonnet calls take.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().with_name("words_subprocess.py")
# Load plus analysis takes about a second; this only stops a stuck process. It counts against
# Q_CLUSTER's timeout together with the deep dive's Sonnet calls.
TIMEOUT_SECONDS = 60


class AnalysisFailed(Exception):
    pass


def analyze(texts: dict[str, str]) -> dict[str, list[dict]]:
    """Words for each text (see `words_subprocess` for the fields). Raises AnalysisFailed."""
    try:
        result = subprocess.run(
            # -I: isolated mode, ignores PYTHON* env vars and doesn't put the script's or the
            # current directory on sys.path. env={}: the child gets no secrets.
            [sys.executable, "-I", str(SCRIPT)],
            input=json.dumps(texts).encode("ascii"),
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            env={},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisFailed(f"timed out after {TIMEOUT_SECONDS} s") from exc
    except OSError as exc:
        raise AnalysisFailed(f"could not start: {exc}") from exc
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AnalysisFailed(f"exit code {result.returncode}: {(lines or [''])[-1][:300]}")
    try:
        words = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AnalysisFailed("output is not JSON") from exc
    if not isinstance(words, dict) or set(words) != set(texts):
        raise AnalysisFailed("output doesn't match the input texts")
    return words
