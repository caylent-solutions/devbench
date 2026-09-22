# Windows Setup Guide

DevBench's tooling — the Makefile, the plugin guard hooks under
`plugin/devbench-orchestrate/scripts/`, and `scripts/install-hook-deps.sh` —
assumes a Unix-like environment: bash, `/usr/bin`, `make`, POSIX file
permissions. None of that is native to Windows, and there is no
lightweight way to patch around it. **Run DevBench inside WSL2**, not
directly on Windows. This guide gets you from a stock Windows machine to
a working WSL2 environment ready to follow
[docs/zero-to-ready.md](zero-to-ready.md).

If you hit something not covered here, please open an issue or a PR —
this guide reflects one real end-to-end setup, not an exhaustive survey
of every Windows configuration.

---

## Table of contents

- [Why WSL2, not native Windows](#why-wsl2-not-native-windows)
- [Step 1: Enable virtualization](#step-1-enable-virtualization)
- [Step 2: Install WSL2 + a distro](#step-2-install-wsl2--a-distro)
- [Step 3: Install prerequisites inside WSL](#step-3-install-prerequisites-inside-wsl)
- [Step 4: Authenticate gh and claude](#step-4-authenticate-gh-and-claude)
- [Critical: where Claude Code sessions must run](#critical-where-claude-code-sessions-must-run)
- [Minor gotchas when Windows tools touch the WSL repo](#minor-gotchas-when-windows-tools-touch-the-wsl-repo)
- [Continue to zero-to-ready.md](#continue-to-zero-to-readymd)

---

## Why WSL2, not native Windows

We first attempted a native-Windows setup (Git Bash + winget-installed
`make`/`jq`). It is not viable:

- GNU Make ports for Windows (e.g. GnuWin32) break on their own install
  path when it contains spaces or parentheses (`C:\Program Files
  (x86)\...`), because recursive `$(MAKE)` invocations get mis-quoted by
  the underlying shell call.
- `scripts/install-hook-deps.sh` resolves `python3` via
  `PATH="/usr/bin:$PATH" command -v python3`. On Windows there is no real
  `/usr/bin`, and the `python3` name is often bound to a disabled Windows
  Store "app execution alias" stub that shadows a real Python
  installation elsewhere on `PATH` — the script installs PyYAML for the
  wrong (non-functional) interpreter.
- Guard hooks and other scripts assume POSIX file permissions
  (executable bits) that Windows does not model the same way.

None of this is a shortage of individual fixes — it's that the tooling's
baseline assumption (a Unix environment) doesn't hold, and every fix
uncovers the next mismatch. WSL2 gives you that Unix environment for
real, at which point every step in the rest of this repo's docs works
exactly as written.

---

## Step 1: Enable virtualization

WSL2 requires hardware virtualization enabled in your machine's
firmware. Check current status:

```powershell
wsl --status
```

If you see `WSL2 is unable to start since virtualization is not enabled
on this machine`, you must enable it in your BIOS/UEFI firmware
(commonly "Intel VT-x", "AMD-V", or "SVM Mode" — under a "Security" or
"Advanced" tab; the exact path is vendor-specific, e.g. ThinkPads use
`F1` at boot to enter setup, not the more common `Del`/`F2`). This
requires a reboot into firmware settings and cannot be scripted.

## Step 2: Install WSL2 + a distro

From an elevated (Administrator) PowerShell:

```powershell
wsl --install
```

If virtualization was previously disabled, re-run after enabling it. If
it reports the WSL platform installed but no distro, install one
explicitly:

```powershell
wsl --install -d Ubuntu --no-launch
```

Ubuntu's first launch normally runs an interactive username/password
wizard. For a scripted setup, you can bypass it and create a user
directly:

```powershell
wsl -d Ubuntu -u root useradd -m -s /bin/bash -G sudo <your-username>
wsl -d Ubuntu -u root bash -c "echo '<your-username> ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/<your-username>"
wsl -d Ubuntu -u root bash -c "printf '[user]\ndefault=<your-username>\n' > /etc/wsl.conf"
wsl --shutdown
```

## Step 3: Install prerequisites inside WSL

All of this runs *inside* the WSL Ubuntu shell (`wsl -d Ubuntu`), not in
PowerShell:

```bash
sudo apt-get update -y
sudo apt-get install -y git jq curl python3 python3-pip python3-yaml build-essential

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

# Node.js (needed for the Claude Code CLI) + Claude Code
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code

# GitHub CLI
sudo mkdir -p -m 755 /etc/apt/keyrings
wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt-get update
sudo apt-get install -y gh
```

From here, `git clone https://github.com/caylent-solutions/devbench.git
~/devbench` and `make install` / `make validate` behave exactly as
[docs/zero-to-ready.md](zero-to-ready.md) describes — no further
workarounds needed once you're inside WSL.

## Step 4: Authenticate `gh` and `claude`

```bash
gh auth login
```

**Then run this before cloning any private repo:**

```bash
gh auth setup-git
```

Without this step, `git clone` of a private repo over HTTPS hangs
indefinitely on an interactive credential prompt with no error message
— `git` isn't wired to use `gh`'s stored token until `setup-git` runs.

```bash
claude auth login
```

This opens a browser OAuth flow and then asks you to paste a code back
into the *same terminal*. Run it in a real interactive terminal you're
typing into directly — it cannot be driven from a backgrounded or
non-interactive script, since the code has to be pasted back into the
process that's waiting for it.

---

## Critical: where Claude Code sessions must run

**The Claude Code Desktop app on Windows runs as a Windows-native
process with its own plugin/marketplace configuration
(`C:\Users\<you>\.claude\`), entirely separate from a WSL-installed
`claude`'s configuration (`/home/<you>/.claude/` inside WSL).**

If you register a DevBench marketplace (e.g. `devbench-authoring`) using
the WSL-side `claude` CLI, **a Desktop app session merely pointed at a
WSL folder via a UNC path (`\\wsl.localhost\Ubuntu\...` or
`\\wsl$\Ubuntu\...`) will never see it** — restarting that session does
not help, because there is nothing to "pick up": it's reading from a
completely different config store. Worse, Claude Code refuses to trust
a plugin marketplace whose source is a network-shaped path at all, even
when added directly to `extraKnownMarketplaces` in that Windows-side
config — so there is no workaround that keeps you on the Windows-native
process.

**The fix: run `claude` from a real terminal executing natively inside
WSL** (`wsl -d Ubuntu`, or Windows Terminal with a WSL profile, or VS
Code's Remote-WSL extension) for any session that needs a DevBench
plugin's skills. A Desktop app session pointed at a WSL path via UNC is
fine for browsing and editing files, git status, and running shell
commands via a Windows-hosted agent shelling into `wsl.exe` — but it
cannot itself load a WSL-registered plugin.

---

## Minor gotchas when Windows tools touch the WSL repo

If you browse or `git`-operate on the WSL clone from the Windows side
(Explorer, Windows Git, a Windows-native Claude Code session's tools),
you may hit:

- **"dubious ownership" errors.** Windows Git refuses to operate on a
  UNC path until you add it explicitly:
  ```bash
  git config --global --add safe.directory '%(prefix)///wsl.localhost/Ubuntu/home/<you>/devbench'
  ```
- **Every file showing as modified with 0 insertions/deletions.**
  Windows Git can't see Unix executable-permission bits over the UNC
  bridge and reports every `.sh` script's mode as changed from `755` to
  `644`. Fix locally (does not affect the actual file):
  ```bash
  git config core.fileMode false
  ```

Neither of these affects a real WSL-native terminal — they're specific
to viewing a WSL repo *through* Windows Git.

---

## Continue to zero-to-ready.md

Once WSL2 is set up per the steps above, follow
[docs/zero-to-ready.md](zero-to-ready.md) starting at Step 1 — every
step from there on works as written, running from your WSL terminal.
