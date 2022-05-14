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


def load_projects() -> dict:
    with open('projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = json.load(projects_json)
        return {int(k): projects_loaded[k] for k in projects_loaded}


def save_projects():
    with open('projects.json', 'r+', encoding='UTF8') as projects_json:
        projects_json.truncate()
        json.dump(projects, projects_json, ensure_ascii=False, indent=4)


def detailed_user(message: discord.Message):
    return f'{message.author.name}#{message.author.discriminator} ({message.author.id})'


async def edit_pin(channel: discord.TextChannel, create: bool, ran_sync: bool = True):
    text = "Welcome to the **{0} TAS project!** This improvements channel is in part managed by this bot, which automatically verifies and commits files. When posting " \
           "a file, please include the amount of frames saved, the name of the level/map, the ChapterTime of the file, and the ChapterTime of the file before you made improvements " \
           "(ex: `-4f 3B (1:30.236 -> 1:30.168)`). Room(s) affected is ideal, and category affected and video are optional." \
           "\n\nRepo: {1}" \
           "\nPackage: {2}" \
           "\nLast sync verification: {3}" \
           "\n\nBot reactions key:" \
           "\n```" \
           "\n📝 = Successfully verified and committed" \
           "\n👀 = Currently processing file" \
           "\n👍 = Non-TAS containing message" \
           "\n🤘 = Successfully verified draft but didn't commit" \
           "\n❌ = Invalid TAS file or post```"

    name = projects[channel.id]['name']
    repo = projects[channel.id]['repo']
    pin = projects[channel.id]['pin']
    subdir = projects[channel.id]['subdir']
    repo_url = f'https://github.com/{repo}/{subdir}' if subdir else f'https://github.com/{repo}'
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
projects = load_projects()
