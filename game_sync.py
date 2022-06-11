import asyncio
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

    try:
        for project_id in projects:
            if projects[project_id]['do_run_validation'] and main.path_caches[project_id]:
                await sync_test(project_id)
    except Exception:
        await close_game()
        post_cleanup()
        raise

    post_cleanup()


async def sync_test(project_id: int, report_channel: Optional[discord.DMChannel] = None):
    project = projects[project_id]
    log.info(f"Running sync test for project: {project['name']}")
    installed_mods = [item for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]
    mods = project['mods']
    path_cache = main.path_caches[project_id]
    repo = project['repo']
    blacklist = []
    desyncs = []
    files_timed = 0
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
    last_game_loading_notify = time.perf_counter()
    await dm_report(report_channel, launching_game_text)

    # make sure path cache is correct while the game is launching
    main.generate_request_headers(project['installation_owner'])
    main.generate_path_cache(project_id)
    await dm_report(report_channel, "Generated repo structure cache")

    # wait for the game to load (handles mods updating as well)
    while not game_loaded:
        try:
            await asyncio.sleep(2)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.ConnectTimeout:
            current_time = time.perf_counter()

            if current_time - last_game_loading_notify > 60:
                await dm_report(report_channel, "Game is still loading")
                last_game_loading_notify = current_time
        else:
            log.info("Game loaded")
            await asyncio.sleep(2)
            game_loaded = True

    for tas_filename in path_cache:
        log.info(f"Downloading {path_cache[tas_filename]}")
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{path_cache[tas_filename]}', headers=main.headers)
        utils.handle_potential_request_error(r, 200)
        tas_read = base64.b64decode(r.json()['content'])

        # set up temp tas file
        tas_lines = validation.as_lines(tas_read)
        _, found_chaptertime, chapter_time, chapter_time_trimmed, chapter_time_line = validation.parse_tas_file(tas_lines, False, False)

        if not found_chaptertime:
            no_chaptertime_text = f"{tas_filename} has no ChapterTime"
            log.warning(no_chaptertime_text)
            await dm_report(report_channel, no_chaptertime_text)
            continue

        tas_lines[chapter_time_line] = 'ChapterTime: '
        tas_lines.append('***')

        with open(r'E:\Big downloads\celeste\temp.tas', 'w', encoding='UTF8') as temp_tas:
            temp_tas.write('\n'.join(tas_lines))

        # now run it
        testing_timing_text = f"Testing timing of {tas_filename} ({chapter_time_trimmed})"
        log.info(testing_timing_text)
        requests.post(r'http://localhost:32270/tas/playtas?filePath=E:\Big downloads\celeste\temp.tas')
        tas_finished = False
        await dm_report(report_channel, testing_timing_text)

        while not tas_finished:
            try:
                await asyncio.sleep(1)
                session_data = requests.get('http://localhost:32270/tas/info', timeout=2)
            except requests.Timeout:
                pass
            else:
                tas_finished = 'Running: False' in session_data.text

        log.info("TAS has finished")
        await asyncio.sleep(5)

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

        await dm_report(report_channel, sync_text)
        files_timed += 1

    await close_game(report_channel)
    project['last_run_validation'] = int(time.time())
    utils.save_projects()
    improvements_channel = client.get_channel(project_id)
    await main.edit_pin(improvements_channel)

    if desyncs:
        desyncs_formatted = '\n'.join(desyncs)
        desync_warning = f"Sync check finished, {len(desyncs)} desync{plural(desyncs)} found (of {files_timed} file{plural(files_timed)} tested):" \
                         f"\n```\n{desyncs_formatted}```"
        await improvements_channel.send(desync_warning)

    if report_channel:
        if desyncs:
            await report_channel.send(desync_warning)
        else:
            await report_channel.send(f"Sync check finished, 0 desyncs found (of {files_timed} file{plural(files_timed)} tested)")


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


async def close_game(report_channel: Optional[discord.DMChannel] = None):
    closed = False

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
                closed = True
            except psutil.NoSuchProcess as error:
                log.error(repr(error))
        elif 'studio' in process_name.lower() and 'celeste' in process_name.lower():
            try:
                psutil.Process(process_pid).kill()
                log.info("Closed Studio")
                closed = True
            except psutil.NoSuchProcess as error:
                log.error(repr(error))

    if closed:
        closed_game_text = "Closed the game and Studio"
        log.info(closed_game_text)
        await dm_report(report_channel, closed_game_text)
        await asyncio.sleep(1)
    else:
        game_not_closed_text = "Game was not running"
        log.info(game_not_closed_text)
        await dm_report(report_channel, game_not_closed_text)


async def dm_report(report_channel: Optional[discord.DMChannel], text: str):
    if report_channel:
        await report_channel.send(f"`{text}`")


log: Optional[logging.Logger] = None
client: Optional[discord.Client] = None


if __name__ == '__main__':
    run_syncs()
