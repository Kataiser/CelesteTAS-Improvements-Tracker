import logging
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
    with open('projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = ujson.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    validate_project_formats(projects_fixed)
    return projects_fixed


def save_projects():
    validate_project_formats(main.projects)

    with open('projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        ujson.dump(main.projects, projects_json, ensure_ascii=False, indent=4, escape_forward_slashes=False)


def add_project_key(key: str, value: Any):
    for project_id in main.projects:
        main.projects[project_id][key] = value

    save_projects()
    log.info(f"Added `{key}: {value}` to {len(main.projects)} projects, be sure to update validate_project_formats and command_register_project")


def load_path_caches() -> dict:
    with open('path_caches.json', 'r', encoding='UTF8') as path_caches_json:
        path_caches_loaded = ujson.load(path_caches_json)
        return {int(k): path_caches_loaded[k] for k in path_caches_loaded}


def save_path_caches():
    with open('path_caches.json', 'r+', encoding='UTF8') as path_caches_json:
        path_caches_json.truncate()
        ujson.dump(main.path_caches, path_caches_json, ensure_ascii=False, indent=4, escape_forward_slashes=False)


def validate_project_formats(projects: dict):
    for project_id in projects:
        validate_project_schema(projects[project_id])


def load_project_schema() -> Callable:
    with open('project_schema.json', 'r') as projects_schema_file:
        return fastjsonschema.compile(ujson.load(projects_schema_file))


log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
validate_project_schema = load_project_schema()
