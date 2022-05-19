import json
import logging
from typing import Optional, Sized, Union

import discord
import requests


def plural(count: Union[int, Sized]) -> str:
    if isinstance(count, int):
        return 's' if count != 1 else ''
    else:
        return 's' if len(count) != 1 else ''


def handle_potential_request_error(req: requests.Response, code: int):
    if req.status_code != code:
        log.warning(f"Bad HTTP status_code: {req.status_code}, should be {code}")
        log.warning(req.text)


def detailed_user(message: discord.Message):
    return f'{message.author.name}#{message.author.discriminator} ({message.author.id})'


def load_projects():
    with open('projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = json.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    validate_project_formats(projects_fixed)
    return projects_fixed


def save_projects():
    validate_project_formats(projects)

    with open('projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        json.dump(projects, projects_json, ensure_ascii=False, indent=4)


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
            assert isinstance(project['path_cache'], dict)

            for mod in project['mods']:
                assert isinstance(mod, str); assert len(mod) > 0

            for file in project['path_cache']:
                assert isinstance(file, str); assert len(file) > 0
                path = project['path_cache'][file]; assert isinstance(path, str); assert len(path) > 0

            assert len(project) == 14
        except (KeyError, AssertionError) as error:
            log.error(f"Invalid format for project {project_id}: {repr(error)}")


log: Optional[logging.Logger] = None
projects = load_projects()
