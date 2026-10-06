# Project instructions

This is a shared training capstone; token waste multiplies across 20+ users.

## Response style

- Default: brief, direct, answer only what was asked. No greetings, filler,
  narration, restated requirements, tutorials, summaries, or next steps unless requested.
- Use short paragraphs or bullets only when they improve clarity. Do not use
  decorative headings, tables, examples, alternatives, or code unless needed.
- Ask one short question only when blocked; otherwise make a safe scoped assumption.
- Never paste large code, diffs, logs, JSON, source, or file contents unless asked.
- Errors: state the actionable cause in one line.

## Work style

- Read only relevant files; prefer targeted search and excerpts.
- After implementation: at most 7 lines: `Completed.`, changed paths, and test results and what was done ... all under 7 lines.
- After implementation never run full test suits and regression testing. Only run the test that are specific to ongoing story. Leave last line with a command for the develop if he/she wants to run the full test suite.
  If no changes: `No changes.` Include developer action items only when present.
- Keep plans, comments, docs, commits, tests, and generated stories compact.

## Retrieval/manual testing

- Never pretty-print full retrieval responses. Do not use `curl ... | python3 -m json.tool`.
- Use raw `curl -s` or focused `jq` fields that exclude/truncate passage `text`.
- After writing a file, report its path; do not echo its contents.
