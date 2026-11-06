"""Run the eval through Claude Code, with no API key and no copy-pasting.

``warlord eval run`` sends each question to ``claude -p``, Claude Code's
headless mode, which uses your Claude Code login. Each question gets a fresh
conversation with only the warlord MCP server connected, and the referee
instructions appended to the system prompt. The final answer, the tools the
agent called, the turn count and the cost go into the answers file.

``warlord eval grade`` then shows each answer next to the gold answer and
asks you correct / incorrect. Or ``--judge`` has Claude compare them; you
spot-check, and your own marks always win over the judge's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from warlord.agent.prompts import REFEREE_MCP

JUDGE_PROMPT = """You grade answers to Twilight Imperium 4 rules questions.
Compare the ANSWER with the GOLD answer. The answer is correct if it reaches the same ruling for the same reason;
wording, extra detail, citations and labels do not matter. Reply with exactly one word: CORRECT or INCORRECT.

QUESTION: {question}
GOLD: {gold}
ANSWER: {answer}"""


class ClaudeNotFound(RuntimeError):
    pass


def _claude() -> str:
    path = shutil.which("claude")
    if path is None:
        raise ClaudeNotFound("The claude command (Claude Code) is not on PATH. Install Claude Code and log in first.")
    return path


def _mcp_config(data: Path, folder: Path) -> Path:
    cfg = {"mcpServers": {"warlord": {"command": sys.executable, "args": ["-m", "warlord.mcp_server", "--data", str(data)]}}}
    path = folder / "warlord-mcp.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def ask(question: str, data: Path, model: str | None = None, timeout: int = 600, runner=subprocess.run) -> dict:
    """One fresh headless Claude Code conversation with only the warlord tools."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _mcp_config(data, Path(tmp))
        cmd = [
            _claude(), "-p", question,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(cfg), "--strict-mcp-config",
            "--allowedTools", "mcp__warlord",
            "--permission-mode", "dontAsk",
            "--append-system-prompt", REFEREE_MCP,
            "--no-session-persistence",
        ]
        if model:
            cmd += ["--model", model]
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout, cwd=tmp)
    tools, answer, meta = [], "", {}
    for line in (proc.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    tools.append(str(block.get("name", "")).removeprefix("mcp__warlord__"))
        elif event.get("type") == "result":
            answer = event.get("result") or ""
            meta = {k: event.get(k) for k in ("num_turns", "total_cost_usd", "is_error") if k in event}
    if not answer and proc.returncode:
        meta["error"] = (proc.stderr or "").strip()[-500:]
    return {"answer": answer.strip(), "tools_used": tools, **meta}


def run(questions: list[dict], answers_path: Path, data: Path, model: str | None = None,
        rerun: bool = False, progress=print, runner=subprocess.run) -> dict:
    """Ask every question that has no answer yet; keep existing answers and grades."""
    existing = _load(answers_path)
    done = 0
    for q in questions:
        row = existing.get(q["id"], {"id": q["id"], "question": q["question"], "correct": None})
        if row.get("answer") and not rerun:
            continue
        progress(f"{q['id']}: {q['question'][:70]}")
        result = ask(q["question"], data, model=model, runner=runner)
        row.update(result)
        row["correct"] = None if rerun else row.get("correct")
        existing[q["id"]] = row
        _save(answers_path, existing, [x["id"] for x in questions])
        done += 1
    return {"asked": done, "file": str(answers_path)}


def judge(questions: list[dict], answers_path: Path, model: str | None = None, runner=subprocess.run) -> dict:
    gold = {q["id"]: q for q in questions}
    rows = _load(answers_path)
    judged = 0
    for qid, row in rows.items():
        if not row.get("answer") or qid not in gold or row.get("judge") is not None:
            continue
        prompt = JUDGE_PROMPT.format(question=gold[qid]["question"], gold=gold[qid]["answer"], answer=row["answer"])
        cmd = [_claude(), "-p", prompt, "--output-format", "json", "--strict-mcp-config", "--tools", "",
               "--no-session-persistence"]
        if model:
            cmd += ["--model", model]
        proc = runner(cmd, capture_output=True, text=True, timeout=300)
        try:
            verdict = str(json.loads(proc.stdout).get("result", "")).strip().upper()
        except (json.JSONDecodeError, AttributeError):
            verdict = ""
        row["judge"] = True if verdict.startswith("CORRECT") else False if verdict.startswith("INCORRECT") else None
        judged += 1
    _save(answers_path, rows, list(rows))
    return {"judged": judged}


def grade(questions: list[dict], answers_path: Path, read=input, show=print) -> dict:
    """Walk through answered, ungraded questions: y = correct, n = incorrect, s = skip, q = quit."""
    gold = {q["id"]: q for q in questions}
    rows = _load(answers_path)
    graded = 0
    for qid, row in rows.items():
        if not row.get("answer") or row.get("correct") is not None or qid not in gold:
            continue
        q = gold[qid]
        show(f"\n=== {qid} [{q['topic']}] {q['question']}\n--- Gold ({', '.join(q['refs'])}): {q['answer']}\n"
             f"--- Answer{' (judge: ' + ('correct' if row['judge'] else 'incorrect') + ')' if row.get('judge') is not None else ''}:\n"
             f"{row['answer']}\n")
        key = (read("Correct? [y/n/s/q] ") or "s").strip().lower()[:1]
        if key == "q":
            break
        if key in ("y", "n"):
            row["correct"] = key == "y"
            graded += 1
            _save(answers_path, rows, list(rows))
    return {"graded": graded}


def _load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def _save(path: Path, rows: dict[str, dict], order: list[str]) -> None:
    ids = [i for i in order if i in rows] + [i for i in rows if i not in order]
    with path.open("w", encoding="utf-8") as fh:
        for i in ids:
            fh.write(json.dumps(rows[i], ensure_ascii=False) + "\n")
