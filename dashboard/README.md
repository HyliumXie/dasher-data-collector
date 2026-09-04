# DoorDash lifecycle labeler

From the project root, run:

```powershell
python dashboard/build_dashboard.py
```

Then open <http://127.0.0.1:8765>. The server reads screenshot records directly
from `data/<date>/<timestamp>/` and appends labels to
`annotations/page_labels.jsonl`. It never modifies raw collector data.

Keyboard shortcuts: Left/Right selects the previous/next page; Ctrl+S (or
Command+S) saves the current label.
