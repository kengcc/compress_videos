# Agent Instructions

This repository contains utilities and scripts for compressing videos. Keep changes small, readable, and easy to verify.

## Programming Standards

- Write clear, idiomatic code for the language already used in the touched files.
- Prefer simple functions with focused responsibilities over broad, clever abstractions.
- Keep naming descriptive and consistent with nearby code.
- Handle errors explicitly, especially around filesystem paths, subprocesses, media tools, and user input.
- Avoid hardcoded machine-specific paths. Use configuration, arguments, or documented defaults.
- Preserve existing behavior unless the requested change intentionally modifies it.

## Formatting, Syntax, and Linting

- Keep code syntactically valid and formatted before finishing.
- Use the formatter and linter already configured in the repository when one exists.
- If no formatter or linter exists yet, follow the common style for the language and avoid introducing noisy formatting churn.
- Do not mix unrelated style-only changes into functional changes.
- Keep shell commands portable where reasonable, and quote paths that may contain spaces.

## Documentation

- Document public commands, scripts, flags, and configuration in concise language.
- Add comments only where they clarify non-obvious decisions or edge cases.
- Keep README updates practical: include purpose, setup, usage examples, and important limitations when relevant.
- Avoid duplicating obvious implementation details in comments.

## Tests and Verification

- Add or update tests for behavior changes when the project has a test structure.
- For script changes, verify the main command path and at least one failure path where practical.
- When tests cannot be run, state why and describe what was checked instead.
- Do not commit generated media, large outputs, caches, or local-only artifacts unless explicitly required.

## Dependency and Tooling Changes

- Add new dependencies only when they materially simplify the implementation or improve reliability.
- Prefer standard libraries and existing project tools before introducing new packages.
- Document any external runtime requirement, such as `ffmpeg`, including how it is expected to be found.
- Keep dependency version changes scoped and intentional.

## Git and Change Hygiene

- Check the worktree before editing when making non-trivial changes.
- Do not revert or overwrite user changes unless explicitly asked.
- Keep commits focused on one logical change.
- Exclude temporary files, build outputs, logs, and machine-local configuration from commits.
