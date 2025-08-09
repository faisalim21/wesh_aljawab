#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import os
from pathlib import Path

# حمل .env.dev تلقائياً لو موجود (محلي فقط)
try:
    from dotenv import load_dotenv
    env_dev = Path(__file__).resolve().parent / ".env.dev"
    if env_dev.exists():
        load_dotenv(env_dev)
except Exception:
    pass

def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wesh_aljawab.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
