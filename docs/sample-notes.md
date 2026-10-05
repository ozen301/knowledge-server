# Sample notes and retrieval questions

`tests/fixtures/vault/` holds a small vault of invented notes in English and
Japanese. They let the project test and demonstrate retrieval without the
vault owner's real notes:

- The automated tests search and read them, and
  `tests/test_retrieval_evaluation.py` checks the reference queries of the
  [questions](#question-set) below against them.
- The HTTP launcher's synthetic mode serves only an exact copy of them,
  checked against `src/knowledge_server/adapter/synthetic-vault.json`. When a
  note changes, update that manifest in the same change.
- The [usage](usage.md#check-the-connection) and
  [deployment](deployment.md#troubleshooting) guides use one of them for a
  connection check.

The notes imitate real ones: setup notes, research notes, a long note of 245
lines, Markdown tables and code blocks, and links between notes. Some lines
are written to exercise known weaknesses of literal search, as the next
section describes.

## Deliberate Unicode forms

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

Each question has three parts that are recorded separately, so that a failure
can be traced to one step:

- the **question**, as the vault owner would ask it;
- the **reference queries**, literal search queries that show which lines the
  search can and cannot find;
- the **expected answer** and the lines that support it, or the expected
  outcome "no answer".

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

## Check a retrieval change

Use the questions to check a change that affects retrieval: run the affected
questions through one or more hosts, compare the results with the [known
weaknesses](#known-weaknesses), and update that section when the change is
adopted. Each question runs once per host, and models vary between runs, so a
single result is an example, not a rate.

A failure is caused by one of: **query choice** (the agent did not search for
words in the note), **matching** (the note contains the text but search
cannot find it), **reading** (the agent answered without reading the lines it
needed), or **citation** (the cited path or line does not support the
answer).

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

## Known weaknesses

The questions were last run on 2026-10-01, with Claude Code 2.1.286 using
Claude Haiku 4.5 and Claude Sonnet 5.5, and with Codex CLI 0.159.3 using
`gpt-6-luna` at medium effort. The other runs answered correctly, and no
cited line was wrong, though Haiku sometimes cited only part of a passage.
These runs failed:

| Question | Hosts | Cause |
|---|---|---|
| E8 | All three | Matching: they searched `Hervé Jégou` and similar precomposed or unaccented forms and reported that no note mentions the name |
| J6 | Sonnet, Codex | Matching: they searched `ベンチマーク`, `benchmark`, and similar words and reported that no note covers benchmarks |
| J5 | Haiku | Reading: it presented a TODO item as the planned model |
| E10 | Haiku | Query choice: it answered from the setup section and never reached the troubleshooting section |
| E4 | Codex | No tool calls: it ran `rg` in its empty working directory instead |

The weaknesses behind them:

- **Decomposed text causes false no-answer results** (E8, J6). When the
  decomposed word is the only way to a note, hosts search the precomposed
  form, find nothing, and report that no note exists. NFC-equivalent
  matching would fix this; the [roadmap](roadmap.md#nfc-equivalent-matching)
  lists it as the first candidate.
- **Questions with no answer cost many calls** (E6, J5): up to 25, against
  2 to 8 for questions with an answer.
- **Queries that combine separate keywords find nothing.** A query such as
  `一人称視点 三人称視点 利点` is one literal string, so it does not match lines
  that contain the words separately.
- **Codex often skips the tools** and runs shell commands in its working
  directory first. This is host behavior; the server cannot change it.

Citations are reliable when the line number comes from the server, from a
search match or a numbered read line. No search was truncated and no read
needed a second page, so the [limits](tool-contract.md#initial-limits) stay
as they are; the sample notes are too small to test the size budgets.
