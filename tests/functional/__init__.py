"""Functional (stub-claude) test layer for ``devbench supervise`` (Phase 5, Section 10.0).

These tests drive the FULL ``supervise`` CLI flows (start / __run / stop / restart /
status / info / attach) against the REAL executable ``stub-claude.py`` fixture through
the REAL ``pexpect`` supervisor -- no real ``claude``, no subscription, no tokens, and
no ``screen`` (screen is bypassed by driving the in-screen ``__run`` body directly, the
same program ``screen`` would otherwise host). Every scenario asserts the state-machine
transition, the exit code, and the persisted registry state (AC-13..20, AC-30).

The shared :mod:`functional.harness` provides the deterministic wiring; each test file
scripts one stub behaviour and asserts the supervisor's response.
"""
