"""Test package. Sets neutral SKILLUMINATI_DEV_* pins before any plugin module loads, so the
dev-harness runs (a1, a2, free) can be tested with made-up names and the tests never depend on
the author's own provider or models. Fake models only; nothing here makes a call."""

import os

DEV_PINS = {
    "SKILLUMINATI_DEV_PROVIDER": "dev-provider",
    "SKILLUMINATI_DEV_MODEL": "dev-small-model",
    "SKILLUMINATI_DEV_LARGE_MODEL": "dev-large-model",
    "SKILLUMINATI_DEV_HARM_MODEL": "dev-harm-model",
    "SKILLUMINATI_DEV_FREE_PROVIDER": "dev-free-provider",
    "SKILLUMINATI_DEV_FREE_MODEL": "dev-free-model:free",
}
os.environ.update(DEV_PINS)
os.environ.pop("SKILLUMINATI_DEV_FREE_COLUMN", None)
