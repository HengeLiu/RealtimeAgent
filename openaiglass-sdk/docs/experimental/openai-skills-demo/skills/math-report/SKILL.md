---
name: math-report
description: Use when the user asks to calculate numbers and write a short calculation report.
tools: add_numbers, save_text
---

# Math Report Skill

Use this skill when the user asks for arithmetic plus a short written report.

## Rules

- Use `add_numbers` for addition instead of doing arithmetic directly in the answer.
- If the user asks to save or write a report, call `save_text`.
- Keep the final answer concise.
- If a file is saved, mention the saved file path.

