"""CLI: scripts/gem.sh .venv/bin/python -m agent.audit --plan <plan.py> [--max-duration 7200] (staged Antigravity audit)."""
from agent.managed import staged_main

if __name__ == "__main__":
    staged_main()
