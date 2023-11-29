import argparse
import os.path
import subprocess
import time

import psutil


def main():
    print("Updating bot")
    parser = argparse.ArgumentParser()
    parser.add_argument('target_pid')
    parser.add_argument('parent_pid')
    target_pid = int(parser.parse_args().target_pid)
    parent_pid = int(parser.parse_args().parent_pid)
    print(f"Target PID: {target_pid}")
    print(f"Target PID: {parent_pid}")

    time.sleep(1)
    target_process = psutil.Process(target_pid)
    target_process.kill()
    parent_process = psutil.Process(parent_pid)
    parent_process.kill()
    print("Killed processes")
    time.sleep(1)

    subprocess.run('git pull')
    time.sleep(1)

    bot_starts = ('CelesteTAS Improvements Tracker.exe', 'run.bat', 'python bot.py')

    if os.path.isfile(bot_starts[0]):
        subprocess.Popen(bot_starts[0], creationflags=0x00000010)
        print(f"Restarted bot via {bot_starts[0]}")
    elif os.path.isfile(bot_starts[1]):
        subprocess.Popen(bot_starts[1], creationflags=0x00000010)
        print(f"Restarted bot via {bot_starts[1]}")
    else:
        subprocess.Popen(bot_starts[2], creationflags=0x00000010)
        print(f"Restarted bot via {bot_starts[2]}")

    time.sleep(10)


if __name__ == '__main__':
    main()
