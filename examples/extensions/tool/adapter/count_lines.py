import json,sys
from pathlib import Path
data=Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"lines": len(data.splitlines()), "bytes": len(data.encode("utf-8"))}))
