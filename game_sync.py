import argparse
import base64
import functools
import logging
import os
import shutil
import stat
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
    parser.add_argument('-p', type=int, help="Only sync test a specific project (ID)", required=False)
    cli_project_id = parser.parse_args().p

    if cli_project_id:
        log.info(f"Running sync test for project ID {cli_project_id} only")
        test_projects = (cli_project_id,)
    else:
        log.info("Running all sync tests")
        test_projects = reversed(main.projects)

    utils.projects = utils.load_projects()
    main.path_caches = utils.load_path_caches()

    try:
        for project_id in test_projects:
            if (main.projects[project_id]['do_run_validation'] or cli_project_id) and main.path_caches[project_id]:
                sync_test(project_id)
    except Exception as error:
        log.error(repr(error))
        close_game()
        post_cleanup()
        raise

    post_cleanup()


def sync_test(project_id: int):
    project = main.projects[project_id]
    log.info(f"Running sync test for project: {project['name']}")
    mods = project['mods']
    repo = project['repo']
    previous_desyncs = project['desyncs']
    desyncs = []
    mods_to_load = set(mods)
    files_timed = 0
    remove_debug_save_files()
    queued_filetime_commits = []

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

    # clone repo
    repo_cloned = repo.partition('/')[2]
    repo_path = f'E:\\Big downloads\\celeste\\repos\\{repo_cloned}'

    if not os.path.isdir(r'E:\Big downloads\celeste\repos'):
        os.mkdir(r'E:\Big downloads\celeste\repos')
    elif os.path.isdir(repo_path):
        shutil.rmtree(repo_path, onerror=del_rw)

    time.sleep(0.1)
    cwd = os.getcwd()
    os.chdir(r'E:\Big downloads\celeste\repos')
    subprocess.run(f'git clone https://github.com/{repo} --recursive', capture_output=True)
    os.chdir(cwd)
    log.info(f"Cloned repo to {repo_path}")

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
            log.info("Game loaded")
            time.sleep(2)
            game_loaded = True

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
        _, found_final_time, final_time, final_time_trimmed, final_time_line_num, _ = validation.parse_tas_file(tas_lines, False, False, True)

        if found_final_time:
            has_filetime = tas_lines[final_time_line_num].startswith('FileTime')

            if has_filetime:
                tas_lines_og = tas_lines.copy()
                final_time_line_num_og = final_time_line_num
                tas_lines[final_time_line_num] = 'FileTime: \n'
                has_console_load = [line for line in tas_lines if line.startswith('console load')] != []

                if not has_console_load:
                    # if it starts from begin, then menu there
                    tas_lines[:0] = ['unsafe\n', 'console overworld\n', '2\n', '1,J\n', '94\n', '1,J\n', '56\n', 'Repeat 5\n', '1,D\n', '1,F,180\n', 'Endrepeat\n', '1,J\n',
                                     '14\n', '1,D\n', '1,F,180\n', '1,D\n', '1,F,180\n', '1,L\n', '1,U\n', '1,F,\n', '1,U\n', '1,F,\n']
            else:
                tas_lines[final_time_line_num] = 'ChapterTime: \n'
        else:
            log.info(f"{tas_filename} has no final time")
            continue

        tas_lines.insert(0, f'Set,CollabUtils2.DisplayEndScreenForAllMaps,{not has_filetime}\n')
        tas_lines.append('***')

        with open(file_path, 'w', encoding='UTF8') as tas_file:
            tas_file.truncate()
            tas_file.write(''.join(tas_lines))

        time.sleep(0.2)
        initial_mtime = os.path.getmtime(file_path)

        # now run it
        log.info(f"Testing timing of {tas_filename} ({final_time_trimmed})")
        tas_started = False

        while not tas_started:
            try:
                requests.post(f'http://localhost:32270/tas/playtas?filePath={file_path}', timeout=10)
                tas_started = True
                tas_finished = False
            except (requests.Timeout, requests.ConnectionError):
                pass

        while not tas_finished:
            try:
                time.sleep(10 if has_filetime else 3)
                session_data = requests.get('http://localhost:32270/tas/info', timeout=2).text
            except (requests.Timeout, requests.ConnectionError):
                pass
            else:
                tas_finished = 'Running: False' in session_data

        log.info("TAS has finished")
        time.sleep(3)
        extra_sleeps = 0

        while os.path.getmtime(file_path) == initial_mtime and extra_sleeps < 5:
            time.sleep(3 + (extra_sleeps ** 2))
            extra_sleeps += 1
            log.info(f"Extra sleeps: {extra_sleeps}")

        # determine if it synced or not
        with open(file_path, 'rb') as tas_file:
            tas_updated = validation.as_lines(tas_file.read())

        _, found_final_time, final_time_new, final_time_new_trimmed, final_time_line_num, _ = validation.parse_tas_file(tas_updated, False, False, True)

        # clear debug save, for silvers
        if has_filetime:
            try:
                requests.post('http://localhost:32270/console?command=overworld', timeout=2)
                time.sleep(0.5)
                requests.post('http://localhost:32270/console?command=clrsav', timeout=2)
            except (requests.Timeout, requests.ConnectionError):
                pass

        if found_final_time:
            frame_diff = validation.calculate_time_difference(final_time_new, final_time)
            synced = frame_diff == 0

            if not has_filetime:
                log_command = log.info if synced else log.warning
                log_command(f"{'Synced' if synced else 'Desynced'}: {final_time_trimmed} -> {final_time_new_trimmed} ({'+' if frame_diff > 0 else ''}{frame_diff}f)")

                if not synced:
                    desyncs.append(tas_filename)
            else:
                project['filetimes'][tas_filename] = final_time_new_trimmed

                if not synced:
                    new_time_line = tas_updated[final_time_line_num]
                    tas_lines_og[final_time_line_num_og] = new_time_line
                    commit_message = f"{'+' if frame_diff > 0 else ''}{frame_diff}f {tas_filename} ({final_time_new_trimmed})"
                    queued_filetime_commits.append((file_path_repo, ''.join(tas_lines_og), commit_message))
                    # don't commit now, since there may be desyncs
        else:
            log.warning(f"Desynced (no {'FileTime' if has_filetime else 'ChapterTime'})")
            log.info(session_data.partition('<pre>')[2].partition('</pre>')[0])
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
        desyncs_formatted = '\n'.join(desyncs)
        desyncs_block = '' if desyncs == new_desyncs else f"\nAll desyncs:\n```\n{desyncs_formatted}```"
        report_text = f"Sync check finished, {len(new_desyncs)} new desync{plural(new_desyncs)} found ({files_timed} file{plural(files_timed)} tested):" \
                      f"\n```\n{new_desyncs_formatted}```{desyncs_block}"

    if time_since_last_commit > 1209600 and project['do_run_validation']:
        project['do_run_validation'] = False
        log.warning(f"Disabled auto sync check after {time_since_last_commit} seconds of inactivity")
        report_text = "Disabled nightly sync checking after two weeks of no improvements."

    main.projects[project_id] = project  # yes this is dumb
    utils.save_projects()

    with open(f'sync\\sync_result_{project_id}.txt', 'w', encoding='UTF8') as sync_result:
        if report_text:
            sync_result.write(report_text)

    log.info("Created result file")

    # commit updated fullgame files
    for queued_commit in queued_filetime_commits:
        file_path_repo, lines_joined, commit_message = queued_commit
        desyncs_found = [d for d in desyncs if d[:-4] in lines_joined]

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

    try:
        log.info(f"Removed {len(debug_save_files)} debug save files")
    except AttributeError:
        pass


def post_cleanup():
    generate_blacklist(set())
    remove_debug_save_files()
    files_to_remove = ['log.txt']
    dirs_to_remove = ['LogHistory', 'TAS Files\\Backups', 'repos']
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
            shutil.rmtree(dir_to_remove, onerror=del_rw)

    log.info(f"Deleted {files_removed} file{plural(files_removed)} and {dirs_removed} dir{plural(dirs_to_remove)} from game install")


def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)


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
