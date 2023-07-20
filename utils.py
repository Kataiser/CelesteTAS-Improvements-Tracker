import logging
import time
from typing import Any, Callable, Optional, Sized, Union, Tuple

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


def detailed_user(message: Optional[discord.Message] = None, user: Optional[discord.User] = None) -> Optional[str]:
    if message:
        user = message.author
    elif not user:
        return

    return f'{user.global_name} ({user.name}, {user.id})'


def nickname(author: discord.User) -> str:
    nicknames = {234520815658336258: "Vamp",
                 587491655129759744: "Ella",
                 513223843721117713: "The Senate",
                 671098132959985684: "Mr. Wolf",
                 226515080752267286: "Soloiini",
                 794291191726211103: "Ash"}

    return nicknames[author.id] if author.id in nicknames else author.global_name


def load_projects() -> dict:
    with open('sync\\projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = ujson.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    validate_project_formats(projects_fixed)
    return projects_fixed


def save_projects():
    validate_project_formats(main.projects)

    with open('sync\\projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        ujson.dump(main.projects, projects_json, ensure_ascii=False, indent=4, escape_forward_slashes=False)


def add_project_key(key: str, value: Any):
    for project_id in main.projects:
        main.projects[project_id][key] = value

    save_projects()
    log.info(f"Added `{key}: {value}` to {len(main.projects)} projects, be sure to update validate_project_formats and command_register_project")


def validate_project_formats(projects: dict):
    for project_id in projects:
        validate_project_schema(projects[project_id])


def load_project_schema() -> Callable:
    with open('project_schema.json', 'r') as projects_schema_file:
        return fastjsonschema.compile(ujson.load(projects_schema_file))


def load_sj_data() -> Tuple[dict, dict]:
    with open('sj.json', 'r', encoding='UTF8') as sj_file:
        sj_data: dict = ujson.load(sj_file)
        sj_data_filenames = {sj_data[sj_map][4]: sj_map for sj_map in sj_data}
        return sj_data, sj_data_filenames


def log_timestamp() -> str:
    current_time = time.time()
    current_time_local = time.localtime(current_time)
    return time.strftime('%Y-%m-%d %H:%M:%S,', current_time_local) + str(round(current_time % 1, 3))[2:]


log: Optional[logging.Logger] = None
validate_project_schema = load_project_schema()
