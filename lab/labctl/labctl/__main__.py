import multiprocessing

from labctl.cli import app

if __name__ == "__main__":  # a collector process (spawn) imports this module too
    multiprocessing.freeze_support()
    app()
