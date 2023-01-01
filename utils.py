import logging
import os
import subprocess
import time
from typing import Any, Callable, Optional, Sized, Union

import discord
import fastjsonschema
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
        validate_project_schema(projects[project_id])


def load_project_schema() -> Callable:
    with open('project_schema.json', 'r') as projects_schema_file:
        return fastjsonschema.compile(ujson.load(projects_schema_file))


log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
validate_project_schema = load_project_schema()
