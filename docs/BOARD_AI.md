# Board AI (whiteboard intelligence) - contributor guide

The whiteboard has a **Board AI...** button. It sends an image of the current board page to the DoodleDraw Lovable app
and shows what comes back. Rendering/drawing stay local; only the page image (PNG, shrunk to <= 3 MB) and the topic text are sent.

## API
`POST https://dooddraw.lovable.app/api/public/v1/board`, header `x-api-key: <Board AI key>` (a *different* key from the
QuoteTube key), JSON `{"image": "data:image/png;base64,...", "tasks": [...], "lens": "socratic", "context": "..."}`.
Override the host with `IDEAWOOD_BOARD_BASE`. The key is typed in the dialog; saved (plain text in QSettings) only when
"Remember on this PC" is ticked. Never commit a key.

| Button | tasks sent | Result |
|---|---|---|
| Read handwriting | `["read"]` | `transcription` -> text + LaTeX |
| Explain the board | `["explain"]` | `explanation` -> title, summary, key points, quiz |
| Read + explain | `["read","explain"]` | both |
| Finish my sketch | `["finish"]` | editable shapes (labels, arrows, boxes) + preview image |
| Save lecture notes | - | Markdown file: title, summary, key points, board text + LaTeX, quiz |

## What is verified, what is not
* **Verified (from the owner's example):** endpoint, `x-api-key`, request body, `tasks` `read`/`explain`, `lens: "socratic"`,
  `result["transcription"]["latex"]`, `result["explanation"]["quiz"]` (list).
* **Assumed - not verified against the live API:** the task name `finish` (`board_ai.TASK_FINISH`), the field names
  `transcription.text`, `explanation.title/summary/key_points`, and the shape format (see `shapes_to_items`: accepts
  `type|kind|shape`, `x,y,w,h` or `x1,y1,x2,y2` or `from/to`, fractions or pixels, `text|label`).
* Any reply that does not match is still shown under **Raw reply**, so nothing is lost. Please send a real reply for each
  task and tighten `board_ai.py` + `tests/test_board_ai.py` accordingly.

## Code
* `voxcut/board_ai.py` - client, lenient parsers, `shapes_to_items`, `notes_markdown` (no Qt).
* `voxcut/board_ai_ui.py` - dialog + worker thread; "Add shapes" puts them on a NEW page (never overwrites your board).
* `tests/test_board_ai.py` - mock server verifying the request shape, parsing, shape conversion, notes and the dialog flow.

## Ideas
Lens list from the Doodle app, per-page automatic notes, writing the LaTeX onto the board as text, sending a screenshot
region instead of the whole page, caching results per page.
