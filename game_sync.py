import logging
import os
import subprocess
import time
from typing import Optional

import requests

import main
import utils
import validation
from utils import plural, projects


def sync_test(project: int):
    log.info(f"Running sync test for project: {projects[project]['name']}")
    installed_mods = [item for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]
    mods = projects[project]['mods']
    blacklist = []

    for installed_mod in installed_mods:
        if installed_mod.removesuffix('.zip') not in mods and installed_mod != 'CelesteTAS.zip':
            blacklist.append(installed_mod)

    with open(r'E:\Big downloads\celeste\Mods\blacklist.txt', 'w') as blacklist_txt:
        blacklist_txt.write("# This file has been created by the Improvements Tracker\n")
        blacklist_txt.write('\n'.join(blacklist))

    log.info(f"Created blacklist, launching game with {len(mods)} mod{plural(mods)}")
    subprocess.Popen(r'E:\Big downloads\celeste\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits
    game_loaded = False

    while not game_loaded:
        try:
            time.sleep(2)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.ConnectTimeout:
            pass
        else:
            log.info("Game loaded")
            time.sleep(2)
            game_loaded = True

    test_path = r'C:\Program Files (x86)\Steam\steamapps\common\Celeste\CelesteTAS\1A.tas'

    with open(test_path, 'rb') as tas_file:
        tas_read = tas_file.read()

    # set up temp tas file
    tas_lines = validation.as_lines(tas_read)
    _, _, chapter_time, chapter_time_trimmed, chapter_time_line = validation.parse_tas_file(tas_lines, False, False)
    tas_lines[chapter_time_line] = 'ChapterTime: '
    tas_lines.append('***')

    with open(r'E:\Big downloads\celeste\temp.tas', 'w', encoding='UTF8') as temp_tas:
        temp_tas.write('\n'.join(tas_lines))

    # now run it
    log.info(f"Testing timing of {os.path.basename(test_path)} ({chapter_time_trimmed})")
    requests.post(r'http://localhost:32270/tas/playtas?filePath=E:\Big downloads\celeste\temp.tas')
    tas_finished = False

    while not tas_finished:
        try:
            time.sleep(2)
            session_data = requests.get('http://localhost:32270/tas/info', timeout=2)
        except requests.ConnectTimeout:
            pass
        else:
            tas_finished = 'Running: False' in session_data.text

    log.info("TAS has finished")
    time.sleep(2)

    with open(r'E:\Big downloads\celeste\temp.tas', 'rb') as tas_file:
        tas_read = tas_file.read()

    _, found_chaptertime, chapter_time_new, chapter_time_new_trimmed, _ = validation.parse_tas_file(validation.as_lines(tas_read), False, False)
    synced = False

    if found_chaptertime:
        frame_diff = validation.calculate_time_difference(chapter_time, chapter_time_new)
        synced = frame_diff == 0
        log.info(f"{chapter_time_trimmed} -> {chapter_time_new_trimmed} ({'+' if frame_diff > 0 else ''}{frame_diff}f)")
    else:
        log.info("Desynced")
        synced = False


log: Optional[logging.Logger] = None


if __name__ == '__main__':
    log = main.create_loggers()[0]
    utils.load_projects()

    sync_test(970380662907482142)
