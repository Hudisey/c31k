# Render varsayılan olarak "gunicorn app:app" çalıştırır; bu satır onu c31k.py'ye bağlar.
from c31k import app  # noqa: F401
