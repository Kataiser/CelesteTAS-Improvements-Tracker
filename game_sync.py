import argparse
import base64
import functools
import logging
import os
import shutil
import subprocess
import time
import zipfile
from typing import Optional

import psutil
import requests
import ujson
import yaml

import main
import utils
import validation
from utils import plural


def run_syncs():
    global log
    log = main.create_loggers('game_sync.log')[0]
    parser = argparse.ArgumentParser()
    parser.add_argument('--project_id', type=int, help="Only sync test a specific project", required=False)
    cli_project_id = parser.parse_args().project_id

    if cli_project_id:
        log.info(f"Running sync test for project ID {cli_project_id} only")
        test_projects = (cli_project_id,)
    else:
        log.info("Running all sync tests")
        test_projects = reversed(main.projects)

    utils.projects = utils.load_projects()
    main.path_caches = utils.load_path_caches()
    results = {}

    try:
        for project_id in test_projects:
            if main.projects[project_id]['do_run_validation'] and main.path_caches[project_id]:
                results[project_id] = sync_test(project_id)
    except Exception as error:
        log.error(repr(error))
        close_game()
        post_cleanup()
        raise

    post_cleanup()

    if results:
        with open('sync\\game_sync_results.json', 'w', encoding='UTF8') as game_sync_results:
            ujson.dump(results, game_sync_results, indent=4)

        log.info("Created results file")
    else:
        log.info("Didn't create results file")


def sync_test(project_id: int) -> Optional[str]:
    project = main.projects[project_id]
    log.info(f"Running sync test for project: {project['name']}")
    mods = project['mods']
    repo = project['repo']
    previous_desyncs = project['desyncs']
    desyncs = []
    mods_to_load = set(mods)
    files_timed = 0
    remove_debug_save_files()

    for mod in mods:
        mods_to_load = mods_to_load.union(get_mod_dependencies(mod))

    generate_blacklist(mods_to_load)
    log.info(f"Created blacklist, launching game with {len(mods_to_load)} mod{plural(mods_to_load)}")
    subprocess.Popen(r'E:\Big downloads\celeste\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits
    game_loaded = False
    last_game_loading_notify = time.perf_counter()

    # make sure path cache is correct while the game is launching
    main.generate_request_headers(project['installation_owner'], 300)
    main.generate_path_cache(project_id)
    path_cache = main.path_caches[project_id]

    # wait for the game to load (handles mods updating as well)
    while not game_loaded:
        try:
            time.sleep(2)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.ConnectTimeout:
            current_time = time.perf_counter()

            if current_time - last_game_loading_notify > 60:
                last_game_loading_notify = current_time
        else:
            log.info("Game loaded")
            time.sleep(2)
            game_loaded = True

    for process in psutil.process_iter(['name']):
        if process.name() == 'Celeste.exe':
            process.nice(psutil.HIGH_PRIORITY_CLASS)
            log.info("Set game process to high priority")
            break

    for tas_filename in path_cache:
        if 'lobby' in tas_filename.lower():
            log.info(f"Skipping {tas_filename} (lobby)")
            continue
        elif tas_filename == 'translocation.tas':
            files_timed += 1
            continue

        log.info(f"Downloading {path_cache[tas_filename]}")

        try:
            r = requests.get(f'https://api.github.com/repos/{repo}/contents/{path_cache[tas_filename]}', headers=main.headers)
        except requests.Timeout as error:
            log.error(f"Skipping {tas_filename}: {repr(error)}")
            continue

        utils.handle_potential_request_error(r, 200)
        tas_read = base64.b64decode(ujson.loads(r.content)['content'])

        # set up temp tas file
        tas_lines = validation.as_lines(tas_read)
        _, found_chaptertime, chapter_time, chapter_time_trimmed, chapter_time_line = validation.parse_tas_file(tas_lines, False, False)

        if not found_chaptertime:
            log.info(f"{tas_filename} has no ChapterTime")
            continue

        tas_lines[chapter_time_line] = 'ChapterTime: '
        tas_lines.append('***')

        with open(r'E:\Big downloads\celeste\temp.tas', 'w', encoding='UTF8') as temp_tas:
            temp_tas.write('\n'.join(tas_lines))

        # now run it
        log.info(f"Testing timing of {tas_filename} ({chapter_time_trimmed})")
        requests.post(r'http://localhost:32270/tas/playtas?filePath=E:\Big downloads\celeste\temp.tas')
        tas_finished = False

        while not tas_finished:
            try:
                time.sleep(1)
                session_data = requests.get('http://localhost:32270/tas/info', timeout=2)
            except requests.Timeout:
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
            log_command = log.info if synced else log.warning
            log_command(f"{'Synced' if synced else 'Desynced'}: {chapter_time_trimmed} -> {chapter_time_new_trimmed} ({'+' if frame_diff > 0 else ''}{frame_diff}f)")

            if not synced:
                desyncs.append(tas_filename)
        else:
            log.warning("Desynced (no ChapterTime)")
            desyncs.append(tas_filename)

        files_timed += 1

    close_game()
    current_time = int(time.time())
    project['last_run_validation'] = current_time
    project['desyncs'] = desyncs
    time_since_last_commit = current_time - project['last_commit_time']
    new_desyncs = [f for f in desyncs if f not in previous_desyncs]
    log.info(f"All desyncs: {desyncs}")
    log.info(f"New desyncs: {new_desyncs}")
    report_text = None

    if new_desyncs:
        new_desyncs_formatted = '\n'.join(new_desyncs)
        report_text = f"Sync check finished, {len(new_desyncs)} new desync{plural(new_desyncs)} found ({files_timed} file{plural(files_timed)} tested):\n```\n{new_desyncs_formatted}```"

    if time_since_last_commit > 2600000 and project['do_run_validation']:
        project['do_run_validation'] = False
        log.warning(f"Disabled auto sync check after {time_since_last_commit} seconds of inactivity")
        report_text = "Disabled nightly sync checking after a month of no improvements."

    main.projects[project_id] = project  # yes this is dumb
    utils.save_projects()
    return report_text


def generate_blacklist(mods_to_load: set):
    installed_mods = [item for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]
    blacklist = []

    for installed_mod in installed_mods:
        if installed_mod.removesuffix('.zip') not in mods_to_load and installed_mod not in ('CelesteTAS.zip', 'SpeedrunTool.zip'):
            blacklist.append(installed_mod)

    with open(r'E:\Big downloads\celeste\Mods\blacklist.txt', 'w') as blacklist_txt:
        blacklist_txt.write("# This file has been created by the Improvements Tracker\n")
        blacklist_txt.write('\n'.join(blacklist))


# remove all files related to the debug save
def remove_debug_save_files():
    debug_save_files = [file for file in os.listdir(r'E:\Big downloads\celeste\Saves') if file.startswith('debug')]

    for debug_save_file in debug_save_files:
        os.remove(f'E:\\Big downloads\\celeste\\Saves\\{debug_save_file}')

    log.info(f"Removed {len(debug_save_files)} debug save files")


def post_cleanup():
    generate_blacklist(set())
    remove_debug_save_files()
    files_to_remove = ['log.txt', 'temp.tas']
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


def close_game():
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

    if not closed:
        log.info("Game was not running")


# TODO: make recursive (if necessary)
def get_mod_dependencies(mod: str) -> list:
    zip_path = f'{mods_dir()}\\{mod}.zip'

    if not os.path.isfile(zip_path):
        return []

    with zipfile.ZipFile(zip_path) as mod_zip:
        if zipfile.Path(mod_zip, 'everest.yaml').is_file():
            with mod_zip.open('everest.yaml') as everest_yaml:
                mod_everest = yaml.safe_load(everest_yaml)
        else:
            return []

    return [d['Name'] for d in mod_everest[0]['Dependencies'] if d['Name'] != 'Everest']


@functools.cache
def mods_dir() -> str:
    pc_path = r'E:\Big downloads\celeste\Mods'
    aws_path = r'C:\Users\Administrator\Desktop\mods'

    if os.path.isdir(pc_path):
        return pc_path
    elif os.path.isdir(aws_path):
        return aws_path
    else:
        raise FileNotFoundError("ok where'd my mods go")


log: Optional[logging.Logger] = None

if __name__ == '__main__':
    run_syncs()
