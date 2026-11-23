"""Mint a demo JWT signed with JWT_SECRET: python scripts/mint_token.py cliente1988 [merchant|operator]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.security import issue_token  # noqa: E402

if __name__ == "__main__":
    sub = sys.argv[1] if len(sys.argv) > 1 else "cliente1988"
    role = sys.argv[2] if len(sys.argv) > 2 else "merchant"
    print(issue_token(get_settings(), sub, role))
