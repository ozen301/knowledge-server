"""Repeatable checks for the retrieval questions in docs/sample-notes.md.

Each case repeats a reference query from the question set against the sample
notes in tests/fixtures/vault/ and checks the matches and the lines that hold
the expected answer. The tests run the real ripgrep and no model provider.
"""

import asyncio
import shutil
import unicodedata
from pathlib import Path

import pytest

from knowledge_server.core.models import ReadRequest, SearchRequest
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import read_note
from knowledge_server.core.search import search_notes

_FOUND_RG = shutil.which("rg")
if _FOUND_RG is None:
    raise RuntimeError("ripgrep (rg) must be installed to run the evaluation tests")
RG: str = _FOUND_RG

VAULT = Path(__file__).parent / "fixtures" / "vault"
POLICY = PathPolicy(VAULT)

NAS = "infrastructure/nas-configuration.md"
DEVBOX = "infrastructure/ubuntu-development-environment.md"
METRICS = "computer-vision/object-detection-metrics.md"
QUANT = "llm/local-llm-quantization.md"
SEMANTIC = "knowledge-system/semantic-search.md"
VLM = "llm/VLMメモ.md"
EGO4D = "computer-vision/ego4d.md"
EGOCENTRIC = "computer-vision/一人称視点映像.md"
STATE_CHANGE = "computer-vision/物体状態変化.md"


def _hits(query: str) -> list[tuple[str, int]]:
    request = SearchRequest(queries=[query])
    result = asyncio.run(search_notes(POLICY, request, ripgrep=RG))
    assert not result.truncated
    assert not result.incomplete
    return [(match.path, match.line) for match in result.matches]


def _read(path: str, start_line: int, end_line: int) -> str:
    """Return the text of a line range, without the line-number prefixes."""
    request = ReadRequest(path=path, start_line=start_line, end_line=end_line)
    numbered = read_note(POLICY, request).numbered_content
    return "".join(line.partition("\t")[2] + "\n" for line in numbered.split("\n")[:-1])


@pytest.mark.parametrize(
    ("question", "query", "expected"),
    [
        ("E1", "CPU", [(NAS, 7), (DEVBOX, 15), (DEVBOX, 21)]),
        ("E2", "Git repositories", [(NAS, 27), (NAS, 29)]),
        ("E3", "mAP", [(METRICS, 22)]),
        ("E4", "middle ground", [(QUANT, 13)]),
        ("E5", "Jégou", []),
        ("E5", "Jegou", []),
        ("E5", "FAISS", [(SEMANTIC, 12), (SEMANTIC, 14)]),
        ("E5", "Douze", [(SEMANTIC, 18)]),
        ("E6", "embedding", [(SEMANTIC, 9), (SEMANTIC, 26)]),
        ("E7", "parity", []),
        (
            "E7",
            "RAID",
            [(NAS, 5), (NAS, 12), (NAS, 21), (NAS, 25)]
            + [(DEVBOX, line) for line in (21, 113, 114, 187, 218, 219, 227)],
        ),
        ("E8", "Hervé Jégou", []),
        ("E8", "Hervé", []),
        # The decomposed é starts with a plain e, so a query that ends just
        # before the accent matches.
        ("E8", "Herve", [(SEMANTIC, 18)]),
        ("J1", "利点", [(EGOCENTRIC, 13), (STATE_CHANGE, 66)]),
        ("J2", "数字+単位", []),
        ("J2", "数字＋単位", [(VLM, 3)]),
        ("J2", "OCR", [(VLM, 4), (VLM, 10)]),
        (
            "J3",
            "Ego4D",
            [(EGO4D, 1), (EGO4D, 3)]
            + [(STATE_CHANGE, line) for line in (34, 40, 104, 111, 119)],
        ),
        ("J3", "3,670", [(EGO4D, 5)]),
        ("J4", "ベンチマーク", []),
        (
            "J4",
            "データセット",
            [(EGO4D, 3)] + [(STATE_CHANGE, line) for line in (30, 32, 81)],
        ),
        ("J4", "Episodic Memory", [(EGO4D, 11)]),
        (
            "J5",
            "行動認識",
            [(EGO4D, 18), (EGOCENTRIC, 29), (STATE_CHANGE, 11), (STATE_CHANGE, 120)],
        ),
        ("J6", "ベンチ", []),
        ("J6", "benchmark", []),
        ("E9", "Node.js", [(DEVBOX, 78), (DEVBOX, 92)]),
        ("E9", "nvm", [(DEVBOX, 78), (DEVBOX, 95), (DEVBOX, 96)]),
        ("E10", "share is not mounted", [(DEVBOX, 216)]),
        ("E11", "_netdev", [(DEVBOX, 124), (DEVBOX, 125), (DEVBOX, 128)]),
        ("J7", "VOST", [(STATE_CHANGE, 35), (STATE_CHANGE, 112)]),
        ("J8", "一番難しい", [(STATE_CHANGE, 83)]),
    ],
)
def test_reference_query_matches(
    question: str, query: str, expected: list[tuple[str, int]]
) -> None:
    """A reference query finds exactly the expected lines."""
    assert _hits(query) == expected


@pytest.mark.parametrize(
    ("question", "path", "start_line", "end_line", "answer"),
    [
        ("E1", NAS, 7, 7, "AMD Ryzen 5 2600X"),
        ("E2", NAS, 29, 29, "Bare Git repositories can be stored on the NAS"),
        ("E2", NAS, 35, 35, "working clone inside the development VM"),
        ("E3", METRICS, 22, 22, "mAP = mean(AP for each class)"),
        ("E4", QUANT, 12, 13, "### Q5\nSeems like a reasonable middle ground"),
        ("E6", SEMANTIC, 26, 26, "Which embedding model works for both"),
        ("J1", EGOCENTRIC, 9, 13, "手の動きが見えやすい"),
        ("J2", VLM, 3, 4, "普通のOCRよりVLMの方が扱いやすい"),
        ("J3", EGO4D, 5, 5, "約3,670時間の映像"),
        ("E9", DEVBOX, 78, 78, "| Node.js | 22 LTS | nvm |"),
        ("E9", DEVBOX, 96, 96, "nvm install --lts"),
        ("E10", DEVBOX, 218, 218, "Check that the NAS itself is up"),
        ("E10", DEVBOX, 222, 223, "check the NFS rule for the\n   share"),
        ("E11", DEVBOX, 124, 124, "nfs defaults,_netdev,noatime 0 0"),
        ("E11", DEVBOX, 128, 128, "makes systemd wait for the network"),
        ("J7", STATE_CHANGE, 35, 35, "形が大きく変わる物体のセグメンテーション"),
        ("J8", STATE_CHANGE, 81, 83, "多段階の状態変化をどう表現するか"),
    ],
)
def test_answer_lines(
    question: str, path: str, start_line: int, end_line: int, answer: str
) -> None:
    """The expected answer is on the lines that a citation names."""
    assert answer in _read(path, start_line, end_line)


@pytest.mark.parametrize(
    ("question", "path", "start_line", "end_line", "answer"),
    [
        ("E5", SEMANTIC, 18, 18, "Jeff Johnson, Matthijs Douze, Hervé Jégou."),
        ("J4", EGO4D, 11, 12, "5つのベンチマーク（Episodic Memory"),
        ("J4", EGO4D, 12, 12, "が定義されたデータセット。"),
    ],
)
def test_decomposed_answer_lines(
    question: str, path: str, start_line: int, end_line: int, answer: str
) -> None:
    """These lines store accents and voiced marks as combining characters.

    If an editor normalizes the notes to NFC, the evaluation of the Unicode
    limitation no longer applies, and this test fails.
    """
    content = _read(path, start_line, end_line)
    assert answer not in content
    assert answer in unicodedata.normalize("NFC", content)
