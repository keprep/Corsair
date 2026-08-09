import os

# Qt must be told to run headless before anything imports it.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CORSAIR_CONTROL_LANG", "en")
