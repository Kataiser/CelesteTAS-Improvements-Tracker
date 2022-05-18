import argparse
import base64
import datetime
import json
import logging
import os
import sys
import time
import traceback
from typing import Optional

import discord
import psutil
import requests

import dm
import game_sync
import gen_token
import utils
import validation
from utils import plural

client = discord.Client()
debug = False


def main():
    global debug, projects
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        print("DEBUG MODE")

    utils.load_projects()
    projects = utils.projects
    load_project_logs()
    log.info(f"Loaded {len(projects)} project{plural(projects)} and {len(project_logs)} project message log{plural(project_logs)}")

    with open('bot_token', 'r') as bot_token_file:
        bot_token = bot_token_file.read()

    while True:
        try:
            log.info("Logging in...")
            client.run(bot_token)
        except Exception as error:
            log.error(error)

            if not debug:
                log.info("Restarting bot in 5 seconds, this can only end well")
                time.sleep(5)
            else:
                break


@client.event
async def on_ready():
    log.info(f"Logged in as {client.user}")
    downtime_message_count = 0

    if not debug:
        self_process = psutil.Process()
        self_process.nice(psutil.IDLE_PRIORITY_CLASS)
        self_process.ionice(psutil.IOPRIO_VERYLOW)
        log.info("Set process priorities")
    else:
        log.info("Skipped setting priorities")

    for improvements_channel in projects:
        downtime_messages = await client.get_channel(improvements_channel).history(limit=20).flatten()
        downtime_messages.reverse()  # make chronological

        for message in downtime_messages:
            downtime_message_count += 1
            await process_improvement_message(message)

    log.info(f"Finished considering {downtime_message_count} downtime messages")


@client.event
async def on_message(message: discord.Message):
    await client.wait_until_ready()

    if message.author == client.user:
        return
    elif not message.guild:
        await dm.handle(message)
        return
    elif message.channel.id not in projects:
        return

    await process_improvement_message(message)


@client.event
async def on_connect():
    log.info("Connected to Discord")


@client.event
async def on_disconnect():
    log.error("Disconnected from Discord")


@client.event
async def on_error(event: str):
    error = traceback.format_exc()
    log.error(error)


# process a message posted in a registered improvements channel
async def process_improvement_message(message: discord.Message):
    if not is_processable_message(message):
        return

    log.info(f"Processing message at {message.jump_url}")
    tas_attachments = [a for a in message.attachments if a.filename.endswith('.tas')]
    video_attachments = [a for a in message.attachments if a.filename.rpartition('.')[2] in ('mp4', 'webm', 'gif', 'mkv')]
    has_video = video_attachments or 'youtube.com/watch?v=' in message.content or 'youtu.be/xq3g50e7U6s' in message.content \
                or 'streamable.com/' in message.content or 'gfycat.com/' in message.content

    if has_video:
        log.info("Video found 🍿")
        await message.add_reaction('🍿')

    if len(tas_attachments) == 0:
        log.info("No TAS file found 👍")

        if not has_video:
            await message.add_reaction('👍')

        add_project_log(message)
        log.info("Done processing message")
        return
    elif len(tas_attachments) > 1:
        log.warning(f"Message has {len(tas_attachments)} TAS files. This could break stuff")

    await message.clear_reaction('❌')
    await message.add_reaction('👀')
    global headers
    installation_owner = projects[message.channel.id]['installation_owner']
    headers = {'Authorization': f'token {gen_token.access_token(installation_owner)}', 'Accept': 'application/vnd.github.v3+json'}

    for attachment in tas_attachments:
        log.info(f"Processing {attachment.filename}")
        repo = projects[message.channel.id]['repo']
        is_lobby = projects[message.channel.id]['is_lobby']
        r = requests.get(attachment.url)
        utils.handle_potential_request_error(r, 200)
        file_content = r.content
        old_file_path = get_file_repo_path(message, attachment.filename)
        old_file_content = None

        if old_file_path:
            log.info("Downloading old version of file, for time reference")
            r = requests.get(f'https://api.github.com/repos/{repo}/contents/{old_file_path}', headers=headers)
            utils.handle_potential_request_error(r, 200)
            old_file_content = base64.b64decode(r.json()['content'])

        validation_result = validation.validate(file_content, attachment.filename, message.content, old_file_content, is_lobby)

        if validation_result.valid_tas:
            # I love it when
            # when timesave :)
            # (or drafts)
            log.info(f"Committing {attachment.url} (maybe)")
            commit_status = commit(message, attachment.filename, file_content, validation_result)

            if commit_status:
                history_data = (utils.detailed_user(message), message.channel.id, projects[message.channel.id]['name'], *commit_status, attachment.url)
                history_log.info(history_data)
                log.info("Added to history log")
                await message.add_reaction('📝')
                await utils.edit_pin(message.channel, False)
            else:
                log.info("File is a draft, and committing drafts is disabled for this project 🤘")
                await message.add_reaction('🤘')
        else:
            log.info(f"Warning {utils.detailed_user(message)} about {validation_result.log_text}")
            await message.add_reaction('❌')
            await message.reply(validation_result.warning_text)

        if len(tas_attachments) > 1:
            log.info(f"Done processing {attachment.filename}")

    add_project_log(message)
    await message.clear_reaction('👀')
    log.info("Done processing message")


# assumes already verified TAS
def commit(message: discord.Message, filename: str, content: bytes, validation_result: validation.ValidationResult) -> Optional[tuple]:
    log.info(f"Using project: {projects[message.channel.id]['name']} ({message.channel.id})")
    repo = projects[message.channel.id]['repo']
    data = {'content': base64.b64encode(content).decode('UTF8')}
    author = nicknames[message.author.id] if message.author.id in nicknames else message.author.name
    file_path = get_file_repo_path(message, filename)
    chapter_time = "" if projects[message.channel.id]['is_lobby'] else f" ({validation_result.chapter_time})"

    if file_path:
        draft = True
        timesave = "Updated: " if projects[message.channel.id]['is_lobby'] else f"{validation_result.timesave} "
        data['sha'] = get_sha(repo, file_path)
        data['message'] = f"{timesave}{filename}{chapter_time} from {author}"
    else:
        draft = False
        data['message'] = f"{filename} draft by {author}{chapter_time}"
        subdir = projects[message.channel.id]['subdir']
        file_path = f'{subdir}/{filename}' if subdir else filename
        projects[message.channel.id]['path_cache'][filename] = file_path
        utils.save_projects()

        if not projects[message.channel.id]['commit_drafts']:
            return

    log.info(f"Set commit message to \"{data['message']}\"")
    r = requests.put(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers, data=json.dumps(data))
    utils.handle_potential_request_error(r, 200 if draft else 201)
    commit_url = r.json()['commit']['html_url']
    log.info(f"Successfully committed: {commit_url}")
    return data['message'], commit_url


# if a file exists in the repo, get its path
def get_file_repo_path(message: discord.Message, filename: str) -> Optional[str]:
    project = message.channel.id
    repo = projects[project]['repo']
    path_cache = projects[project]['path_cache']
    project_subdir = projects[message.channel.id]['subdir']

    if filename not in path_cache:
        # walk the repo and cache the path of all TAS files found
        log.info(f"Caching {repo} structure")
        r = requests.get(f'https://api.github.com/repos/{repo}/contents', headers=headers)
        utils.handle_potential_request_error(r, 200)

        for item in r.json():
            if item['type'] == 'dir':
                # recursively get files in dirs (fyi {'recursive': 1} means true, not a depth of 1)
                dir_sha = item['sha']
                r = requests.get(f'https://api.github.com/repos/{repo}/git/trees/{dir_sha}', headers=headers, params={'recursive': 1})
                utils.handle_potential_request_error(r, 200)

                for subitem in r.json()['tree']:
                    if subitem['type'] == 'blob':
                        subitem_name = subitem['path'].split('/')[-1]
                        subitem_full_path = f"{item['name']}/{subitem['path']}"

                        if subitem_full_path.startswith(project_subdir) and subitem_name.endswith('.tas'):
                            path_cache[subitem_name] = subitem_full_path
            elif not project_subdir and item['name'].endswith('.tas'):
                path_cache[item['name']] = item['path']

        utils.save_projects()
        log.info(f"Cached: {path_cache}")

    if filename in path_cache:
        return path_cache[filename]


# we know the file exists, so get its SHA for updating
def get_sha(repo: str, file_path: str) -> str:
    r = requests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    utils.handle_potential_request_error(r, 200)
    repo_contents = r.json()
    log.info(f"Found SHA of {file_path}: {repo_contents['sha']}")
    return repo_contents['sha']


# haven't processed message before, and wasn't posted before project install
def is_processable_message(message: discord.Message) -> bool:
    if message.id in project_logs[message.channel.id] or message.author == client.user or message.type.value == 6:
        return False
    else:
        # because the timestamp is UTC, but the library doesn't seem to know that
        post_time = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        return post_time > projects[message.channel.id]['install_time']


# load the saved message IDs of already committed posts
def load_project_logs():
    for project in projects:
        project_log_path = f'project_logs\\{project}.bin'

        if not os.path.isfile(project_log_path):
            open(project_log_path, 'w').close()
            project_logs[project] = []
            log.info(f"Created {project_log_path}")
        else:
            with open(project_log_path, 'rb') as project_log_db:
                project_log_read = project_log_db.read()

            project_logs[project] = memoryview(project_log_read).cast('Q')


def add_project_log(message: discord.Message):
    project_log_path = f'project_logs\\{message.channel.id}.bin'

    with open(project_log_path, 'ab') as project_log_db:
        # yes this format is basically unnecessary, but I think it's cool :)
        project_log_db.write(message.id.to_bytes(8, byteorder='little'))

    log.info(f"Added message ID {message.id} to {project_log_path}")


def create_loggers() -> (logging.Logger, logging.Logger):
    logger = logging.getLogger('bot')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(filename='bot.log', encoding='UTF8', mode='w')
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))

    history = logging.getLogger('history')
    history.setLevel(logging.DEBUG)
    handler = logging.FileHandler(filename='history.log', encoding='UTF8', mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    history.addHandler(handler)

    gen_token.log = logger
    validation.log = logger
    utils.log = logger
    dm.log = logger
    game_sync.log = logger
    dm.history_log = history

    return logger, history


log, history_log = create_loggers()
projects: Optional[dict] = None
project_logs = {}
nicknames = {234520815658336258: 'Vamp'}
dm.client = client
headers = None


if __name__ == '__main__':
    main()
