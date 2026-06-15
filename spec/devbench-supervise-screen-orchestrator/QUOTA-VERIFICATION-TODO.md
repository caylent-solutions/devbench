# TODO -- Verify the REAL interactive quota / usage-limit prompt before finalizing the supervise quota state machine

> Glyph note: this file uses ASCII double-hyphen `--` everywhere a dash is needed. The em-dash glyph (U+2014) does not appear in this file.

Companion to: `spec/devbench-supervise-screen-orchestrator/devbench-supervise-screen-orchestrator.md` (Section 4.9, Section 7.3, DI-5, AC-29).

## 1. Why this TODO exists (status: UNVERIFIED)

The `devbench supervise` interactive quota state machine (spec Section 4.9) must detect a usage-limit ("5-hour-window exhaustion") event in the screen-scraped PTY output of an interactive `claude` CLI session, and then either inject an in-session wait/retry choice (path 4.9a) or fall back to poll-and-restart (path 4.9b).

The EXACT interactive prompt text, the menu options Claude presents at the limit, and how the reset time is displayed are **UNVERIFIED**. The spec's `supervise.detection_patterns.quota_limit`, `supervise.detection_patterns.quota_wait_prompt`, and `supervise.injectable_commands.quota_wait_choice` values are currently PLACEHOLDERS seeded from the SDK-surface markers in `src/devbench/quota.py` (`_QUOTA_MARKERS`, `_RESET_AT_RE`, `_RATE_LIMIT_RE`). Those markers were observed on the SDK / API surface, NOT in the interactive CLI's on-screen prompt, which may differ in wording and layout.

**These detection patterns MUST NOT be finalized until the data points in Section 2 below are captured against a REAL interactive quota/usage-limit event** (Section 3 is the step-by-step manual capture procedure). Until then, the in-session-wait path (4.9a) is best-effort and the supervisor relies on the poll-and-restart path (4.9b) plus the deterministic devbench log markers (`[QUOTA_WAITING]` / `[QUOTA_POLLING]` / `[ORCHESTRATOR_QUOTA_RESUME]`), which are stable across CLI versions.

This is the HIGHEST-RISK discovery item in the spec (DI-5).

## 2. Data points the implementer needs (capture ALL of these)

Capture each item VERBATIM (copy the exact bytes; do not paraphrase, do not "clean up" punctuation -- the real Unicode apostrophe in "You've" matters, see `quota.py:50`):

1. **Verbatim prompt text.** The full on-screen line(s) the interactive `claude` session prints when the 5-hour usage window is exhausted. Capture every line from the first limit-related word to the prompt cursor.
2. **The menu options Claude presents.** The exact option labels and how they are selected (a number to type? a letter? arrow-key + Enter? a word like "wait"/"retry"?). Record the literal keystroke(s) that select each option.
3. **How the reset time is displayed.** The verbatim reset-time string (e.g. `resets 8:00am (UTC)` vs `Resets at 08:00 UTC` vs a relative `in 3h 12m`). Note the timezone label and the format, so the `reset_at` regex (currently `quota._RESET_AT_RE`, `resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)`) can be confirmed or extended.
4. **Whether selecting "wait" keeps the session ALIVE vs exits.** After choosing the wait/retry option (if one exists): does the same `claude` process stay running and resume on its own when the window refreshes (path 4.9a applies), or does the process exit (path 4.9b applies)?
5. **The exit code IF it exits.** If the session exits on the limit (no in-session wait offered, or "wait" still exits), record the process exit status (`echo $?` immediately after).
6. **Any stderr / log markers.** Anything written to stderr or to `~/.claude/` logs at the moment of the limit (error strings, structured markers, a JSON event). These give a CLI-version-stable detection fallback if the on-screen wording changes.
7. **CLI version.** `claude --version` at the time of capture (the prompt wording is version-fragile; record which version produced the captured strings).

Record each item with its source (PTY transcript line, stderr, transcript jsonl, etc.) so the patterns can cite where they came from.

## 3. Step-by-step MANUAL capture procedure (for a HUMAN operator who hits a real limit)

Run these when you are ABOUT to hit, or have just hit, a real 5-hour-window usage limit in an interactive `claude` session. Every command below is copy-pasteable. The goal is to capture the raw PTY bytes (Section 2) so they can be handed back to finalize the detection patterns.

### 3.0 One-time prep (before you expect to hit the limit)

```bash
# Pick a capture directory and record the CLI version.
mkdir -p ~/quota-capture
cd ~/quota-capture
claude --version | tee ~/quota-capture/claude-version.txt
date -u +"%Y-%m-%dT%H:%M:%SZ" | tee ~/quota-capture/capture-started-utc.txt
```

### 3.1 Run claude under a raw-PTY logger so the limit prompt is captured byte-for-byte

Use ONE of the two loggers below. Both capture the raw terminal bytes (including the exact prompt + menu) to a file you can grep later. Prefer `script` (records keystrokes too); use `screen -L` if you want the run inside a detached screen (closer to how `devbench supervise` runs it).

Option A -- `script(1)` (records the full interactive session, including the limit prompt and your keystrokes):

```bash
# -q quiet, -f flush after each write so the file is current if claude exits,
# the trailing program is the interactive claude session itself.
script -q -f ~/quota-capture/pty-capture.typescript \
  -c 'claude --dangerously-skip-permissions'
# ... use the session normally (or run your real workload) until the limit hits ...
# Immediately AFTER the limit prompt appears, record the exit code if it exited:
echo "claude-exit-code=$?" | tee ~/quota-capture/exit-code.txt
```

Option B -- `screen -L` (run inside a logged screen, mirroring the supervise daemon's PTY):

```bash
# -L enables logging; -Logfile sets the path (GNU screen >= 4.06). The screen
# log file accumulates the raw window output, including the limit prompt.
screen -L -Logfile ~/quota-capture/screen-capture.log -S quota-capture \
  claude --dangerously-skip-permissions
# Detach with Ctrl-a d if needed; reattach with: screen -r quota-capture
# After the limit prompt appears and (if it exits) the screen window dies:
screen -ls | tee ~/quota-capture/screen-ls.txt
```

### 3.2 Copy the VERBATIM prompt out of the raw capture

```bash
# script(1) capture: strip terminal control sequences so the prompt is readable,
# then keep a copy of BOTH the raw and the cleaned versions.
cat -v ~/quota-capture/pty-capture.typescript > ~/quota-capture/pty-capture.catv.txt   # shows control bytes literally
# (If `col -b` is available, a cleaner pass:)
col -b < ~/quota-capture/pty-capture.typescript > ~/quota-capture/pty-capture.clean.txt 2>/dev/null || true

# screen -L capture is already plain text:
#   ~/quota-capture/screen-capture.log

# Grep the capture for the limit/quota/reset wording (case-insensitive).
# These patterns mirror the current placeholders in src/devbench/quota.py; the
# REAL on-screen wording may differ -- record whatever actually matched and the
# surrounding lines.
grep -niE "hit your limit|usage limit|rate.?limit|resets|reset at|try again|wait|upgrade|come back" \
  ~/quota-capture/pty-capture.catv.txt ~/quota-capture/screen-capture.log 2>/dev/null \
  | tee ~/quota-capture/limit-grep.txt

# Capture the FULL prompt block with context (20 lines around each hit) for the
# verbatim record (Section 2 item 1 + item 2 + item 3):
grep -niC 20 -E "hit your limit|usage limit|rate.?limit|resets" \
  ~/quota-capture/pty-capture.catv.txt ~/quota-capture/screen-capture.log 2>/dev/null \
  | tee ~/quota-capture/limit-context.txt
```

### 3.3 Read the transcript JSONL (the structured, CLI-version-stable record)

Interactive `claude` writes a per-session transcript to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. The project slug is the working directory path with `/` replaced by `-` (e.g. the devbench workspace is `~/.claude/projects/-workspaces-telemetry-devbench/`).

```bash
# Find the MOST RECENTLY MODIFIED transcript (the session you just ran):
ls -t ~/.claude/projects/*/*.jsonl | head -5 | tee ~/quota-capture/recent-transcripts.txt
LATEST=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "latest transcript: $LATEST" | tee -a ~/quota-capture/recent-transcripts.txt

# Copy it for the record:
cp "$LATEST" ~/quota-capture/transcript.jsonl

# Grep the transcript for limit / reset / error markers. If `jq` is installed,
# the second form pretty-prints matching events:
grep -niE "limit|rate.?limit|resets|quota|429|usage|too many requests" \
  ~/quota-capture/transcript.jsonl | tee ~/quota-capture/transcript-grep.txt

command -v jq >/dev/null && \
  jq -c 'select((tostring | test("limit|rate.?limit|resets|quota|429|usage"; "i")))' \
  ~/quota-capture/transcript.jsonl | tee ~/quota-capture/transcript-events.json
```

### 3.4 Capture stderr / any side-channel markers

```bash
# If you ran claude with stderr redirected, keep that file. Otherwise, re-run a
# short probe with stderr captured so any limit-time stderr is recorded:
claude --dangerously-skip-permissions 2> ~/quota-capture/claude-stderr.txt
# Inspect ~/.claude/ for any log files touched at the limit time:
find ~/.claude -type f -newermt "$(cat ~/quota-capture/capture-started-utc.txt)" \
  2>/dev/null | tee ~/quota-capture/claude-files-touched.txt
```

### 3.5 Record the reset time and the alive-vs-exit observation

```bash
# Reset time (Section 2 item 3): copy the verbatim reset string from limit-context.txt.
# Alive-vs-exit (Section 2 item 4 + item 5): note whether the process from 3.1
# was still running after the prompt (path 4.9a) or exited (path 4.9b), and its
# exit code (from exit-code.txt). Write your observation down:
cat > ~/quota-capture/observations.md <<'EOF'
# Quota capture observations
- CLI version: (paste from claude-version.txt)
- Verbatim prompt text: (paste the exact line(s) from limit-context.txt)
- Menu options + selecting keystroke(s):
- Reset-time string (verbatim) + format:
- After choosing wait/retry: did the SAME claude process stay alive and resume? (yes/no)
- If it exited: exit code (from exit-code.txt):
- stderr / transcript markers found:
EOF
echo "Fill in ~/quota-capture/observations.md, then hand back the whole ~/quota-capture/ directory."
```

## 4. Hand-back: what to deliver

Bundle and return the `~/quota-capture/` directory (or paste its contents):

```bash
tar -czf ~/quota-capture.tgz -C ~ quota-capture
echo "deliver ~/quota-capture.tgz (contains the verbatim prompt, transcript, and observations)"
```

The captured info finalizes:

- `supervise.detection_patterns.quota_limit` -- the PTY regex matching the verbatim limit line (Section 2 item 1).
- `supervise.detection_patterns.quota_wait_prompt` -- the PTY regex matching the in-session wait/retry prompt (Section 2 item 2), IF one exists.
- `supervise.detection_patterns.reset_at` -- confirm or extend `quota._RESET_AT_RE` against the real reset-time format (Section 2 item 3).
- `supervise.injectable_commands.quota_wait_choice` -- the literal keystroke(s) that select "wait" (Section 2 item 2).
- The 4.9a-vs-4.9b branch default -- driven by the alive-vs-exit observation (Section 2 item 4 + item 5).

Once these are set from REAL data, mark DI-5 confirmed and check off AC-29 in the spec's Section 10.1.
