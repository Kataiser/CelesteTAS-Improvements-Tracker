import base64
import copy
import datetime
import functools
import logging
import os
import sys
import time
import urllib.parse
from typing import Optional

import discord
import requests
import ujson

import commands
import game_sync
import gen_token
import utils
import validation
from utils import plural, projects


# process a message posted in a registered improvements channel
async def process_improvement_message(message: discord.Message, skip_validation: bool = False):
    if not skip_validation and not is_processable_message(message):
        return

    log.info(f"Processing message from {utils.detailed_user(message)} in server {message.guild.name} (project: {projects[message.channel.id]['name']}) at {message.jump_url}")
    tas_attachments = [a for a in message.attachments if a.filename.endswith('.tas')]
    video_attachments = [a for a in message.attachments if a.filename.rpartition('.')[2] in ('mp4', 'webm', 'gif', 'gifv', 'mkv', 'avi', 'mov', 'm4v')]
    has_video = video_attachments or [s for s in ('youtube.com/watch?v=', 'youtu.be/', 'streamable.com/', 'gfycat.com/') if s in message.content]

    if has_video:
        log.info("Video found 🍿")
        await message.add_reaction('🍿')

    if len(tas_attachments) == 0:
        log.info("No TAS file found 👍")

        if not has_video:
            if "bad bot" in message.content.lower():
                await message.add_reaction('😢')
            else:
                await message.add_reaction('👍')

        add_project_log(message)
        log.info("Done processing message")
        return
    elif len(tas_attachments) > 1:
        log.warning(f"Message has {len(tas_attachments)} TAS files. This could break stuff")
        # TODO: handle this better

    if not skip_validation:
        await message.clear_reaction('❌')
        await message.clear_reaction('⏭')
    await message.add_reaction('👀')
    generate_request_headers(projects[message.channel.id]['installation_owner'])
    committed = False

    for attachment in tas_attachments:
        log.info(f"Processing file {attachment.filename} at {attachment.url}")
        repo = projects[message.channel.id]['repo']
        is_lobby = projects[message.channel.id]['is_lobby']
        r = requests.get(attachment.url)
        utils.handle_potential_request_error(r, 200)
        file_content = r.content
        filename, filename_no_underscores = attachment.filename, attachment.filename.replace('_', ' ')

        if filename not in path_caches[message.channel.id] and filename_no_underscores in path_caches[message.channel.id]:
            log.info(f"Considering {filename} as {filename_no_underscores}")
            filename = filename_no_underscores

        old_file_path = get_file_repo_path(message.channel.id, filename)
        old_file_content = None

        if old_file_path:
            log.info("Downloading old version of file, for time reference")
            r = requests.get(f'https://api.github.com/repos/{repo}/contents/{old_file_path}', headers=headers)
            r_json = ujson.loads(r.content)

            if r.status_code == 404 and 'message' in r_json and r_json['message'] == "Not Found":
                del path_caches[message.channel.id][filename]
                log.warning("File existed in path cache but doesn't seem to exist in repo")
            else:
                utils.handle_potential_request_error(r, 200)
                old_file_content = base64.b64decode(r_json['content'])
        else:
            log.info("No old version of file exists")

        validation_result = validation.validate(file_content, filename, message, old_file_content, is_lobby, skip_validation)

        if validation_result.valid_tas:
            # I love it when
            # when timesave :)
            # (or drafts)
            commit_status = commit(message, filename, file_content, validation_result)
            projects[message.channel.id]['last_commit_time'] = int(time.time())
            utils.save_projects()

            if commit_status:
                committed = True
                history_data = (utils.detailed_user(message), message.channel.id, projects[message.channel.id]['name'], *commit_status, attachment.url)
                history_log.info(history_data)
                log.info("Added to history log")
                await message.add_reaction('📝')
                await edit_pin(message.channel)
            else:
                log.info("File is a draft, and committing drafts is disabled for this project 🤘")
                await message.add_reaction('🤘')
        else:
            log.info(f"Warning {utils.detailed_user(message)} about {validation_result.log_text}")
            await message.add_reaction('❌')
            await message.add_reaction('⏭')

            if len(tas_attachments) > 1:
                await message.reply(f"`{attachment.filename}`\n{validation_result.warning_text}")
            else:
                await message.reply(validation_result.warning_text)

        if len(tas_attachments) > 1:
            log.info(f"Done processing {filename}")

    if not skip_validation:
        add_project_log(message)

    utils.sync_data_repo(commit_status[0] if committed else None)
    await message.clear_reaction('👀')
    log.info("Done processing message")


# assumes already verified TAS
def commit(message: discord.Message, filename: str, content: bytes, validation_result: validation.ValidationResult) -> Optional[tuple]:
    log.info("Potentially committing file")
    repo = projects[message.channel.id]['repo']
    data = {'content': base64.b64encode(content).decode('UTF8')}
    author = nicknames[message.author.id] if message.author.id in nicknames else message.author.name
    file_path = get_file_repo_path(message.channel.id, filename)
    chapter_time = f" ({validation_result.finaltime})" if validation_result.finaltime else ""
    user_github_account = get_user_github_account(message.author.id)

    if file_path:
        draft = False
        timesave = f"{validation_result.timesave} " if validation_result.timesave else "Updated: "
        data['sha'] = get_sha(repo, file_path)
        data['message'] = f"{timesave}{filename}{chapter_time} from {author}"
    else:
        draft = True
        data['message'] = f"{filename} draft by {author}{chapter_time}"
        subdir = projects[message.channel.id]['subdir']
        file_path = f'{subdir}/{filename}' if subdir else filename
        path_caches[message.channel.id][filename] = file_path
        utils.save_path_caches()

        if not projects[message.channel.id]['commit_drafts']:
            return

    if user_github_account:
        data['author'] = {'name': user_github_account[0], 'email': user_github_account[1]}
        log.info(f"Set commit author to {data['author']}")

    log.info(f"Set commit message to \"{data['message']}\"")
    r = requests.put(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers, data=ujson.dumps(data))
    utils.handle_potential_request_error(r, 201 if draft else 200)
    commit_url = ujson.loads(r.content)['commit']['html_url']
    log.info(f"Successfully committed: {commit_url}")
    return data['message'], commit_url


# if a file exists in the repo, get its path
def get_file_repo_path(project_id: int, filename: str) -> Optional[str]:
    if filename not in path_caches[project_id]:
        generate_path_cache(project_id)

    if filename in path_caches[project_id]:
        return path_caches[project_id][filename]


# walk the project's repo and cache the path of all TAS files found
def generate_path_cache(project_id: int):
    repo = projects[project_id]['repo']
    project_subdir = projects[project_id]['subdir']
    project_subdir_base = project_subdir.partition('/')[0]
    log.info(f"Caching {repo} structure ({project_subdir=})")
    r = requests.get(f'https://api.github.com/repos/{repo}/contents', headers=headers)
    utils.handle_potential_request_error(r, 200)
    old_path_cache = copy.copy(path_caches[project_id]) if project_id in path_caches else None
    path_caches[project_id] = {}  # always start from scratch

    for item in ujson.loads(r.content):
        if item['type'] == 'dir' and (item['name'].startswith(project_subdir_base) if project_subdir else True):
            # recursively get files in dirs (fyi {'recursive': 1} means true, not a depth of 1)
            dir_sha = item['sha']
            r = requests.get(f'https://api.github.com/repos/{repo}/git/trees/{dir_sha}', headers=headers, params={'recursive': 1})
            utils.handle_potential_request_error(r, 200)

            for subitem in ujson.loads(r.content)['tree']:
                if subitem['type'] == 'blob':
                    subitem_name = subitem['path'].split('/')[-1]
                    subitem_full_path = f"{item['name']}/{subitem['path']}"

                    if subitem_name.endswith('.tas') and (subitem_full_path.startswith(project_subdir) if project_subdir else True):
                        path_caches[project_id][subitem_name] = subitem_full_path
        elif not project_subdir and item['name'].endswith('.tas'):
            path_caches[project_id][item['name']] = item['path']

    if path_caches[project_id] != old_path_cache:
        utils.save_path_caches()

    log.info(f"Cached: {path_caches[project_id]}")


# we know the file exists, so get its SHA for updating
def get_sha(repo: str, file_path: str) -> str:
    r = requests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    utils.handle_potential_request_error(r, 200)
    repo_contents = ujson.loads(r.content)
    log.info(f"Found SHA of {file_path}: {repo_contents['sha']}")
    return repo_contents['sha']


# haven't processed message before, and wasn't posted before project install
def is_processable_message(message: discord.Message) -> bool:
    if message.id in project_logs[message.channel.id] or message.author.id == 970375635027525652 or (safe_mode and message.channel.id not in safe_projects):
        return False
    else:
        # because the timestamp is UTC, but the library doesn't seem to know that
        post_time = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        return post_time > projects[message.channel.id]['install_time']


async def edit_pin(channel: discord.TextChannel, create: bool = False):
    project = projects[channel.id]
    lobby_text = "Since this is channel is for a lobby, this is not automatically validated. " if project['is_lobby'] else ""
    level_text = "the name of the level/map"
    ensure_level = project['ensure_level']
    desyncs = project['desyncs']
    desyncs_text = "\n"

    text = "Welcome to the **{0} TAS project!** This improvements channel is in part managed by this bot, which automatically verifies and commits files. When posting " \
           f"a file, please include the amount of frames saved{f', {level_text},' if ensure_level else ''} and the ChapterTime of the file, (ex: `-4f 3B (1:30.168)`). {lobby_text}" \
           f"Room(s) affected is ideal, and{'' if ensure_level else f' {level_text},'} previous ChapterTime, category affected, and video are optional." \
           "\n\nRepo: <{1}> (<https://desktop.github.com> is recommended)" \
           "\nPackage DL: <{2}>" \
           "\nAdmin: <@{3}>" \
           "\nLast sync check: {4}{5}" \
           "\nBot reactions key:" \
           "\n```" \
           "\n📝 = Successfully verified and committed" \
           "\n👀 = Currently processing file" \
           "\n❌ = Invalid TAS file or post" \
           "\n⏭ = React to commit invalid post anyway" \
           "\n👍 = Non-TAS containing message" \
           "\n🤘 = Successfully verified draft but didn't commit" \
           "\n🍿 = Video in message```"

    if project['do_run_validation']:
        last_run = project['last_run_validation']

        if last_run:
            sync_timestamp = f"<t:{last_run}>"
        else:
            sync_timestamp = "`Not yet run`"

        if desyncs:
            desyncs_formatted = '\n'.join(desyncs)
            desyncs_text = f"\n\nCurrently desyncing file{plural(desyncs)}:\n```\n{desyncs_formatted}```"
    else:
        sync_timestamp = "`Disabled`"

    name = project['name']
    repo = project['repo']
    pin = project['pin']
    subdir = project['subdir']
    repo_url = f'https://github.com/{repo}/tree/master/{subdir}' if subdir else f'https://github.com/{repo}'
    package_url = f'https://download-directory.github.io/?url=https://github.com/{repo}/tree/main/{urllib.parse.quote(subdir)}' if subdir else \
        f'https://github.com/{repo}/archive/refs/heads/master.zip'
    text_out = text.format(name, repo_url, package_url, project['admin'], sync_timestamp, desyncs_text)

    if create:
        log.info("Creating pin")
        return await channel.send(text_out)
    else:
        pin_message = channel.get_partial_message(pin)
        await pin_message.edit(content=text_out)
        log.info("Edited pin")
        return pin_message


@functools.cache
def get_user_github_account(discord_id: int) -> Optional[tuple]:
    with open('improvements-bot-data\\githubs.json', 'r') as githubs_json:
        github_accounts = ujson.load(githubs_json)

    if str(discord_id) in github_accounts:
        return github_accounts[str(discord_id)]


# load the saved message IDs of already committed posts
def load_project_logs():
    if project_logs:
        # bot restarted itself
        return

    for project in projects:
        project_log_path = f'improvements-bot-data\\project_logs\\{project}.bin'

        if not os.path.isfile(project_log_path):
            open(project_log_path, 'w').close()
            project_logs[project] = []
            log.info(f"Created {project_log_path}")
        else:
            with open(project_log_path, 'rb') as project_log_db:
                project_log_read = project_log_db.read()

            project_logs[project] = list(memoryview(project_log_read).cast('Q'))


def add_project_log(message: discord.Message):
    project_log_path = f'improvements-bot-data\\project_logs\\{message.channel.id}.bin'

    with open(project_log_path, 'ab') as project_log_db:
        # yes this format is basically unnecessary, but I think it's cool :)
        project_log_db.write(message.id.to_bytes(8, byteorder='little'))

    project_logs[message.channel.id].append(message.id)
    log.info(f"Added message ID {message.id} to {project_log_path}")


def generate_request_headers(installation_owner: str, min_time: int = 30):
    global headers
    headers = {'Authorization': f'token {gen_token.access_token(installation_owner, min_time)}', 'Accept': 'application/vnd.github.v3+json'}


def create_loggers() -> (logging.Logger, logging.Logger):
    logger = logging.getLogger('bot')
    logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(filename='bot.log', encoding='UTF8', mode='w')
    log_formatter = logging.Formatter('%(asctime)s:%(levelname)s: %(message)s')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_formatter)
    logger.addHandler(stdout_handler)

    history = logging.getLogger('history')
    history.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(filename='improvements-bot-data\\history.log', encoding='UTF8', mode='a')
    file_handler.setFormatter(log_formatter)
    history.addHandler(file_handler)

    global log, history_log
    log = logger
    gen_token.log = logger
    validation.log = logger
    utils.log = logger
    commands.log = logger
    game_sync.log = logger
    history_log = history
    commands.history_log = history

    return logger, history


log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
project_logs = {}
path_caches = {}
headers = None
login_time = None
safe_mode = None
nicknames = {234520815658336258: "Vamp", 587491655129759744: "Ella"}
safe_projects = (970380662907482142, 973793458919723088, 975867007868235836, 976903244863381564)
