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
    global projects

    with open('projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = json.load(projects_json)
        projects = {int(k): projects_loaded[k] for k in projects_loaded}

    validate_project_formats()


def save_projects():
    validate_project_formats()

    with open('projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        json.dump(projects, projects_json, ensure_ascii=False, indent=4)


def validate_project_formats():
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
            assert isinstance(project['subdir'], str)
            assert isinstance(project['mods'], list)
            assert isinstance(project['path_cache'], dict)

            for mod in project['mods']:
                assert isinstance(mod, str); assert len(mod) > 0

            for file in project['path_cache']:
                assert isinstance(file, str); assert len(file) > 0
                path = project['path_cache'][file]; assert isinstance(path, str); assert len(path) > 0

        except (KeyError, AssertionError) as error:
            log.error(f"Invalid format for project {project_id}: {repr(error)}")


async def edit_pin(channel: discord.TextChannel, create: bool, ran_sync: bool = True):
    lobby_text = "Since this is channel is for a lobby, this is not automatically validated. " if projects[channel.id]['is_lobby'] else ""
    level_text = "the name of the level/map"
    ensure_level = projects[channel.id]['ensure_level']

    text = "Welcome to the **{0} TAS project!** This improvements channel is in part managed by this bot, which automatically verifies and commits files. When posting " \
           f"a file, please include the amount of frames saved{f', {level_text},' if ensure_level else ''} and the ChapterTime of the file, (ex: `-4f 3B (1:30.168)`). {lobby_text}" \
           f"Room(s) affected is ideal, and{'' if ensure_level else f' {level_text},'} previous ChapterTime, category affected, and video are optional." \
           "\n\nRepo: <{1}> (<https://desktop.github.com> is recommended)" \
           "\nPackage: <{2}>" \
           "\nLast sync verification: {3}" \
           "\n\nBot reactions key:" \
           "\n```" \
           "\n📝 = Successfully verified and committed" \
           "\n👀 = Currently processing file" \
           "\n❌ = Invalid TAS file or post" \
           "\n👍 = Non-TAS containing message" \
           "\n🤘 = Successfully verified draft but didn't commit" \
           "\n🍿 = Video in message```"

    name = projects[channel.id]['name']
    repo = projects[channel.id]['repo']
    pin = projects[channel.id]['pin']
    subdir = projects[channel.id]['subdir']
    repo_url = f'https://github.com/{repo}/tree/master/{subdir}' if subdir else f'https://github.com/{repo}'
    package_url = f'https://download-directory.github.io/?url=https://github.com/{repo}/tree/main/{subdir}' if subdir else \
        f'https://github.com/{repo}/archive/refs/heads/master.zip'
    # sync_timestamp = f'<t:{round(time.time())}>'
    text_out = text.format(name, repo_url, package_url, "Not yet implemented")

    if create:
        log.info("Creating pin")
        return await channel.send(text_out)
    else:
        pin_message = channel.get_partial_message(pin)
        await pin_message.edit(content=text_out, suppress=True)
        log.info("Edited pin")
        return pin_message


log: Optional[logging.Logger] = None
projects: Optional[dict] = None
