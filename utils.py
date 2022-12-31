import logging
import os
import subprocess
import time
from typing import Any, Optional, Sized, Union

import discord
import requests
import ujson

import main


def plural(count: Union[int, Sized]) -> str:
    if isinstance(count, int):
        return 's' if count != 1 else ''
    else:
        return 's' if len(count) != 1 else ''


def handle_potential_request_error(req: requests.Response, code: int):
    if req.status_code != code:
        log.warning(f"Bad HTTP status_code: {req.status_code}, should be {code} (url={req.url})")
        log.warning(req.text)


def detailed_user(message: Optional[discord.Message] = None, user: Optional[discord.User] = None):
    if message:
        user = message.author

    return f'{user.name}#{user.discriminator} ({user.id})'


def load_projects() -> dict:
    with open('improvements-bot-data\\projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = ujson.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    validate_project_formats(projects_fixed)
    return projects_fixed


def save_projects():
    validate_project_formats(main.projects)

    with open('improvements-bot-data\\projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        ujson.dump(main.projects, projects_json, ensure_ascii=False, indent=4, escape_forward_slashes=False)


def add_project_key(key: str, value: Any):
    for project_id in main.projects:
        main.projects[project_id][key] = value

    save_projects()
    sync_data_repo(f"Bulk added project key: {key}")
    log.info(f"Added `{key}: {value}` to {len(main.projects)} projects, be sure to update validate_project_formats and command_register_project")


def load_path_caches():
    with open('improvements-bot-data\\path_caches.json', 'r', encoding='UTF8') as path_caches_json:
        path_caches_loaded = ujson.load(path_caches_json)
        main.path_caches = {int(k): path_caches_loaded[k] for k in path_caches_loaded}


def save_path_caches():
    with open('improvements-bot-data\\path_caches.json', 'r+', encoding='UTF8') as path_caches_json:
        path_caches_json.truncate()
        ujson.dump(main.path_caches, path_caches_json, ensure_ascii=False, indent=4, escape_forward_slashes=False)


def sync_data_repo(commit_message: Optional[str] = None, only_pull: bool = False):
    log.info("Syncing data repo")
    working_dir = os.getcwd()

    try:
        history_log.handlers[0].flush()
    except AttributeError:
        pass

    try:
        os.chdir('improvements-bot-data')
        subprocess.run('git pull', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fingers crossed no conflicts occur

        if not only_pull and b'working tree clean' not in subprocess.run('git status', capture_output=True).stdout:
            # fingers crossed here too
            commit_message = commit_message if commit_message else int(time.time())
            log.info(f"Committing changes to data repo with message \"{commit_message}\"")
            subprocess.run('git add *', stdout=subprocess.DEVNULL)
            subprocess.run(f'git commit -m"{commit_message}"', stdout=subprocess.DEVNULL)
            subprocess.run('git push', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        log.error(f"Error updating data repo: {repr(error)}")

    os.chdir(working_dir)
    main.projects = load_projects()
    main.load_project_logs()
    load_path_caches()


def validate_project_formats(projects: dict):
    for project_id in projects:
        project = projects[project_id]

        try:
            name = project['name']; assert isinstance(name, str); assert len(name) > 0
            repo = project['repo']; assert isinstance(repo, str); assert len(repo) > 0
            installation_owner = project['installation_owner']; assert isinstance(installation_owner, str); assert len(installation_owner) > 0
            assert isinstance(project['admin'], int)
            assert isinstance(project['install_time'], int)
            assert isinstance(project['commit_drafts'], bool)
            assert isinstance(project['is_lobby'], bool)
            assert isinstance(project['ensure_level'], bool)
            assert isinstance(project['pin'], int)
            assert isinstance(project['do_run_validation'], bool)
            assert isinstance(project['last_run_validation'], int) or project['last_run_validation'] is None
            assert isinstance(project['subdir'], str)
            assert isinstance(project['mods'], list)
            assert isinstance(project['desyncs'], list)
            assert isinstance(project['last_commit_time'], int)

            for mod in project['mods']:
                assert isinstance(mod, str); assert len(mod) > 0

            assert len(project) == 15
        except (KeyError, AssertionError) as error:
            log.error(f"Invalid format for project {project_id}: {repr(error)}")


log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
