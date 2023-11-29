import base64
import dataclasses
import datetime
import gzip
import io
import logging
import os
import random
import socket
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional

import discord
import requests
import ujson
from discord.ext import tasks

import commands
import db
import gen_token
import spreadsheet
import utils
import validation
from utils import plural


# process a message posted in a registered improvements channel
async def process_improvement_message(message: discord.Message, project: Optional[dict] = None, skip_validation: bool = False) -> bool:
    if not project:
        project = db.projects.get(message.channel.id)

    if not skip_validation and not is_processable_message(message, project):
        return False

    log.info(f"Processing message from {utils.detailed_user(message)} in server {message.guild.name} (project: {project['name']}) at {message.jump_url}")
    tas_attachments = [a for a in message.attachments if a.filename.endswith('.tas')]
    zip_attachments = [a for a in message.attachments if a.filename.endswith('.zip')]
    video_attachments = [a for a in message.attachments if a.filename.rpartition('.')[2] in ('mp4', 'webm', 'gif', 'gifv', 'mkv', 'avi', 'mov', 'm4v')]
    has_video = video_attachments or [s for s in ('youtube.com/watch?v=', 'youtu.be/', 'streamable.com/', 'gfycat.com/') if s in message.content]

    if has_video:
        log.info("Video found 🍿")
        await message.add_reaction('🍿')

    for zip_attachment in zip_attachments:
        log.info(f"Downloading and parsing {zip_attachment.filename} from {zip_attachment.url}")
        r = requests.get(zip_attachment.url)
        utils.handle_potential_request_error(r, 200)

        with zipfile.ZipFile(io.BytesIO(r.content), 'r') as zip_file:
            for file in zip_file.filelist:
                if file.filename.endswith('.tas'):
                    with zip_file.open(file) as file_opened:
                        basename = os.path.basename(file.filename)
                        tas_attachments.append(AttachmentFromZip(basename, f'{zip_attachment.filename}/{file.filename}', file_opened.read()))

    if len(tas_attachments) == 0:
        log.info("No TAS file found 👍")

        if not has_video:
            if "bad bot" in message.content.lower():
                await message.add_reaction('😢')
            elif message.content == '👀':
                await message.add_reaction('👁️')
            else:
                await message.add_reaction('👍')

        add_project_log(message)
        log.info("Done processing message")
        await set_status(message, project['name'])
        return True
    elif len(tas_attachments) > 1:
        log.warning(f"Message has {len(tas_attachments)} TAS files. This could break stuff")
        # TODO: handle this better

    if not skip_validation:
        await message.clear_reaction('❌')
        await message.clear_reaction('⏭')
    await message.add_reaction('👀')
    generate_request_headers(project['installation_owner'])

    for attachment in tas_attachments:
        log.info(f"Processing file {attachment.filename} at {attachment.url}")
        repo = project['repo']
        is_lobby = project['is_lobby']

        if isinstance(attachment, discord.Attachment):
            r = requests.get(attachment.url)
            utils.handle_potential_request_error(r, 200)
            file_content = r.content
        else:  # zip attachment
            file_content = attachment.content

        filename, filename_no_underscores = attachment.filename, attachment.filename.replace('_', ' ')
        db.path_caches.enable_cache()

        if filename not in db.path_caches.get(message.channel.id) and filename_no_underscores in db.path_caches.get(message.channel.id):
            log.info(f"Considering {filename} as {filename_no_underscores}")
            filename = filename_no_underscores

        old_file_path = get_file_repo_path(message.channel.id, filename)
        old_file_content = None

        if old_file_path:
            log.info("Downloading old version of file, for time reference")
            r = requests.get(f'https://api.github.com/repos/{repo}/contents/{old_file_path}', headers=headers)
            r_json = ujson.loads(r.content)

            if r.status_code == 404 and 'message' in r_json and r_json['message'] == "Not Found":
                db.path_caches.remove_file(message.channel.id, filename)
                log.warning("File existed in path cache but doesn't seem to exist in repo")
            else:
                utils.handle_potential_request_error(r, 200)
                old_file_content = base64.b64decode(r_json['content'])
        else:
            log.info("No old version of file exists")

        validation_result = validation.validate(file_content, filename, message, old_file_content, is_lobby, project['ensure_level'], skip_validation)
        db.path_caches.disable_cache()

        if validation_result.valid_tas:
            # I love it when
            # when timesave :)
            # (or drafts)
            file_content = convert_line_endings(file_content, old_file_content)
            commit_status = commit(project, message, filename, file_content, validation_result)
            project['last_commit_time'] = int(time.time())

            if commit_status:
                history_data = (utils.detailed_user(message), message.channel.id, project['name'], *commit_status, attachment.url)
                db.history_log.set(utils.log_timestamp(), str(history_data))
                log.info("Added to history log")
                await message.add_reaction('🚧' if validation_result.wip else '📝')
                await edit_pin(message.channel)
            else:
                log.info("File is a draft, and committing drafts is disabled for this project 🤘")
                await message.add_reaction('🤘')

            if validation_result.sj_data:
                spreadsheet.update_stats(attachment.filename, validation_result)

            if project['sync_check_timed_out']:
                project['sync_check_timed_out'] = False
                project['do_run_validation'] = True
                log.info("Reenabled sync checking")
                await message.channel.send("Reenabled sync checking for this project.")

            db.projects.set(message.channel.id, project)
            update_contributors(message.author, message.channel.id, project)
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

    await message.clear_reaction('👀')
    log.info("Done processing message")
    await set_status(message, project['name'])
    return True


# assumes already verified TAS
def commit(project: dict, message: discord.Message, filename: str, content: bytes, validation_result: validation.ValidationResult) -> Optional[tuple]:
    log.info("Potentially committing file")
    repo = project['repo']
    data = {'content': base64.b64encode(content).decode('UTF8')}
    author = utils.nickname(message.author)
    file_path = get_file_repo_path(message.channel.id, filename)
    chapter_time = f" ({validation_result.finaltime})" if validation_result.finaltime else ""
    user_github_account = utils.get_user_github_account(message.author.id)

    if file_path:
        draft = False
        timesave = f"{validation_result.timesave} " if validation_result.timesave else "Updated: "
        data['sha'] = get_sha(repo, file_path)
        data['message'] = f"{timesave}{filename}{chapter_time} from {author}"
    else:
        draft = True
        data['message'] = f"{filename} {'WIP' if validation_result.wip else 'draft'} by {author}{chapter_time}"
        subdir = project['subdir']
        file_path = f'{subdir}/{filename}' if subdir else filename
        db.path_caches.add_file(message.channel.id, filename, file_path)

        if not project['commit_drafts']:
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
    path_cache = db.path_caches.get(project_id)

    if filename not in path_cache:
        path_cache = generate_path_cache(project_id)

    if filename in path_cache:
        return path_cache[filename]


# walk the project's repo and cache the path of all TAS files found
def generate_path_cache(project_id: int, project: Optional[dict] = None) -> dict:
    if not project:
        project = db.projects.get(project_id)

    repo = project['repo']
    project_subdir = project['subdir']
    project_subdir_base = project_subdir.partition('/')[0]
    log.info(f"Caching {repo} structure ({project_subdir=})")
    r = requests.get(f'https://api.github.com/repos/{repo}/contents', headers=headers)
    utils.handle_potential_request_error(r, 200)
    contents_json = ujson.loads(r.content)
    path_cache = {}  # always start from scratch

    if 'message' in contents_json and contents_json['message'] == 'This repository is empty.':
        log.info("Repo is empty")
    else:
        for item in contents_json:
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
                            path_cache[subitem_name] = subitem_full_path
            elif not project_subdir and item['name'].endswith('.tas'):
                path_cache[item['name']] = item['path']

    db.path_caches.set(project_id, path_cache)
    log.info(f"Cached: {path_cache}")
    return path_cache


# we know the file exists, so get its SHA for updating
def get_sha(repo: str, file_path: str) -> str:
    r = requests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    utils.handle_potential_request_error(r, 200)
    repo_contents = ujson.loads(r.content)
    log.info(f"Found SHA of {file_path}: {repo_contents['sha']}")
    return repo_contents['sha']


# haven't processed message before, and wasn't posted before project install
def is_processable_message(message: discord.Message, project: dict) -> bool:
    if message.id in db.project_logs.get(message.channel.id) or message.author.id == 970375635027525652 or (safe_mode and message.channel.id not in safe_projects):
        return False
    else:
        # because the timestamp is UTC, but the library doesn't seem to know that
        post_time = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        return post_time > project['install_time']


async def edit_pin(channel: discord.TextChannel, create_from_project: Optional[dict] = None):
    if create_from_project:
        project = create_from_project
    else:
        project = db.projects.get(channel.id)

    ensure_level = project['ensure_level']
    desyncs = project['desyncs']
    desyncs_text = "\n"
    filetimes_text = ""
    lobby_text = "Since this is channel is for a lobby, this is not automatically validated. " if project['is_lobby'] else ""
    level_text_ensure = ", the name of the level/map," if ensure_level else ''
    level_text_not_ensure = "" if ensure_level else " the name of the level/map,"
    maingame_times = ("1A (49.130)", "1B (1:04.838)", "1C (15.147)", "2A (1:25.034)", "2B (1:15.667)", "2C (19.414)", "3A (3:14.310)", "3B (1:28.349)", "3C (15.878)", "4A (1:46.794)",
                      "4B (2:00.819)", "4C (24.905)", "5A (3:10.077)", "5B (1:41.660)", "5C (16.337)", "6A (4:35.621)", "6B (3:15.296)", "6C (21.607)", "7A (6:39.636)", "7B (4:41.588)",
                      "7C (34.153)", "8A (2:24.364)", "8B (2:04.406)", "8C (22.270)")
    example_timesave = f"-{round(random.triangular(1, 50, 0))}f {random.choice(maingame_times)}"

    text = "Welcome to the **{0} TAS project!** This improvements channel is in part managed by this bot, which automatically verifies and commits files. When posting " \
           f"a file, please include the amount of frames saved{level_text_ensure} and the ChapterTime of the file, (ex: `{example_timesave}`). {lobby_text}" \
           f"Room(s) affected is ideal, and{level_text_not_ensure} previous ChapterTime, category affected, and video are optional." \
           "\n\nRepo: [{8}](<{1}>) (using [Github Desktop](<https://desktop.github.com/>) is recommended)" \
           "\n[Package download](<{2}>)" \
           "\nAdmin{6}: {3}" \
           "\nLast sync check (start time): {4}{5}{7}" \
           "\nBot reactions key:" \
           "\n```" \
           "\n📝 = Successfully verified and committed" \
           "\n👀 = Currently processing file" \
           "\n❌ = Invalid TAS file or post" \
           "\n⏭ = React to commit invalid post anyway" \
           "\n👍 = Non-TAS containing message" \
           "\n🤘 = Successfully verified draft but didn't commit" \
           "\n🚧 = Committed WIP file" \
           "\n🍿 = Video in message```"

    if project['do_run_validation']:
        last_run = project['last_run_validation']
        filetimes = dict(sorted(project['filetimes'].items()))

        if last_run:
            sync_timestamp = f"<t:{last_run}> (<t:{last_run}:R>)"
        else:
            sync_timestamp = "`Not yet run`"

        if desyncs:
            desyncs_formatted = '\n'.join(desyncs)
            desyncs_text = f"\n\nDesyncing file{plural(desyncs)}:\n```\n{desyncs_formatted}```"

        if filetimes:
            filetimes_formatted = '\n'.join([f"{file[:-4]}: {filetimes[file]}" for file in filetimes])
            filetimes_text = f"\nFullgame file time{plural(filetimes)} (updates on sync check):\n```\n{filetimes_formatted}```"
    else:
        sync_timestamp = "`Disabled`"

    name = project['name']
    repo = project['repo']
    pin = project['pin']
    subdir = project['subdir']
    admins = ', '.join([f'<@{admin}>' for admin in project['admins']])
    repo_url = f'https://github.com/{repo}/tree/HEAD/{subdir}' if subdir else f'https://github.com/{repo}'
    package_url = f'https://download-directory.github.io/?url=https://github.com/{repo}/tree/HEAD/{urllib.parse.quote(subdir)}' if subdir else \
                  f'https://github.com/{repo}/archive/refs/heads/master.zip'
    repo_full = f'{repo}/{subdir}' if subdir else repo
    text_out = text.format(name, repo_url, package_url, admins, sync_timestamp, desyncs_text, plural(project['admins']), filetimes_text, repo_full)

    if len(text_out) > 1900:
        log.warning(f"Pin text is too long ({len(text_out)} chars), trimming")
        text_out = text_out[:1900]

    if create_from_project:
        log.info("Creating pin")
        return await channel.send(text_out)
    else:
        pin_message = channel.get_partial_message(pin)
        await pin_message.edit(content=text_out)
        log.info("Edited pin")
        return pin_message


@dataclasses.dataclass
class AttachmentFromZip:
    filename: str
    url: str
    content: bytes


def convert_line_endings(tas: bytes, old_tas: Optional[bytes]) -> bytes:
    uses_crlf = tas.count(b'\r\n') >= tas.count(b'\n')

    if old_tas:
        old_uses_crlf = old_tas.count(b'\r\n') >= old_tas.count(b'\n')

        if uses_crlf == old_uses_crlf:
            return tas

        if old_uses_crlf:
            log.info("Converted from LF to CRLF due to old file")
            return tas.replace(b'\n', b'\r\n')

        log.info("Converted from CRLF to LF due to old file")
        return tas.replace(b'\r\n', b'\n')

    if uses_crlf:
        return tas
    else:
        log.info("Converted from LF to CRLF")
        return tas.replace(b'\n', b'\r\n')


@tasks.loop(minutes=1)
async def handle_game_sync_results():
    sync_results_found = db.sync_results.get_all()

    if not sync_results_found:
        return

    global client

    for sync_result in sync_results_found:
        project_id = int(sync_result['project_id'])
        project = db.projects.get(project_id)
        project_name = project['name']
        log.info(f"Handling game sync result for project {project_name}")
        report_text = sync_result['report_text']
        improvements_channel = client.get_channel(project_id)
        await edit_pin(improvements_channel)

        if report_text:
            if sync_result['log']:
                sync_check_time = project['last_run_validation']
                sync_log = discord.File(io.BytesIO(sync_result['log'].encode('UTF8')), filename=f'game_sync_{project_name}_{sync_check_time}.log')
                await improvements_channel.send(report_text, file=sync_log)
            else:
                await improvements_channel.send(report_text)

        db.sync_results.delete_item(project_id)


def update_contributors(contributor: discord.User, project_id: int, project: dict):
    try:
        project_contributors = db.contributors.get(project_id, keep_primary_key=False)
    except db.DBKeyError:
        project_contributors = {}
        log.info("Created contributors entry for project")

    contributor_id = str(contributor.id)

    if contributor_id in project_contributors:
        project_contributors[contributor_id]['count'] += 1
        log.info(f"Incremented contributor: {contributor_id} = {project_contributors[contributor_id]}")
    else:
        project_contributors[contributor_id] = {'name': utils.nickname(contributor), 'count': 1}
        log.info(f"Created contributor: {contributor_id} = {project_contributors[contributor_id]}")

    db.contributors.set(project_id, project_contributors)

    if not project['use_contributors_file']:
        log.info("Not updating Contributors.txt")
        return

    contributor_names = [project_contributors[id_]['name'] for id_ in project_contributors]
    repo = project['repo']
    contributors_txt_path = f'{project['subdir']}/Contributors.txt' if project['subdir'] else 'Contributors.txt'
    r = requests.get(f'https://api.github.com/repos/{repo}/contents/{contributors_txt_path}', headers=headers)
    r_json = ujson.loads(r.content)

    if r.status_code == 404 and 'message' in r_json and r_json['message'] == "Not Found":
        commit_message = "Created Contributors.txt"
        created_file = True
        do_commit = True
    else:
        utils.handle_potential_request_error(r, 200)
        existing_contributors = base64.b64decode(r_json['content']).decode('UTF8').splitlines()
        contributors_added = []
        created_file = False
        do_commit = False

        for db_contributor in contributor_names:
            if db_contributor not in existing_contributors:
                existing_contributors.append(db_contributor)
                contributors_added.append(db_contributor)
                do_commit = True

        commit_message = f"Added {', '.join(sorted(contributors_added, key=str.casefold))} to Contributors.txt"

    if do_commit:
        file_data = '\n'.join(sorted(contributor_names, key=str.casefold)).encode('UTF8')
        commit_data = {'content': base64.b64encode(file_data).decode('UTF8'), 'message': commit_message}

        if not created_file:
            commit_data['sha'] = get_sha(repo, contributors_txt_path)

        log.info(commit_message)
        r = requests.put(f'https://api.github.com/repos/{repo}/contents/{contributors_txt_path}', headers=headers, data=ujson.dumps(commit_data))
        utils.handle_potential_request_error(r, 201 if created_file else 200)


async def set_status(message: Optional[discord.Message] = None, project_name: Optional[str] = None):
    if message:
        status = f"{projects_count()} TAS projects, last processed post from {utils.nickname(message.author)} in \"{project_name}\""
        db.misc.set('status', status)
    else:
        try:
            status = db.misc.get('status')
        except db.DBKeyError:
            status = f"{projects_count()} TAS projects"
            db.misc.set('status', status)

    log.info(f"Setting status to \"Watching {status}\"")
    await client.change_presence(status=discord.Status.online, activity=discord.Activity(name=status, type=discord.ActivityType.watching))


def projects_count() -> int:
    return len(fast_project_ids - inaccessible_projects)


def add_project_log(message: discord.Message):
    project_logs = db.project_logs.get(message.channel.id)
    project_logs.append(message.id)
    db.project_logs.set(message.channel.id, project_logs)


def generate_request_headers(installation_owner: str, min_time: int = 30):
    global headers
    headers = {'Authorization': f'token {gen_token.access_token(installation_owner, min_time)}', 'Accept': 'application/vnd.github.v3+json'}


def create_logger(name: str) -> logging.Logger:
    filename = f'{name}.log'

    # backup old logs
    if os.path.isfile(filename):
        with open(filename, 'rb') as old_log:
            old_log_data = old_log.read()

        with gzip.open(Path(f'log_history/{name}_{int(os.path.getmtime(filename))}_{socket.gethostname()}.log.gz'), 'wb') as old_log_gzip:
            old_log_gzip.write(old_log_data)

    logger = logging.getLogger('bot')
    logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(filename=filename, encoding='UTF8', mode='w')
    log_formatter = logging.Formatter('%(asctime)s:%(levelname)s: %(message)s')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_formatter)
    logger.addHandler(stdout_handler)
    logger.info("Log created")

    global log
    log = logger
    gen_token.log = logger
    validation.log = logger
    utils.log = logger
    commands.log = logger
    spreadsheet.log = logger

    return logger


log: Optional[logging.Logger] = None
headers = None
login_time = None
client: Optional[discord.Client] = None
safe_mode = None
safe_projects = (970380662907482142, 973793458919723088, 975867007868235836, 976903244863381564, 1067206696927248444)
inaccessible_projects = set(safe_projects)
fast_project_ids = set()
