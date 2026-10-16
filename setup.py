"""Install Sedabot dependencies into the current Python.

Prefer the project virtual environment:

    .venv\\Scripts\\python.exe setup.py
"""

import subprocess
import sys


def main() -> None:
    print("Sedabot for Windows — setup")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    print()
    print("Setup complete.")
    print("Run:  python sayit.py")
    print("Or:   run.bat")
    print("The first launch downloads Ganji into models\\.")


if __name__ == "__main__":
    main()
