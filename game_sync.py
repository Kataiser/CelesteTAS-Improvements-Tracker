import logging
import os
import subprocess
import time
from typing import Optional

import requests

from utils import projects


def sync_test(project: int):
    # log.info(f"Running sync test for project {projects[project]['name']}")
    installed_mods = [item for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]
    blacklist = []

    for installed_mod in installed_mods:
        if installed_mod.removesuffix('.zip') not in projects[project]['mods'] and installed_mod != 'CelesteTAS.zip':
            blacklist.append(installed_mod)

    with open(r'E:\Big downloads\celeste\Mods\blacklist.txt', 'w') as blacklist_txt:
        blacklist_txt.write("# This file has been created by the Improvements Tracker\n")
        blacklist_txt.write('\n'.join(blacklist))

    # log.info(f"Created blacklist, launching game with {len(mods)} mods")
    subprocess.Popen(r'E:\Big downloads\celeste\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits
    game_loaded = False

    while not game_loaded:
        try:
            time.sleep(2)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.ConnectTimeout:
            pass
        else:
            # log.info("Game loaded")
            game_loaded = True


log: Optional[logging.Logger] = None


if __name__ == '__main__':
    sync_test(970380662907482142)
