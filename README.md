# roulbot

## Setup

The winning-numbers database is stored compressed (`mvp2/winning_numbers.db.gz`) to keep the repo under GitHub's file-size limit. Decompress it before running anything that reads the database:

```bash
# macOS / Linux / Git Bash
gunzip -k mvp2/winning_numbers.db.gz
```

```powershell
# Windows PowerShell (if gunzip is unavailable)
$in  = 'mvp2/winning_numbers.db.gz'
$out = 'mvp2/winning_numbers.db'
$src = [System.IO.File]::OpenRead($in)
$dst = [System.IO.File]::Create($out)
$gz  = New-Object System.IO.Compression.GzipStream($src, [System.IO.Compression.CompressionMode]::Decompress)
$gz.CopyTo($dst); $gz.Dispose(); $dst.Dispose(); $src.Dispose()
```

This produces `mvp2/winning_numbers.db`, which the application code expects. The raw `.db` is git-ignored, so re-run this step after a fresh clone.
