import argparse
import base64
import datetime
import functools
import gzip
import io
import logging
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
import zlib
from pathlib import Path
from typing import Optional, Union

import dateutil.parser
import orjson
import pydantic
import requests
from deepdiff import DeepDiff

import db
import gen_token
import main
import utils
import validation
from utils import plural


def run_syncs():
    global log, game_sync_hash
    start_time = time.time()
    log = main.create_logger('game_sync')
    parser = argparse.ArgumentParser()
    parser.add_argument('project', help="Only sync test a specific project (ID or name, use quotes if need be)", nargs='?')
    parser.add_argument('--all', action='store_true', help="Run all sync checks", default=False)
    parser.add_argument('--safe', action='store_true', help="Disable database writes", default=False)
    cli_project = parser.parse_args().project
    force_run_all = parser.parse_args().all
    db.writes_enabled = not parser.parse_args().safe

    with open('game_sync.py', 'rb') as game_sync_py:
        game_sync_hash = zlib.adler32(game_sync_py.read())

    if cli_project:
        if cli_project.isdigit():
            log.info(f"Running sync test for project ID {cli_project} only")
            test_project_ids = (int(cli_project),)
        else:
            test_project_ids = [int(p['project_id']) for p in db.projects.get_by_name_or_id(cli_project)]
            log.info(f"Running sync test for project ID{plural(test_project_ids)} {test_project_ids} only")
    else:
        projects = db.projects.dict()
        test_project_ids = []

        for project_id in sorted(projects, key=lambda x: projects[x]['last_commit_time'], reverse=True):
            project = projects[project_id]

            if project['do_run_validation'] and db.path_caches.get(project_id):
                test_project_ids.append(project_id)

        all_sync_tests = {project_id: projects[project_id]['name'] for project_id in test_project_ids}
        log.info(f"Running all sync tests: {all_sync_tests} (forcing all: {force_run_all})")

    try:
        for project_id in test_project_ids:
            sync_test(project_id, cli_project or force_run_all)
    except Exception:
        log_error()
        close_game()
        post_cleanup()
        raise

    post_cleanup()
    log.info(f"All sync checks time: {format_elapsed_time(start_time)}")


class Config(pydantic.BaseModel):
    gameDirectory: Path
    everestBranch: str = 'manual'
    mods: set[str] = set()
    blacklistedMods: list[str] = []
    files: list[str] = []

    @staticmethod
    def path() -> Path:
        return game_dir() / 'SyncChecker/config.json'

    def save(self):
        with open(self.path(), 'w') as config_file:
            config_file.write(self.model_dump_json(indent=4))

        log.info(f"Saved config to {self.path()}")


class AbortInfo(pydantic.BaseModel):
    FilePath: str | None
    FileLine: int | None
    CurrentInput: str | None


class CrashInfo(pydantic.BaseModel):
    FilePath: str | None
    FileLine: int | None
    Error: str


class WrongTimeInfo(pydantic.BaseModel):
    FilePath: str | None
    FileLine: int | None
    OldTime: str
    NewTime: str


class AssertFailedInfo(pydantic.BaseModel):
    FilePath: str | None
    FileLine: int | None
    Actual: str
    Expected: str


class ResultEntryAdditionalInfo(pydantic.BaseModel):
    abort: AbortInfo | None
    crash: CrashInfo | None
    wrongTime: list[WrongTimeInfo] | None
    assertFailed: AssertFailedInfo | None


class ResultEntry(pydantic.BaseModel):
    file: str
    status: str
    gameInfo: str
    additionalInfo: ResultEntryAdditionalInfo


class Result(pydantic.BaseModel):
    startTime: datetime.datetime
    endTime: datetime.datetime
    entries: list[ResultEntry]
    checksum: str

    @staticmethod
    def path() -> Path:
        return game_dir() / 'SyncChecker/result.json'


def sync_test(project_id: int, force: bool):
    start_time = time.time()
    current_log = io.StringIO()
    stream_handler = logging.StreamHandler(current_log)
    stream_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s: %(message)s'))
    log.addHandler(stream_handler)

    project = db.projects.get(project_id)

    if not project['do_run_validation'] and not force:
        log.info(f"Abandoning sync test for project \"{project['name']}\" due to it now being disabled")
        consider_disabling_after_inactivity(project, time.time(), True)
        return

    log.info(f"Running sync test for project: {project['name']}")
    mods = project['mods']
    repo = project['repo']
    previous_desyncs = project['desyncs']
    prev_environment_state = project['sync_environment_state']
    filetimes = {}
    desyncs = []
    mods_to_load = set(mods)
    mods_to_load |= {'CelesteTAS', 'SpeedrunTool'}
    files_timed = 0
    remove_save_files()
    queued_update_commits = []
    crash_logs_data = {}
    crash_logs_dir = f'{game_dir()}\\CrashLogs'
    project_is_maingame = project_id == 598945702554501130
    get_mod_dependencies.cache_clear()
    log.info(f"Previous desyncs: {previous_desyncs}")

    for mod in mods:
        mods_to_load |= get_mod_dependencies(mod)

    main.generate_request_headers(project['installation_owner'], 300)
    environment_state = generate_environment_state(project, mods_to_load)

    if environment_state['last_commit_time'] > project['last_commit_time']:
        log.info(f"Last repo commit time is later than improvement channel post ({environment_state['last_commit_time']} > {int(project['last_commit_time'])}), updating project")
        project['last_commit_time'] = environment_state['last_commit_time']
        db.projects.set(project_id, project)

    if environment_state == prev_environment_state and not force:
        log.info(f"Abandoning sync test for project \"{project['name']}\" due to environment state matching previous run")
        consider_disabling_after_inactivity(project, time.time(), True)
        return

    log.info(f"Environment state changes: {DeepDiff(prev_environment_state, environment_state, ignore_order=True, ignore_numeric_type_changes=True, verbose_level=2)}")
    get_mod_everest_yaml.cache_clear()
    generate_blacklist(mods_to_load)
    log.info(f"Created blacklist, loading {len(mods_to_load)} mod{plural(mods_to_load)}")

    # make sure path cache is correct
    path_cache = main.generate_path_cache(project_id)

    if not path_cache and not force:
        log.info(f"Abandoning sync test due to path cache now being empty")
        consider_disabling_after_inactivity(project, time.time(), True)
        close_game()
        return

    # clone repo
    clone_time, repo_path = clone_repo(repo)
    asserts_added = {}
    sid_cache_files_removed = []
    og_tas_lines = {}

    # add asserts for cached SIDs
    try:
        sid_cache = db.sid_caches.get(project_id)
        log.info(f'Loaded {len(sid_cache)} cached SIDs')
    except db.DBKeyError:
        sid_cache = {}
        log.info("Created SID cache entry")

    for tas_filename in path_cache:
        file_path_repo = path_cache[tas_filename]

        if file_path_repo in sid_cache:
            sid = sid_cache[file_path_repo]

            if not sid_is_valid(sid):
                sid_cache_files_removed.append(file_path_repo)
                continue

            with open(f'{repo_path}\\{file_path_repo}'.replace('/', '\\'), 'r+') as tas_file:
                tas_lines = tas_file.readlines()
                og_tas_lines[tas_filename] = tas_lines.copy()

                for tas_line in enumerate(tas_lines):
                    if tas_line[1].lower() == '#start\n':
                        assert_line = f'Assert,Equal,{sid},{{Level.Session.Area.SID}}\n'
                        assert_line_num = tas_line[0] + 3
                        tas_lines.insert(assert_line_num, assert_line)
                        tas_file.seek(0)
                        tas_file.writelines(tas_lines)
                        asserts_added[file_path_repo] = (assert_line_num, assert_line)
                        break

    if asserts_added:
        log.info(f"Added SID assertions to {len(asserts_added)} file{plural(asserts_added)}: {asserts_added}")

    if sid_cache_files_removed:
        for removed_file in sid_cache_files_removed:
            del sid_cache[removed_file]

        db.sid_caches.set(project_id, sid_cache)
        log.warning(f"Removed {len(sid_cache_files_removed)} invalid cached SIDs: {sid_cache_files_removed}")

    global sleep_scale
    host_sleep_scale = utils.host().sleep_scale

    if host_sleep_scale:
        log.info(f"Sleep scale will be {host_sleep_scale}")
        sleep_scale = host_sleep_scale
    else:
        sleep_scale_default = 0.75
        log.warning(f"Sleep scale defaulting to {sleep_scale_default}")
        sleep_scale = sleep_scale_default

    del path_cache['0 - 202 Berries.tas']
    file_paths = [f'{repo_path}\\{path_cache[tas].replace('/', '\\')}' for tas in path_cache]
    sync_checker_config = Config(gameDirectory=game_dir(), mods=mods_to_load, files=file_paths)
    sync_checker_config.save()
    close_game()
    subprocess.run(f'dotnet {game_dir() / 'SyncChecker/SyncChecker.dll'} {Config.path()} {Result.path()}')









    for tas_filename in path_cache:
        file_path_repo = path_cache[tas_filename]
        file_path_repo_backslashes = file_path_repo.replace('/', '\\')
        file_path = f'{repo_path}\\{file_path_repo_backslashes}'

        if 'lobby' in file_path_repo.lower() and 'lobby' not in tas_filename.lower():
            log.info(f"Skipping {tas_filename} (lobby)")
            continue
        elif tas_filename in ('translocation.tas', 'mt_celeste_jazz_club.tas'):
            continue

        with open(file_path, 'rb') as tas_file:
            tas_file_raw = tas_file.read()

        # set up tas file
        tas_lines = tas_file_raw.replace(b'\r\n', b'\n').decode('UTF8').splitlines(keepends=True)
        tas_parsed = validation.parse_tas_file(tas_lines, False, False)

        if tas_filename not in og_tas_lines:
            og_tas_lines[tas_filename] = tas_lines.copy()

        if tas_parsed.found_finaltime:
            finaltime_line_lower = tas_lines[tas_parsed.finaltime_line_num].lower()
            has_filetime = finaltime_line_lower.startswith('filetime')
            finaltime_is_midway = finaltime_line_lower.startswith('midway')
            finaltime_line_blank = f'{tas_lines[tas_parsed.finaltime_line_num].partition(' ')[0]} \n'
            tas_lines[tas_parsed.finaltime_line_num] = finaltime_line_blank

            if has_filetime or finaltime_is_midway:
                clear_debug_save()

            if has_filetime:
                has_console_load = [line for line in tas_lines if line.startswith('console load')] != []

                if not has_console_load:
                    # if it starts from begin, then menu there. doesn't change mod
                    tas_lines[:0] = ['unsafe\n', 'console overworld\n', '2\n', '1,J\n', '94\n', '1,J\n', '56\n', 'Repeat 20\n', '1,D\n', '1,F,180\n', 'Endrepeat\n', '1,J\n', '14\n']
        else:
            log.info(f"{tas_filename} has no final time")
            continue

        tas_lines.insert(0, f'Set,CollabUtils2.DisplayEndScreenForAllMaps,{not has_filetime}\n')
        tas_lines.append('\n***')

        with open(file_path, 'w', encoding='UTF8') as tas_file:
            tas_file.truncate()
            tas_file.write(''.join(tas_lines))

        # now run it
        time.sleep(0.5)
        initial_mtime = os.path.getmtime(file_path)
        file_sync_start_time = time.time()
        log.info(f"Sync checking {tas_filename} ({tas_parsed.finaltime_trimmed})")
        tas_started = False
        tas_finished = False
        sid = None
        game_crashed = False

        while not tas_started and not game_crashed:
            try:
                requests.post(f'http://localhost:32270/tas/playtas?filePath={file_path}', timeout=10)
            except requests.RequestException:
                if not game_process.is_running():
                    game_crashed = True
            else:
                crash_logs = os.listdir(crash_logs_dir)
                tas_started = True

        while not tas_finished and not game_crashed:
            if time.time() - file_sync_start_time > 3600 * 5:
                raise TimeoutError(f"File {tas_filename} in project {project['name']} has frozen after five hours")

            try:
                scaled_sleep(20 if has_filetime else 5)
                session_data = requests.get('http://localhost:32270/tas/info?forceAllowCodeExecution=true', timeout=2).text
            except requests.RequestException:
                if not game_process.is_running():
                    game_crashed = True
            else:
                tas_running = 'Running: True' in session_data
                session_current_frames = session_data.partition('CurrentFrame: ')[2].partition('<')[0]
                session_total_frames = session_data.partition('TotalFrames: ')[2].partition('<')[0]
                tas_finished = not tas_running or session_current_frames == session_total_frames

                if not has_filetime:
                    sid = session_data.partition('SID: ')[2].partition(' (')[0]

        log.info("TAS has finished")
        files_timed += 1
        scaled_sleep(15 if has_filetime or 'SID:  ()' in session_data else 5)
        extra_sleeps = 0

        while not game_crashed and os.path.getmtime(file_path) == initial_mtime and extra_sleeps < 5:
            time.sleep(3 + (extra_sleeps ** 2))
            extra_sleeps += 1
            log.info(f"Extra sleeps: {extra_sleeps}")

        updated_crash_logs = os.listdir(crash_logs_dir)

        if game_crashed or len(updated_crash_logs) > len(crash_logs):
            new_crash_logs = [file for file in updated_crash_logs if file not in crash_logs]
            log.warning(f"Game crashed ({new_crash_logs}), restarting and continuing")
            desyncs.append((tas_filename, "Crashed game"))
            scaled_sleep(10)
            close_game()
            scaled_sleep(5)
            start_game()

            for new_crash_log_name in new_crash_logs:
                with open(f'{crash_logs_dir}\\{new_crash_log_name}', 'rb') as new_crash_log:
                    crash_logs_data[f'{new_crash_log_name}.gz'] = b64encode(gzip.compress(new_crash_log.read()))

            game_process = wait_for_game_load(mods_to_load, project['name'])
            continue

        # determine if it synced or not
        with open(file_path, 'rb') as tas_file:
            tas_updated = validation.as_lines(tas_file.read())

        tas_parsed_new = validation.parse_tas_file(tas_updated, False, False, tas_parsed.finaltime_type)

        # for silvers
        if has_filetime:  # or tas_lines[tas_parsed.finaltime_line_num].lower().startswith('midway'):
            clear_debug_save()

        if not tas_parsed_new.found_finaltime:
            log.warning(f"Desynced (no {finaltime_line_blank.partition(':')[0]})")
            log.info(session_data.partition('<pre>')[2].partition('</pre>')[0])
            desyncs.append((tas_filename, None))
            continue

        frame_diff = validation.calculate_time_difference(tas_parsed_new.finaltime, tas_parsed.finaltime)
        time_synced = frame_diff == 0

        if has_filetime or project_is_maingame:
            log.info(f"Time: {tas_parsed_new.finaltime_trimmed}")

            if has_filetime:
                filetimes[tas_filename] = tas_parsed_new.finaltime_trimmed

            if not time_synced:
                new_time_line = tas_updated[tas_parsed_new.finaltime_line_num]
                tas_lines_og = og_tas_lines[tas_filename]
                tas_lines_og[tas_parsed.finaltime_line_num] = f'{new_time_line}\n'
                commit_message = f"{'+' if frame_diff > 0 else ''}{frame_diff}f {tas_filename} ({tas_parsed_new.finaltime_trimmed})"
                queued_update_commits.append((file_path, tas_lines_og, tas_file_raw, commit_message))
                # don't commit now, since there may be desyncs
        else:
            if not tas_parsed_new.finaltime_frames:
                log_error(f"Couldn't parse FileTime frames for {file_path_repo}")
                continue

            log_command = log.info if time_synced else log.warning
            time_delta = (f"{tas_parsed.finaltime_trimmed}({tas_parsed.finaltime_frames}) -> {tas_parsed_new.finaltime_trimmed}({tas_parsed_new.finaltime_frames}) "
                          f"({'+' if frame_diff > 0 else ''}{frame_diff}f)")
            log_command(f"{'Synced' if time_synced else 'Desynced'}: {time_delta}")

            if time_synced:
                if file_path_repo not in sid_cache and sid_is_valid(sid):
                    sid_cache[file_path_repo] = sid
                    db.sid_caches.set(project_id, sid_cache)
                    log.info(f"Cached SID for {file_path_repo}: {sid}")
                elif not sid:
                    log.warning(f"Running {file_path_repo} yielded no SID")
            else:
                desyncs.append((tas_filename, time_delta))

    close_game()
    project = db.projects.get(project_id)  # update this, in case it has changed since starting
    project['sync_environment_state'] = environment_state
    project['filetimes'] = filetimes
    project['last_run_validation'] = int(clone_time)
    project['desyncs'] = [desync[0] for desync in desyncs]
    new_desyncs = [d for d in desyncs if d[0] not in previous_desyncs]
    log.info(f"All desyncs: {desyncs}")
    log.info(f"New desyncs: {new_desyncs}")
    report_text = report_log = None

    if new_desyncs:
        new_desyncs_formatted = format_desyncs(new_desyncs)
        desyncs_formatted = format_desyncs(desyncs)
        desyncs_block = '' if desyncs == new_desyncs else f"\nAll desyncs:\n```\n{desyncs_formatted}```"
        report_text = f"Sync check found {len(new_desyncs)} new desync{plural(new_desyncs)} ({files_timed} file{plural(files_timed)} tested):" \
                      f"\n```\n{new_desyncs_formatted}```{desyncs_block}"[:1900]
        stream_handler.flush()
        report_log = b64encode(gzip.compress(re_redact_token.sub("'token': [REDACTED]", current_log.getvalue()).encode('UTF8')))

    disabled_text = consider_disabling_after_inactivity(project, clone_time, False)
    db.projects.set(project_id, project)
    crash_logs_data_report = crash_logs_data if report_text else {}
    db.send_sync_result(db.SyncResultType.NORMAL, {'project_id': project_id, 'report_text': report_text, 'disabled_text': disabled_text,
                                                   'log': report_log, 'crash_logs': crash_logs_data_report})
    log.info("Wrote sync result to DB")
    update_commit_files_changed = 0
    update_commit_single_commit_message = None

    # commit updated fullgame files
    if queued_update_commits:
        log.info(f"Potentially committing updated fullgame files: \"{[i[0] for i in queued_update_commits]}\"")
        clone_repo(repo, gen_token.access_token(project['installation_owner'], 300))

    for queued_commit in queued_update_commits:
        file_path, lines, raw_file, commit_message = queued_commit
        lines_joined = ''.join(lines)
        desyncs_found = [d for d in desyncs if d[0][:-4] in lines_joined]

        # but only if all the files in them sync
        if desyncs_found:
            log.info(f"Not committing updated fullgame file {file_path} due to desyncs: {desyncs_found}")
            continue

        log.info(f"Preparing to commit updated fullgame file: \"{commit_message}\"")
        lines_encoded = main.convert_line_endings(lines_joined.encode('UTF8'), raw_file)
        update_commit_files_changed += 1
        update_commit_single_commit_message = commit_message

        with open(file_path, 'wb') as tas_file:
            tas_file.truncate()
            tas_file.write(lines_encoded)

    if update_commit_files_changed:
        if update_commit_files_changed == 1:
            commit_message = update_commit_single_commit_message
        else:
            commit_messages = [queued_commit[3] for queued_commit in queued_update_commits]
            commit_message = f"Updated {update_commit_files_changed} fullgame files\n\n{'\n'.join(commit_messages)}"

        log.info("Committing updated fullgame file(s)")
        time.sleep(0.2)
        base_dir = os.getcwd()
        os.chdir(repo_path)
        subprocess.run('git add .')
        subprocess.run('git -c "user.name=celestetas-improvements-tracker[bot]" -c "user.email=104732884+celestetas-improvements-tracker[bot]@users.noreply.github.com" '
                       f'commit -m "{commit_message}"')
        subprocess.run('git push')
        commit_sha = subprocess.check_output('git rev-parse HEAD', encoding='UTF8').strip()
        os.chdir(base_dir)
        commit_url = f'https://github.com/{repo}/commit/{commit_sha}'
        log.info(f"Successfully committed: {commit_url}")

        if project_is_maingame:
            db.send_sync_result(db.SyncResultType.MAINGAME_COMMIT, {'maingame_message': f"Committed `{commit_message}` <{commit_url}>"})

    log.info(f"Sync check time: {format_elapsed_time(start_time)}")


def clone_repo(repo: str, access_token: str | None = None):
    repo_cloned = repo.partition('/')[2]
    repo_path = f'{game_dir()}\\repos\\{repo_cloned}'

    if not os.path.isdir(f'{game_dir()}\\repos'):
        os.mkdir(f'{game_dir()}\\repos')
    elif os.path.isdir(repo_path):
        shutil.rmtree(repo_path, onexc=del_rw)

    time.sleep(0.1)
    cwd = os.getcwd()
    os.chdir(f'{game_dir()}\\repos')
    clone_time = time.time()

    if access_token:
        clone_url = f'https://x-access-token:{access_token}@github.com/{repo}'
    else:
        clone_url = f'https://github.com/{repo}'

    subprocess.run(f'git clone --depth=1 --recursive {clone_url}', capture_output=True)
    os.chdir(cwd)
    log.info(f"Cloned repo to {repo_path}")
    return clone_time, repo_path


def clear_debug_save():
    try:
        requests.post('http://localhost:32270/console?command=overworld', timeout=10)
        scaled_sleep(4)
        requests.post('http://localhost:32270/console?command=clrsav', timeout=10)
        scaled_sleep(4)
        log.info("Cleared debug save")
        scaled_sleep(4)
    except (requests.Timeout, requests.ConnectionError):
        pass


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
        if installed_mod.removesuffix('.zip') not in mods_to_load:
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


def del_rw(function, path, excinfo):
    os.chmod(path, stat.S_IWRITE)
    os.remove(path)


def start_game():
    subprocess.Popen(f'{game_dir()}\\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits


def wait_for_game_load(mods: set, project_name: str):
    import psutil
    game_loaded = False
    last_game_loading_notify = time.perf_counter()
    wait_start_time = time.time()

    while not game_loaded:
        try:
            scaled_sleep(5)
            requests.get('http://localhost:32270/', timeout=2)
        except requests.RequestException:
            current_time = time.perf_counter()

            if current_time - last_game_loading_notify > 60:
                last_game_loading_notify = current_time

            if time.time() - wait_start_time > 3600:
                raise TimeoutError(f"Game failed to load after an hour for project {project_name}")
        else:
            game_loaded = True

    mod_versions_start_time = time.perf_counter()
    scaled_sleep(5)
    log.info(f"Game loaded, mod versions: {mod_versions(mods)}")
    scaled_sleep(max(0, 10 - (time.perf_counter() - mod_versions_start_time)))

    for process in psutil.process_iter(['name']):
        if process.name() == 'Celeste.exe':
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            log.info("Set game process to low priority")
            return process


def close_game():
    import psutil
    closed = False

    try:
        # https://docs.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist
        processes = str(subprocess.check_output('tasklist /fi "STATUS eq running"')).split(r'\r\n')
    except subprocess.CalledProcessError:
        processes = []
        log_error()

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
                log_error()
        elif 'studio' in process_name.lower() and 'celeste' in process_name.lower():
            try:
                psutil.Process(process_pid).kill()
                log.info("Closed Studio")
            except psutil.NoSuchProcess:
                log_error()

    if not closed:
        log.info("No running game to close")


@functools.cache
def get_mod_dependencies(mod: str) -> set:
    everest_yaml = get_mod_everest_yaml(mod)
    dependencies = set()

    if everest_yaml and 'Dependencies' in everest_yaml:
        for dependency in everest_yaml['Dependencies']:
            if dependency['Name'] in ('Everest', 'EverestCore', 'Celeste'):
                continue

            dependencies.add(dependency['Name'])
            dependencies |= get_mod_dependencies(dependency['Name'])

    return dependencies


@functools.cache
def get_mod_everest_yaml(mod: str, zip_path: Optional[Path] = None) -> Optional[dict]:
    if not zip_path:
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
                import yaml
                return yaml.safe_load(everest_yaml)[0]
        else:
            return None


def mod_versions(mods: set) -> str:
    versions = []

    for mod in mods:
        everest_yaml = get_mod_everest_yaml(mod)
        versions.append(f"{mod} = {everest_yaml['Version'] if everest_yaml else "UNKNOWN"}")

    return ", ".join(sorted(versions))


def generate_environment_state(project: dict, mods: set) -> dict:
    log.info("Generating environment state")
    state = {'host': utils.cached_hostname(), 'last_commit_time': 0, 'everest_version': None, 'mod_versions': {}, 'game_sync_hash': game_sync_hash,
             'excluded_items': project['excluded_items'], 'installation_owner': project['installation_owner'], 'is_lobby': project['is_lobby'], 'repo': project['repo'],
             'subdir': project['subdir'], 'sid_caches_exist': True}

    try:
        r_commits = requests.get(f'https://api.github.com/repos/{project['repo']}/commits', headers=main.headers, params={'per_page': 1}, timeout=10)
        utils.handle_potential_request_error(r_commits, 200)
    except requests.RequestException:
        log_error()
        return project['sync_environment_state']

    if r_commits.status_code == 200:
        commit = orjson.loads(r_commits.content)
        state['last_commit_time'] = int(dateutil.parser.parse(commit[0]['commit']['author']['date']).timestamp())

    gb_mods = gb_mod_versions()

    if not gb_mods:
        gb_mod_versions.cache_clear()

    for mod in mods:
        if mod in gb_mods:
            mod_gb = gb_mods[mod]
        else:
            mod_spaces = mod.replace('_', ' ')

            if mod_spaces in gb_mods:
                mod_gb = gb_mods[mod_spaces]
            else:
                mod_gb = gb_mods[mod]

        state['mod_versions'][mod] = mod_gb['Version']

    try:
        db.sid_caches.get(project['project_id'], consistent_read=False)
    except db.DBKeyError:
        state['sid_caches_exist'] = False

    log.info(f"Done: {state}")
    assert len(state['mod_versions']) == len(mods)
    return state


def consider_disabling_after_inactivity(project: dict, reference_time: Union[int, float], from_abandoned: bool) -> Optional[str]:
    time_since_last_commit = int(reference_time) - int(project['last_commit_time'])
    disabled_text = ("Disabled sync checking after a month of no improvements. If you would like to reenable it, use the `/edit_project` command. "
                     "Otherwise, it will be automatically reenabled on the next valid improvement/draft.")

    if time_since_last_commit > 2629800 and project['do_run_validation'] and project['project_id'] != 598945702554501130:
        project['do_run_validation'] = False
        project['sync_check_timed_out'] = True
        log.warning(f"Disabled auto sync check after {time_since_last_commit} seconds of inactivity")

        if from_abandoned:
            db.projects.set(project['project_id'], project)
            db.send_sync_result(db.SyncResultType.AUTO_DISABLE, {'project_id': int(project['project_id']), 'disabled_text': disabled_text})
        else:
            # don't need to return projects since it's mutable
            return disabled_text


def format_elapsed_time(start_time: float) -> str:
    hours, seconds = divmod(time.time() - start_time, 3600)
    minutes = seconds / 60
    return f"{int(hours)}h {int(minutes)}m"


@functools.cache
def gb_mod_versions() -> Optional[dict]:
    try:
        r_mods = requests.get('https://maddie480.ovh/celeste/everest_update.yaml', timeout=60)
        utils.handle_potential_request_error(r_mods, 200)
    except requests.RequestException:
        log_error()
        return None

    import yaml
    return yaml.safe_load(r_mods.content)


@functools.cache
def mods_dir() -> Path:
    mods_path = Path('Mods')  # expects a symlink
    mods_path_resolved = mods_path.resolve()

    if not mods_path.is_dir() or not mods_path_resolved.is_dir() or not (mods_path_resolved / 'CelesteTAS.zip').is_file():
        raise FileNotFoundError("ok where'd my mods go")
    else:
        return mods_path_resolved


@functools.cache
def game_dir() -> Path:
    game_path = Path('celeste')  # expects a symlink
    game_path_resolved = game_path.resolve()

    if not game_path.is_dir() or not game_path_resolved.is_dir() or not (game_path_resolved / 'Celeste.exe').is_file():
        raise FileNotFoundError("ok where'd the game go")
    else:
        return game_path_resolved


def scaled_sleep(seconds: float):
    host_sleep_scale = utils.host().sleep_scale
    time.sleep(seconds * sleep_scale)


def log_error(message: Optional[str] = None):
    error = utils.log_error(message)
    db.send_sync_result(db.SyncResultType.REPORTED_ERROR, {'time': int(time.time()), 'error': error[-1950:]})


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode('UTF8')


def sid_is_valid(sid: str) -> bool:
    return sid not in ('', 'null') and 'Cannot access instance field' not in sid and 'Not found' not in sid


log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()
re_redact_token = re.compile(r"'token': '[^']*'")
game_sync_hash = None
sleep_scale = 1.0

if __name__ == '__main__':
    run_syncs()
