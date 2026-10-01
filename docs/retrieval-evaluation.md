# Retrieval evaluation

This document describes how to evaluate retrieval with a small, fixed set
of questions about the sample notes in `tests/fixtures/vault/`, and holds the
current baseline results from local MCP hosts. Use it to check a retrieval
change: run the affected questions, compare the results with the baseline,
and replace the baseline when the change is adopted. The [implementation
tasks](implementation-tasks.md) record what each evaluation decided.

The evaluation is a quick check, not a rigorous benchmark. Each question runs
once per host, and models vary between runs, so a single result is an example,
not a rate.

Each question has three parts that are recorded separately, so that a failure
can be traced to one step:

- the **question**, as the vault owner would ask it;
- the **reference queries**, literal search queries that show which lines the
  search can and cannot find;
- the **expected answer** and the lines that support it, or the expected
  outcome "no answer".

A host run then shows which queries the agent actually chose, which notes it
read, and whether the cited lines support its answer. A failure is caused by
one of: **query choice** (the agent did not search for words in the note),
**matching** (the note contains the text but search cannot find it),
**reading** (the agent answered without reading the lines it needed), or
**citation** (the cited path or line does not support the answer).

`tests/test_retrieval_evaluation.py` repeats every reference query and checks
its matches and the expected answer lines. It runs the real ripgrep and no
model provider.

## Sample notes with decomposed Unicode

Two notes store some characters in decomposed form, as text copied from a PDF
often is. The characters look the same as their usual precomposed form but do
not match a query typed in that form. The notes contain no comment about
this, so that the comment cannot change search results.

| Note | Line | Decomposed text |
|---|---|---|
| `knowledge-system/semantic-search.md` | 18 | `é` in "Hervé" and "Jégou" is `e` followed by U+0301 |
| `computer-vision/ego4d.md` | 11 | `ベ` in "ベンチマーク" is `ヘ` followed by U+3099 |
| `computer-vision/ego4d.md` | 12 | `デ` in "データセット" is `テ` followed by U+3099 |

Line 3 of `ego4d.md` contains "データセット" in precomposed form, so the same
word matches on one line and not on the other. `llm/VLMメモ.md` line 3
contains the full-width `＋`, which does not match the half-width `+`.

`test_decomposed_answer_lines` fails if an editor normalizes these lines. If
you edit these notes, keep the decomposed characters.

## Question set

Paths are relative to `tests/fixtures/vault/`. A reference query with no
matches is written as "none".

| ID | Question | Expected answer and source | Reference queries -> matches | Purpose |
|---|---|---|---|---|
| E1 | Which CPU does my NAS use? | AMD Ryzen 5 2600X; `infrastructure/nas-configuration.md` line 7 | `CPU` -> line 7, and "vCPUs" in `infrastructure/ubuntu-development-environment.md` lines 15, 21 | Direct fact |
| E2 | Where should I keep bare Git repositories, and where should I do development work? | On the NAS (lines 29, 33); work in a clone inside the development VM (line 35); `infrastructure/nas-configuration.md`. `infrastructure/ubuntu-development-environment.md` lines 140-156 also answer it | `Git repositories` -> lines 27, 29 | Answer spread over a section |
| E3 | How do I calculate mAP over multiple classes? | mAP = mean(AP for each class); `computer-vision/object-detection-metrics.md` line 22 | `mAP` -> line 22 | Direct fact |
| E4 | Which quantization level seemed like a good compromise between memory and quality? | Q5; `llm/local-llm-quantization.md` lines 12-13 | `middle ground` -> line 13 | Paraphrased question; the level is in the heading above the match |
| E5 | Who are the authors of the FAISS paper? | Jeff Johnson, Matthijs Douze, Hervé Jégou; `knowledge-system/semantic-search.md` line 18 | `Jégou`, `Jegou` -> none; `FAISS` -> lines 12, 14; `Douze` -> line 18 | Decomposed accent |
| E6 | Which embedding model did I choose for semantic search? | No answer: line 26 of `knowledge-system/semantic-search.md` lists it as an open question | `embedding` -> lines 9, 26 | No answer, with related text |
| E7 | How many parity disks does my NAS array use? | No answer | `parity` -> none; `RAID` -> "Unraid" on 4 lines of `nas-configuration.md` and 7 of `ubuntu-development-environment.md` | No answer; substring false positives |
| E8 | Which of my notes mention Hervé Jégou? | `knowledge-system/semantic-search.md` line 18 | `Hervé Jégou`, `Hervé` -> none; `Herve` -> line 18 | Decomposed accent is the only way in |
| J1 | 一人称視点映像には三人称視点と比べてどんな利点がある？ | 手の動きが見えやすい、操作している物体が大きく映る、注目対象を推定しやすい; `computer-vision/一人称視点映像.md` lines 9-13 | `利点` -> line 13, and `computer-vision/物体状態変化.md` line 66 | Japanese; the list is above the match |
| J2 | VLMはどんな画像で普通のOCRより扱いやすい？ | 数字＋単位＋ラベルが同時に写っている画像; `llm/VLMメモ.md` lines 3-4 | `数字+単位` -> none; `数字＋単位` -> line 3; `OCR` -> lines 4, 10 | Full-width `＋` |
| J3 | Ego4Dの映像は合計何時間？ | 約3,670時間; `computer-vision/ego4d.md` line 5 | `Ego4D` -> lines 1, 3, and 5 lines of `物体状態変化.md`; `3,670` -> line 5 | Japanese direct fact |
| J4 | Ego4Dにはどんなベンチマークがある？ | Episodic Memory, Hands and Objects, Audio-Visual Diarization, Social Interactions, Forecasting; `computer-vision/ego4d.md` lines 11-12 | `ベンチマーク` -> none; `データセット` -> line 3, and lines 30, 32, 81 of `物体状態変化.md`; `Episodic Memory` -> line 11 | Decomposed voiced mark |
| J5 | 行動認識にはどのモデルを使う予定？ | No answer: no note names a model. `行動認識` appears in links and in one sentence of `物体状態変化.md` | `行動認識` -> `computer-vision/ego4d.md` line 18, `computer-vision/一人称視点映像.md` line 29, `computer-vision/物体状態変化.md` lines 11, 120 | No answer; links and unrelated mentions only |
| J6 | ベンチマークについて書いたノートはどれ？ | `computer-vision/ego4d.md` lines 11-12 | `ベンチマーク`, `ベンチ`, `benchmark` -> none | Decomposed voiced mark is the only way in |
| E9 | How did I install Node.js in my development VM? | With nvm; `infrastructure/ubuntu-development-environment.md` line 78 (table), lines 95-96 (commands) | `Node.js` -> lines 78, 92; `nvm` -> lines 78, 95, 96 | Table row and code block |
| E10 | What should I check when the NAS share isn't mounted in my development VM? | Four steps; `infrastructure/ubuntu-development-environment.md` lines 218-223 | `share is not mounted` -> line 216 | Answer after line 200 of a 245-line note |
| E11 | Which mount options do I use for the NAS shares, and why do I use _netdev? | `defaults,_netdev,noatime`; `_netdev` waits for the network; `infrastructure/ubuntu-development-environment.md` lines 124-125, 128-129 | `_netdev` -> lines 124, 125, 128 | Code lines and the prose that explains them |
| J7 | VOSTはどんなデータセット？ | 形が大きく変わる物体のセグメンテーション; `computer-vision/物体状態変化.md` line 35 | `VOST` -> lines 35, 112 | Japanese table row |
| J8 | 物体状態変化で一番難しいと思っている点は？ | 多段階の状態変化をどう表現するか; `computer-vision/物体状態変化.md` lines 81-83 | `一番難しい` -> line 83 | Answer spread over several prose lines |

`Herve` matches line 18 because the decomposed `é` starts with a plain `e`.
A query that continues past the accent, such as `Herve J` or `Jegou`, does
not match.

The hosts receive each question with a request for a citation:

- English: `Using my knowledge vault, answer: <question> Cite the source note
  path and line number.`
- Japanese: `knowledge vaultを使って答えてください：<question>
  出典のノートのパスと行番号も示してください。`

## Run the questions through a host

Copy the sample notes so that a host cannot change them, and start the host
from an empty directory that is not near the copy. Codex runs shell commands
in its working directory, and in one run it found the vault copy in a sibling
directory and read it without the tools.

```sh
cp -a tests/fixtures/vault /path/to/eval/vault
mkdir -p /path/to/empty/dir && cd /path/to/empty/dir
```

Claude Code, with only the four tools allowed. `mcp.json` holds the
`mcpServers.knowledge` entry with `KNOWLEDGE_ROOT=/path/to/eval/vault`. Leave
out `--model` to use the account's default model.

```sh
claude -p --model sonnet --tools "" --strict-mcp-config --mcp-config mcp.json \
  --allowedTools mcp__knowledge__knowledge_search mcp__knowledge__knowledge_read \
    mcp__knowledge__knowledge_list mcp__knowledge__knowledge_info \
  --output-format stream-json --verbose "PROMPT" < /dev/null > run.jsonl
```

Codex CLI, in its read-only sandbox:

```sh
codex exec -m gpt-6-luna -c model_reasoning_effort=medium -s read-only \
  --skip-git-repo-check \
  -c 'mcp_servers.knowledge.command="uv"' \
  -c 'mcp_servers.knowledge.args=["run","--project","/path/to/knowledge-server","--locked","knowledge-server"]' \
  -c 'mcp_servers.knowledge.env={KNOWLEDGE_ROOT="/path/to/eval/vault"}' \
  --json -o answer.txt "PROMPT" < /dev/null > events.jsonl
```

The tool calls are the `tool_use` items in `run.jsonl` and the
`mcp_tool_call` items in `events.jsonl`. After the runs, `diff -r` the copy
against `tests/fixtures/vault/` to confirm that nothing changed.

Grade each run from its transcript:

- The answer is **correct** when it matches the expected answer or the
  expected no-answer outcome.
- A cited line is **off** when it does not contain the text it is cited for.
  A citation is **incomplete** when its lines contain only part of that text.
- Count the knowledge tool calls. Shell commands do not count.
- The first run of each question counts. A run that read the vault without the
  tools, for example with shell commands, is not counted; repeat it from an
  empty directory.

## Baseline results

Run on 2026-10-01 with the numbered read format of the
[contract](phase-1-contract.md#knowledge_read). Hosts: Claude Code 2.1.286
with its default model, Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), and
with Claude Sonnet 5.5 (`claude-sonnet-5-5`); Codex CLI 0.159.3 with
`gpt-6-luna` at medium effort. The Japanese prompt then said 「ナレッジボールト」
instead of "knowledge vault". The vault copy was unchanged afterwards.

Each cell gives the outcome, the cited lines, and the number of tool calls.

| ID | Haiku 4.5 | Sonnet 5.5 | Codex, Luna medium |
|---|---|---|---|
| E1 | Correct; line 7; 4 | Correct; lines 7, 22; 2 | Correct; line 7; 2 |
| E2 | Correct; lines 140, 155 incomplete; 8 | Correct; lines 140-159; 2 | Correct; lines 140-156; 4 |
| E3 | Correct; line 22; 6 | Correct; lines 10-22; 2 | Correct; lines 18-22; 4 |
| E4 | Correct; line 13 incomplete; 2 | Correct; lines 7-17; 3 | **Failed: no tool calls**; 0 |
| E5 | Correct; lines 18-19; 3 | Correct; lines 18-19; 2 | Correct; lines 18-19; 2 |
| E6 | Correct no answer; line 26; 20 | Correct no answer; line 26; 3 | Correct no answer; line 26; 3 |
| E7 | Correct no answer; 10 | Correct no answer; lines 5, 16-17; 4 | Correct no answer; lines 10-17; 5 |
| E8 | **Wrong: no notes found**; 3 | **Wrong: no notes found**; 5 | **Wrong: no notes found**; 2 |
| J1 | Correct; lines 9-13; 4 | Correct; lines 7-13, 15-21; 4 | Correct; lines 7-13; 6 |
| J2 | Correct; lines 3-4; 7 | Correct; lines 3-4, 6-8; 3 | Correct; lines 3-4, 6-8; 6 |
| J3 | Correct; line 5; 2 | Correct; lines 5-7; 2 | Correct; line 5; 2 |
| J4 | Correct; lines 11-12; 3 | Correct; lines 11-12, 14; 2 | Correct; lines 11-12; 2 |
| J5 | **Partly wrong: presents a TODO item as the plan**; lines 61-67, 102-104; 25 | Correct no answer; lines 18, 25, 29, 61-67, 120; 6 | Correct no answer; lines 25, 61; 8 |
| J6 | Correct; lines 11-12; 16 | **Wrong: no notes found**; 14 | **Wrong: no notes found**; 4 |
| E9 | Correct; lines 92-97; 3 | Correct; lines 78, 92-97; 2 | Correct; lines 95, 96; 2 |
| E10 | **Wrong: answered from the setup section**; lines 113-135; 4 | Correct; lines 216-223; 4 | Correct; lines 216-223; 6 |
| E11 | Correct; lines 128-129 incomplete; 6 | Correct; lines 124-125, 128-129; 2 | Correct; lines 124-129; 4 |
| J7 | Correct; line 35; 2 | Correct; lines 35, 112; 3 | Correct; line 35; 2 |
| J8 | Correct; lines 83 incomplete, 85-88; 2 | Correct; lines 77-88; 3 | Correct; lines 81-83; 4 |

No cited line was off. Four runs had incomplete citations, all from Haiku:
each cited only part of the passage that supports its answer (E2, E4, E11,
J8). Sonnet and Codex had none. The failed runs:

- **E8, all hosts:** they searched `Hervé Jégou`, `Jégou`, `Herve Jegou`, and
  similar precomposed or unaccented forms, found nothing, and answered that no
  note mentions the name. `Herve` alone would have matched.
- **J6, Sonnet and Codex:** they searched `ベンチマーク`, `benchmark`, `性能評価`,
  and similar words, found nothing, and answered that no note covers
  benchmarks. Haiku listed the folders and read notes until it reached
  `ego4d.md`.
- **J5, Haiku:** it read the related notes and presented a TODO item, "Ego4D
  Hands and Objects のベースラインを動かしてみる", as the planned model.
- **E10, Haiku:** it searched `NAS share mounted development VM`, `NAS share
  mount`, and `NAS`, read the mount setup at lines 111-145, and answered from
  it. It never reached the troubleshooting section at line 214.
- **E4, Codex:** no tool calls. It ran `rg` in its empty working directory
  and answered that it found no notes.

## Known weaknesses

These weaknesses appear in the baseline. A retrieval change that targets one
of them should improve the questions named here.

- **Decomposed text causes false no-answer results** (E8, J6). When the
  decomposed word is the only way to a note, hosts search the precomposed
  form, find nothing, and report that no note exists. When the note can also
  be found through other words (E5, J4), every host finds and reads it.
- **Questions with no answer cost many calls** (E6, J5). The hosts search
  until they are confident that the vault does not contain the answer: up to
  25 calls. Questions with an answer usually need 2 to 8 calls.
- **Queries that combine separate keywords find nothing.** A query such as
  `一人称視点 三人称視点 利点` is one literal string, so it does not match lines
  that contain the words separately.
- **Codex often skips the tools.** Codex usually runs shell commands such as
  `rg` in its working directory before it calls the tools, and sometimes it
  stops there (E4). This is host behavior; the server cannot change it.

Citations are mostly reliable when the line number comes from the server:
from a search match or from a numbered read line. Haiku sometimes cites only
one line of a longer passage. With the earlier read format,
which returned only the first and last line numbers of a range, hosts often
miscounted lines in whole-note reads.

## Limit review

No search was truncated or incomplete, and no read needed a second page. The
hosts asked for up to 50 results, the maximum. The sample notes are too small
to test the file size, read, and search budgets. The observed results do not
justify a change, so the [initial limits](phase-1-contract.md#initial-limits)
stay as they are. Review them again with the private evaluation of the real
vault or when larger notes are added.

## Deferred: NFC-equivalent matching

NFC normalization converts decomposed characters to their precomposed form.
If search normalized both the query and the note text to NFC, E8 and J6
would find line 18 of `semantic-search.md` and line 11 of `ego4d.md`. It
would not make `Jegou` match `Jégou`, which needs accent-insensitive matching,
and it would stop the accidental `Herve` match. Snippets, paths, and line
numbers must still come from the original text, as the [implementation
plan](implementation-plan.md#deferred-retrieval-decisions) requires.

The demonstrated cost is a false no-answer result when the decomposed word is
the only way to the note: in 7 of 12 E8 and J6 runs over the two evaluation
rounds. Decomposed text is common in text copied from PDFs and in macOS file
names, so NFC matching remains an important deferred feature. It needs a
specification for normalized search input and original-text snippets before
implementation. Width matching, such as `＋` and `+`, remains a separate
decision; no host searched with the half-width form in J2.
