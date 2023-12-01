import argparse
import base64
import functools
import io
import logging
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Optional

import psutil
import requests
import ujson
import yaml

import db
import main
import utils
import validation
from utils import plural


def run_syncs():
    global log
    log = main.create_logger('game_sync')
    parser = argparse.ArgumentParser()
    parser.add_argument('project', help="Only sync test a specific project (ID or name, use quotes if need be)", nargs='?')
    cli_project = parser.parse_args().project
    projects = db.projects.dict()

    if cli_project:
        if cli_project.isdigit():
            log.info(f"Running sync test for project ID {cli_project} only")
            test_project_ids = (int(cli_project),)
        else:
            test_project_ids = [int(p['project_id']) for p in db.projects.get_by_name_or_id(cli_project)]
            log.info(f"Running sync test for project ID{plural(test_project_ids)} {test_project_ids} only")
    else:
        log.info("Running all sync tests")
        test_project_ids = projects

    load_sid_caches(projects)

    try:
        for project_id in test_project_ids:
            project = projects[project_id]

            if db.projects.get(project_id)['do_run_validation'] and db.path_caches.get(project_id):
                sync_test(project)
    except Exception:
        utils.log_error()
        close_game()
        post_cleanup()
        raise

    post_cleanup()


def sync_test(project: dict):
    current_log = io.StringIO()
    stream_handler = logging.StreamHandler(current_log)
    stream_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s: %(message)s'))
    log.addHandler(stream_handler)

    log.info(f"Running sync test for project: {project['name']}")
    project_id = project['project_id']
    mods = project['mods']
    repo = project['repo']
    previous_desyncs = project['desyncs']
    desyncs = []
    mods_to_load = set(mods)
    files_timed = 0
    remove_save_files()
    queued_filetime_commits = []

    for mod in mods:
        mods_to_load = mods_to_load.union(get_mod_dependencies(mod))

    get_mod_everest_yaml.cache_clear()
    generate_blacklist(mods_to_load)
    log.info(f"Created blacklist, launching game with {len(mods_to_load)} mod{plural(mods_to_load)}")
    close_game()
    subprocess.Popen(f'{game_dir()}\\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits
    game_loaded = False
    last_game_loading_notify = time.perf_counter()
    everest_version = subprocess.check_output('mons show itch').decode('UTF8').partition('-')[2].partition('\r')[0]

    # make sure path cache is correct while the game is launching
    main.generate_request_headers(project['installation_owner'], 300)
    main.generate_path_cache(project_id)
    path_cache = db.path_caches.get(project_id)

    # clone repo
    repo_cloned = repo.partition('/')[2]
    repo_path = f'{game_dir()}\\repos\\{repo_cloned}'

    if not os.path.isdir(f'{game_dir()}\\repos'):
        os.mkdir(f'{game_dir()}\\repos')
    elif os.path.isdir(repo_path):
        shutil.rmtree(repo_path, onerror=del_rw)

    time.sleep(0.1)
    clone_time = int(time.time())
    cwd = os.getcwd()
    os.chdir(f'{game_dir()}\\repos')
    subprocess.run(f'git clone https://github.com/{repo} --recursive', capture_output=True)
    os.chdir(cwd)
    log.info(f"Cloned repo to {repo_path}")

    # add asserts for cached SIDs
    asserts_added = {}

    for tas_filename in path_cache:
        file_path_repo = path_cache[tas_filename]

        if file_path_repo in sid_caches[project_id]:
            with open(f'{repo_path}\\{file_path_repo}'.replace('/', '\\'), 'r+') as tas_file:
                tas_lines = tas_file.readlines()
                sid = sid_caches[project_id][file_path_repo]

                for tas_line in enumerate(tas_lines):
                    if tas_line[1].lower() == '#start\n':
                        assert_line = f'Assert,Equal,{sid},{{Session.Area.SID}}\n'
                        tas_lines.insert(tas_line[0] + 3, assert_line)
                        tas_file.seek(0)
                        tas_file.writelines(tas_lines)
                        asserts_added[file_path_repo] = assert_line
                        break

    if asserts_added:
        log.info(f"Added SID assertions to {len(asserts_added)} file{plural(asserts_added)}: {asserts_added}")

    # wait for the game to load (handles mods updating as well)
    while not game_loaded:
        try:
            time.sleep(2)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.Timeout:
            current_time = time.perf_counter()

            if current_time - last_game_loading_notify > 60:
                last_game_loading_notify = current_time
        else:
            game_loaded = True

    mod_versions_start_time = time.perf_counter()
    log.info(f"Game loaded, mod versions: {mod_versions(mods_to_load)}")
    log.info(f"Everest version: {everest_version}")
    time.sleep(max(0, 2 - (time.perf_counter() - mod_versions_start_time)))

    for process in psutil.process_iter(['name']):
        if process.name() == 'Celeste.exe':
            process.nice(psutil.HIGH_PRIORITY_CLASS)
            log.info("Set game process to high priority")
            break

    for tas_filename in path_cache:
        file_path_repo = path_cache[tas_filename]
        file_path_repo_backslashes = file_path_repo.replace('/', '\\')
        file_path = f'{repo_path}\\{file_path_repo_backslashes}'

        if 'lobby' in file_path_repo.lower() and 'lobby' not in tas_filename.lower():
            log.info(f"Skipping {tas_filename} (lobby)")
            continue
        elif tas_filename == 'translocation.tas':
            continue

        with open(file_path, 'r', encoding='UTF8') as tas_file:
            tas_lines = tas_file.readlines()

        # set up tas file
        tas_parsed = validation.parse_tas_file(tas_lines, False, False)

        if tas_parsed.found_finaltime:
            has_filetime = tas_lines[tas_parsed.finaltime_line_num].startswith('FileTime')

            if has_filetime:
                tas_lines_og = tas_lines.copy()
                tas_lines[tas_parsed.finaltime_line_num] = 'FileTime: \n'
                has_console_load = [line for line in tas_lines if line.startswith('console load')] != []

                if not has_console_load:
                    # if it starts from begin, then menu there
                    tas_lines[:0] = ['unsafe\n', 'console overworld\n', '2\n', '1,J\n', '94\n', '1,J\n', '56\n', 'Repeat 5\n', '1,D\n', '1,F,180\n', 'Endrepeat\n', '1,J\n',
                                     '14\n', '1,D\n', '1,F,180\n', '1,D\n', '1,F,180\n', '1,L\n', '1,U\n', '1,F,\n', '1,U\n', '1,F,\n']
            else:
                tas_lines[tas_parsed.finaltime_line_num] = 'ChapterTime: \n'
        else:
            log.info(f"{tas_filename} has no final time")
            continue

        tas_lines.insert(0, f'Set,CollabUtils2.DisplayEndScreenForAllMaps,{not has_filetime}\n')
        tas_lines.append('\n***')

        with open(file_path, 'w', encoding='UTF8') as tas_file:
            tas_file.truncate()
            tas_file.write(''.join(tas_lines))

        # now run it
        time.sleep(0.2)
        initial_mtime = os.path.getmtime(file_path)
        log.info(f"Sync checking {tas_filename} ({tas_parsed.finaltime_trimmed})")
        tas_started = False
        tas_finished = False
        sid = None

        while not tas_started:
            try:
                requests.post(f'http://localhost:32270/tas/playtas?filePath={file_path}', timeout=10)
                tas_started = True
            except (requests.Timeout, requests.ConnectionError):
                pass

        while not tas_finished:
            try:
                time.sleep(10 if has_filetime else 1)
                session_data = requests.get('http://localhost:32270/tas/info', timeout=2).text
            except (requests.Timeout, requests.ConnectionError):
                pass
            else:
                tas_finished = 'Running: False' in session_data

                if not has_filetime:
                    sid = session_data.partition('SID: ')[2].partition(' (')[0]

            if not tas_finished and not process.is_running():
                utils.log_error("Game crashed, abandoning game sync for project")
                return

        log.info("TAS has finished")
        time.sleep(8 if 'SID:  ()' in session_data else 3)
        extra_sleeps = 0

        while os.path.getmtime(file_path) == initial_mtime and extra_sleeps < 5:
            time.sleep(3 + (extra_sleeps ** 2))
            extra_sleeps += 1
            log.info(f"Extra sleeps: {extra_sleeps}")

        # determine if it synced or not
        with open(file_path, 'rb') as tas_file:
            tas_updated = validation.as_lines(tas_file.read())

        tas_parsed_new = validation.parse_tas_file(tas_updated, False, False)

        # clear debug save, for silvers
        if has_filetime:
            try:
                requests.post('http://localhost:32270/console?command=overworld', timeout=2)
                time.sleep(2)
                requests.post('http://localhost:32270/console?command=clrsav', timeout=2)
                time.sleep(2)
            except (requests.Timeout, requests.ConnectionError):
                pass

        if tas_parsed_new.found_finaltime:
            frame_diff = validation.calculate_time_difference(tas_parsed_new.finaltime, tas_parsed.finaltime)
            time_synced = frame_diff == 0

            if not has_filetime:
                if not tas_parsed_new.finaltime_frames:
                    utils.log_error(f"Couldn't parse FileTime frames for {file_path_repo}")
                    continue

                log_command = log.info if time_synced else log.warning
                time_delta = (f"{tas_parsed.finaltime_trimmed}({tas_parsed.finaltime_frames}) -> {tas_parsed_new.finaltime_trimmed}({tas_parsed_new.finaltime_frames}) "
                              f"({'+' if frame_diff > 0 else ''}{frame_diff}f)")
                log_command(f"{'Synced' if time_synced else 'Desynced'}: {time_delta}")

                if time_synced:
                    if file_path_repo not in sid_caches[project_id] and sid:
                        sid_caches[project_id][file_path_repo] = sid
                        save_sid_caches()
                        log.info(f"Cached SID for {file_path_repo}: {sid}")
                    elif not sid:
                        log.warning(f"Running {file_path_repo} yielded no SID")
                else:
                    desyncs.append((tas_filename, time_delta))
            else:
                log.info(f"Time: {tas_parsed_new.finaltime_trimmed}")
                project['filetimes'][tas_filename] = tas_parsed_new.finaltime_trimmed

                if not time_synced:
                    new_time_line = tas_updated[tas_parsed_new.finaltime_line_num]
                    tas_lines_og[tas_parsed.finaltime_line_num] = new_time_line
                    commit_message = f"{'+' if frame_diff > 0 else ''}{frame_diff}f {tas_filename} ({tas_parsed_new.finaltime_trimmed})"
                    queued_filetime_commits.append((file_path_repo, tas_lines_og, commit_message))
                    # don't commit now, since there may be desyncs
        else:
            log.warning(f"Desynced (no {'FileTime' if has_filetime else 'ChapterTime'})")
            log.info(session_data.partition('<pre>')[2].partition('</pre>')[0])
            desyncs.append((tas_filename, None))

        files_timed += 1

    close_game()
    project['last_run_validation'] = clone_time
    project['desyncs'] = [desync[0] for desync in desyncs]
    time_since_last_commit = clone_time - project['last_commit_time']
    new_desyncs = [d for d in desyncs if d[0] not in previous_desyncs]
    log.info(f"All desyncs: {desyncs}")
    log.info(f"New desyncs: {new_desyncs}")
    report_text = report_log = None

    if new_desyncs:
        new_desyncs_formatted = format_desyncs(new_desyncs)
        desyncs_formatted = format_desyncs(desyncs)
        desyncs_block = '' if desyncs == new_desyncs else f"\nAll desyncs:\n```\n{desyncs_formatted}```"
        report_text = f"Sync check finished, {len(new_desyncs)} new desync{plural(new_desyncs)} found ({files_timed} file{plural(files_timed)} tested):" \
                      f"\n```\n{new_desyncs_formatted}```{desyncs_block}"[:1900]
        stream_handler.flush()
        report_log = re_redact_token.sub("'token': [REDACTED]", current_log.getvalue())

    if time_since_last_commit > 1209600 and project['do_run_validation']:
        project['do_run_validation'] = False
        project['sync_check_timed_out'] = True
        log.warning(f"Disabled auto sync check after {time_since_last_commit} seconds of inactivity")
        report_text = ("Disabled nightly sync checking after two weeks of no improvements. If you would like to reenable it, rerun the `register_project` command. "
                       "Otherwise, it will be automatically reenabled on the next valid improvement/draft.")

    db.projects.set(project_id, project)
    db.sync_results.set(project_id, {'report_text': report_text, 'log': report_log})
    log.info("Wrote sync result to DB")

    # commit updated fullgame files
    for queued_commit in queued_filetime_commits:
        file_path_repo, lines, commit_message = queued_commit
        lines_joined = ''.join(lines)
        desyncs_found = [d for d in desyncs if d[0][:-4] in lines_joined]

        # but only if all the files in them sync
        if desyncs_found:
            log.info(f"Not committing updated fullgame file {file_path_repo} due to desyncs: {desyncs_found}")
            continue

        main.generate_request_headers(project['installation_owner'], 300)
        commit_data = {'content': base64.b64encode(lines_joined.encode('UTF8')).decode('UTF8'),
                       'sha': main.get_sha(repo, file_path_repo),
                       'message': commit_message}
        log.info(f"Committing updated fullgame file: \"{commit_data['message']}\"")
        r = requests.put(f'https://api.github.com/repos/{repo}/contents/{file_path_repo}', headers=main.headers, data=ujson.dumps(commit_data))
        utils.handle_potential_request_error(r, 200)
        commit_url = ujson.loads(r.content)['commit']['html_url']
        log.info(f"Successfully committed: {commit_url}")


def format_desyncs(desyncs: list) -> str:
    formatted = []

    for desync in desyncs:
        if desync[1]:
            formatted.append(f'{desync[0]}: {desync[1]}')
        else:
            formatted.append(desync[0])

    return '\n'.join(formatted)


def generate_blacklist(mods_to_load: set):
    installed_mods = [item for item in os.listdir(f'{game_dir()}\\Mods') if item.endswith('.zip')]
    blacklist = []

    for installed_mod in installed_mods:
        if installed_mod.removesuffix('.zip') not in mods_to_load and installed_mod not in ('CelesteTAS.zip', 'SpeedrunTool.zip', 'AltEnterFullscreen.zip'):
            blacklist.append(installed_mod)

    with open(f'{game_dir()}\\Mods\\blacklist.txt', 'w') as blacklist_txt:
        blacklist_txt.write("# This file has been created by the Improvements Tracker\n")
        blacklist_txt.write('\n'.join(blacklist))


# remove all files related to any save
def remove_save_files():
    saves_dir = f'{game_dir()}\\Saves'
    save_files = [f'{saves_dir}\\{file}' for file in os.listdir(saves_dir) if file.startswith('debug') or (file[0].isdigit() and file[0] != '0')]

    for save_file in save_files:
        os.remove(save_file)

    try:
        log.info(f"Removed {len(save_files)} save files")
    except AttributeError:
        pass


def post_cleanup():
    generate_blacklist(set())
    remove_save_files()


def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)


def close_game():
    closed = False

    try:
        # https://docs.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist
        processes = str(subprocess.check_output('tasklist /fi "STATUS eq running"')).split(r'\r\n')
    except subprocess.CalledProcessError:
        processes = []
        utils.log_error()

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
            except psutil.NoSuchProcess:
                utils.log_error()
        elif 'studio' in process_name.lower() and 'celeste' in process_name.lower():
            try:
                psutil.Process(process_pid).kill()
                log.info("Closed Studio")
            except psutil.NoSuchProcess:
                utils.log_error()

    if not closed:
        log.info("No running game to close")


# TODO: make recursive (if necessary)
def get_mod_dependencies(mod: str) -> list:
    everest_yaml = get_mod_everest_yaml(mod)

    if everest_yaml is None:
        return []
    else:
        return [d['Name'] for d in everest_yaml['Dependencies'] if d['Name'] != 'Everest']


@functools.cache
def get_mod_everest_yaml(mod: str) -> Optional[dict]:
    zip_path = mods_dir().joinpath(f'{mod}.zip')

    if not zip_path.is_file():
        return None

    with zipfile.ZipFile(zip_path) as mod_zip:
        yaml_name = None

        if zipfile.Path(mod_zip, 'everest.yaml').is_file():
            yaml_name = 'everest.yaml'
        elif zipfile.Path(mod_zip, 'everest.yml').is_file():
            yaml_name = 'everest.yml'

        if yaml_name:
            with mod_zip.open(yaml_name) as everest_yaml:
                return yaml.safe_load(everest_yaml)[0]
        else:
            return None


def mod_versions(mods: set) -> str:
    versions = []

    for mod in mods:
        everest_yaml = get_mod_everest_yaml(mod)
        versions.append(f"{mod} = {get_mod_everest_yaml(mod)['Version'] if everest_yaml else "UNKNOWN"}")

    return ", ".join(sorted(versions))


@functools.cache
def mods_dir() -> Path:
    mod_paths = (Path('G:/celeste/Mods'), Path('C:/Users/Administrator/Desktop/mods'), Path('C:/Users/Bob/Documents/Celeste Itch/Mods'))

    for mod_path in mod_paths:
        if mod_path.is_dir():
            return mod_path

    raise FileNotFoundError("ok where'd my mods go")


@functools.cache
def game_dir() -> Path:
    game_dirs = (Path('G:/celeste'), Path('C:/Users/Bob/Documents/Celeste Itch'))

    for possible_game_dir in game_dirs:
        if possible_game_dir.is_dir():
            return possible_game_dir


def load_sid_caches(projects: dict):
    global sid_caches
    added_key = False

    with open('sid_caches.json', 'r', encoding='UTF8') as sid_caches_file:
        sid_caches = ujson.load(sid_caches_file)
        sid_caches = {int(k): sid_caches[k] for k in sid_caches}

    for project_id in projects:
        if projects[project_id]['do_run_validation'] and project_id not in sid_caches:
            sid_caches[project_id] = {}
            added_key = True
            log.info(f"Added SID cache entry for project {projects[project_id]['name']}")

    if added_key:
        save_sid_caches()


def save_sid_caches():
    with open('sid_caches.json', 'w', encoding='UTF8') as sid_caches_file:
        ujson.dump(sid_caches, sid_caches_file, ensure_ascii=False, indent=4, escape_forward_slashes=False)


log: Optional[logging.Logger] = None
sid_caches: Optional[dict] = None
re_redact_token = re.compile(r"'token': '[^']*'")

if __name__ == '__main__':
    run_syncs()
