import base64
import logging
import os
import shutil
import subprocess
import time
from typing import Optional

import discord
import psutil
import requests

import main
import utils
import validation
from utils import plural, projects


async def run_syncs():
    log.info("Running all sync tests")

    for project_id in projects:
        if projects[project_id]['do_run_validation'] and projects[project_id]['path_cache']:
            await sync_test(project_id)

    post_cleanup()


async def sync_test(project: int, report_channel: Optional[discord.DMChannel] = None) -> Optional[str]:
    log.info(f"Running sync test for project: {projects[project]['name']}")
    installed_mods = [item for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]
    mods = projects[project]['mods']
    path_cache = projects[project]['path_cache']
    repo = projects[project]['repo']
    blacklist = []
    desyncs = []
    remove_debug_save_files()

    # create the mod blacklist
    for installed_mod in installed_mods:
        if installed_mod.removesuffix('.zip') not in mods and installed_mod != 'CelesteTAS.zip':
            blacklist.append(installed_mod)

    with open(r'E:\Big downloads\celeste\Mods\blacklist.txt', 'w') as blacklist_txt:
        blacklist_txt.write("# This file has been created by the Improvements Tracker\n")
        blacklist_txt.write('\n'.join(blacklist))

    launching_game_text = f"Created blacklist, launching game with {len(mods)} mod{plural(mods)}"
    log.info(launching_game_text)
    subprocess.Popen(r'E:\Big downloads\celeste\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits
    game_loaded = False

    if report_channel:
        await report_channel.send(f"`{launching_game_text}`")

    # wait for the game to load (handles mods updating as well)
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

    main.generate_request_headers(projects[project]['installation_owner'])

    for tas_filename in path_cache:
        log.info(f"Downloading {path_cache[tas_filename]}")
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{path_cache[tas_filename]}', headers=main.headers)
        utils.handle_potential_request_error(r, 200)
        tas_read = base64.b64decode(r.json()['content'])

        # set up temp tas file
        tas_lines = validation.as_lines(tas_read)
        _, _, chapter_time, chapter_time_trimmed, chapter_time_line = validation.parse_tas_file(tas_lines, False, False)
        tas_lines[chapter_time_line] = 'ChapterTime: '
        tas_lines.append('***')

        with open(r'E:\Big downloads\celeste\temp.tas', 'w', encoding='UTF8') as temp_tas:
            temp_tas.write('\n'.join(tas_lines))

        # now run it
        testing_timing_text = f"Testing timing of {tas_filename} ({chapter_time_trimmed})"
        log.info(testing_timing_text)
        requests.post(r'http://localhost:32270/tas/playtas?filePath=E:\Big downloads\celeste\temp.tas')
        tas_finished = False

        if report_channel:
            await report_channel.send(f"`{testing_timing_text}`")

        while not tas_finished:
            try:
                time.sleep(1)
                session_data = requests.get('http://localhost:32270/tas/info', timeout=2)
            except requests.ConnectTimeout:
                pass
            else:
                tas_finished = 'Running: False' in session_data.text

        log.info("TAS has finished")
        time.sleep(5)

        # determine if it synced or not
        with open(r'E:\Big downloads\celeste\temp.tas', 'rb') as tas_file:
            tas_read = tas_file.read()

        _, found_chaptertime, chapter_time_new, chapter_time_new_trimmed, _ = validation.parse_tas_file(validation.as_lines(tas_read), False, False)

        if found_chaptertime:
            frame_diff = validation.calculate_time_difference(chapter_time, chapter_time_new)
            synced = frame_diff == 0
            sync_text = f"{'Synced' if synced else 'Desynced'}: {chapter_time_trimmed} -> {chapter_time_new_trimmed} ({'+' if frame_diff > 0 else ''}{frame_diff}f)"
            log.info(sync_text)

            if not synced:
                desyncs.append(tas_filename)
        else:
            sync_text = "Desynced (no ChapterTime)"
            log.info(sync_text)
            desyncs.append(tas_filename)

        if report_channel:
            await report_channel.send(f"`{sync_text}`")

    # close the game and studio
    try:
        # https://docs.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist
        processes = str(subprocess.check_output('tasklist /fi "STATUS eq running"')).split(r'\r\n')
    except subprocess.CalledProcessError as error:
        processes = []
        log.error(repr(error))

    for process_line in processes:
        if '.exe' not in process_line:
            continue

        process_name = process_line.split('.exe')[0]
        process_pid = int(process_line.split('.exe')[1].split()[0])

        if process_name == 'Celeste':
            try:
                psutil.Process(process_pid).kill()
                log.info("Closed Celeste")
            except psutil.NoSuchProcess as error:
                log.error(repr(error))
        elif 'studio' in process_name.lower() and 'celeste' in process_name.lower():
            try:
                psutil.Process(process_pid).kill()
                log.info("Closed Studio")
            except psutil.NoSuchProcess as error:
                log.error(repr(error))

    time.sleep(1)
    improvements_channel = client.get_channel(project)
    await main.edit_pin(improvements_channel, False, True)

    if desyncs:
        desyncs_formatted = '\n'.join(desyncs)
        desync_warning = f"Sync check finished, {len(desyncs)} desync{plural(desyncs)} found (of {len(path_cache)} file{plural(path_cache)}):\n```\n{desyncs_formatted}```"
        await improvements_channel.send(desync_warning)
        return desync_warning


# remove all files related to the debug save
def remove_debug_save_files():
    debug_save_files = [file for file in os.listdir(r'E:\Big downloads\celeste\Saves') if file.startswith('debug')]

    for debug_save_file in debug_save_files:
        os.remove(f'E:\\Big downloads\\celeste\\Saves\\{debug_save_file}')

    log.info(f"Removed {len(debug_save_files)} debug save files")


def post_cleanup():
    remove_debug_save_files()
    files_to_remove = ['log.txt', 'temp.tas', 'Mods\\blacklist.txt']
    dirs_to_remove = ['LogHistory', 'TAS Files\\Backups']
    files_removed = 0
    dirs_removed = 0

    for file_to_remove in files_to_remove:
        file_to_remove = f'E:\\Big downloads\\celeste\\{file_to_remove}'

        if os.path.isfile(file_to_remove):
            files_removed += 1
            os.remove(file_to_remove)

    for dir_to_remove in dirs_to_remove:
        dir_to_remove = f'E:\\Big downloads\\celeste\\{dir_to_remove}'

        if os.path.isdir(dir_to_remove):
            dirs_removed += 1
            shutil.rmtree(dir_to_remove)

    log.info(f"Deleted {files_removed} file{plural(files_removed)} and {dirs_removed} dir{plural(dirs_to_remove)} from game install")


log: Optional[logging.Logger] = None
client: Optional[discord.Client] = None


if __name__ == '__main__':
    run_syncs()
