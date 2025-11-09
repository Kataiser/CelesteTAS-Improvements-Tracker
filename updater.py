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
    print(f"Parent PID: {parent_pid}")

    time.sleep(1)
    target_process = psutil.Process(target_pid)
    target_process.kill()
    time.sleep(0.5)
    parent_process = psutil.Process(parent_pid)
    parent_process.kill()
    print("Killed processes")
    time.sleep(1)

    subprocess.run('git pull')
    time.sleep(1)

    bot_starts = ('run.bat', 'CelesteTAS Improvements Tracker.exe', 'python bot.py')

    for bot_start in bot_starts:
        if os.path.isfile(bot_start):
            subprocess.Popen(bot_start, creationflags=0x00000010)

    time.sleep(10)


if __name__ == '__main__':
    main()
