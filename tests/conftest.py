"""
Shared pytest fixtures and path setup.

Sets up sys.path so tests can import from:
  - app/           → `from services.capability_service import ...`
  - project root   → `from voice_assistant.session_manager import ...`
  - project root   → `from app.services.xxx import ...`
"""

import os
import sys

# Project root = parent of this tests/ directory
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_DIR = os.path.join(_PROJECT_ROOT, "app")

for _p in (_PROJECT_ROOT, _APP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
