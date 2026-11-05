"""Stop only the temporary server process created by a browser check."""
import os
import subprocess


def stop_server(process):
    if process.poll() is None:
        if os.name == 'nt':
            # A Windows venv Python launcher may own a separate interpreter.
            # Terminating just the launcher can leave its SQLite files locked.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.terminate()
    process.wait(timeout=10)
