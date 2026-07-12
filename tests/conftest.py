import os

# Keep the background simulator off during tests so state is deterministic.
os.environ.setdefault("ORBITAL_SIMULATOR_ENABLED", "0")
