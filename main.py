import base64
import dataclasses
import datetime
import io
import logging
import os
import random
import re
import sys
import time
import tomllib
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional, Union

import discord
import niquests
import orjson

import commands
import db
import gen_token
import project_editor
import spreadsheet
import tasks
import utils
import validation
from tasks import start_tasks
from utils import plural


# process a message posted in a registered improvements channel
async def process_improvement_message(message: discord.Message, project: Optional[dict] = None, skip_validation: bool = False) -> bool:
    start_tasks()

    if not project:
        project = db.projects.get(message.channel.id)

    if not skip_validation and not is_processable_message(message, project):
        return False

    if message.channel.id == 1202709072718200842:  # celeste 64
        skip_validation = True

    log.info(f"Processing message from {utils.detailed_user(message)} in server {message.guild.name} (project: {project['name']})\n{message.jump_url}\n{message.content}")
    tas_attachments = [a for a in message.attachments if a.filename.endswith('.tas')]
    zip_attachments = [a for a in message.attachments if a.filename.endswith('.zip')]
    video_attachments = [a for a in message.attachments if a.filename.rpartition('.')[2] in ('mp4', 'webm', 'gif', 'gifv', 'mkv', 'avi', 'mov', 'm4v')]
    has_video = video_attachments or [s for s in ('youtube.com/watch?v=', 'youtu.be/', 'streamable.com/', 'gfycat.com/') if s in message.content]

    if has_video:
        log.info("Video found 🍿")
        await message.add_reaction('🍿')

    for zip_attachment in zip_attachments:
        log.info(f"Downloading and parsing {zip_attachment.filename} from {zip_attachment.url}")
        r = niquests.get(zip_attachment.url)
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

        if isinstance(attachment, AttachmentFromZip):
            file_content = attachment.content
        else:
            r = niquests.get(attachment.url)
            utils.handle_potential_request_error(r, 200)
            file_content = r.content

        filename, filename_no_underscores = attachment.filename, attachment.filename.replace('_', ' ')
        db.path_caches.enable_cache()

        if filename not in db.path_caches.get(message.channel.id) and filename_no_underscores in db.path_caches.get(message.channel.id):
            log.info(f"Considering {filename} as {filename_no_underscores}")
            filename = filename_no_underscores

        old_file_content = download_old_file(message.channel.id, repo, filename)
        validation_result = validation.validate(file_content, filename, message, old_file_content, project, skip_validation)
        db.path_caches.disable_cache()

        if validation_result.valid_tas:
            # I love it when
            # when timesave :)
            # (or drafts)
            file_content = convert_line_endings(file_content, old_file_content)
            commit_status = commit(project, message, filename, file_content, validation_result)
            project['last_commit_time'] = int(time.time())

            # try to only add to project log if not already added
            if not skip_validation or message.id not in db.project_logs.get(message.channel.id):
                add_project_log(message)

            if commit_status:
                await message.add_reaction('🚧' if validation_result.wip else '📝')
                history_data = (utils.detailed_user(message), message.channel.id, project['name'], *commit_status, attachment.url)
                db.history_log.set(utils.log_timestamp(), str(history_data))
                log.info("Added to history log")
                await edit_pin(message.channel)
            else:
                log.info("File is a draft, and committing drafts is disabled for this project 🤘")
                await message.add_reaction('🤘')

            if project['is_lobby'] and project['lobby_sheet_cell']:
                if validation_result.finaltime_frames is not None:
                    spreadsheet_id, _, cell = project['lobby_sheet_cell'].partition('/')
                    write_lobby_sheet(spreadsheet_id, cell, filename, validation_result.finaltime_frames)

            if validation_result.sj_data:
                try:
                    spreadsheet.update_stats(attachment.filename, validation_result)
                except spreadsheet.SheetReadError:  # this just happens sometimes, whatever
                    pass

            if project['sync_check_timed_out']:
                project['sync_check_timed_out'] = False
                project['do_run_validation'] = True
                log.info("Reenabled sync checking")
                await message.channel.send("Reenabled sync checking for this project.")

            db.projects.set(message.channel.id, project)
            update_contributors(message.author, message.channel.id, project)
        else:
            log_messages = ", ".join(validation_result.log_text)
            log.info(f"Warning {utils.detailed_user(message)} about {log_messages}")
            add_project_log(message)
            await message.add_reaction('❌')
            await message.add_reaction('⏭')

            if len(validation_result.warning_text) == 1:
                warnings = validation_result.warning_text[0]
            else:
                warnings = format_markdown_list(validation_result.warning_text)

            if len(tas_attachments) > 1:
                await message.reply(f"`{attachment.filename}`\n{warnings}")
            else:
                await message.reply(warnings)

        if len(tas_attachments) > 1:
            log.info(f"Done processing {filename}")

    await message.clear_reaction('👀')
    log.info("Done processing message")
    await set_status(message, project['name'])
    return True


def write_lobby_sheet(spreadsheet_id: str, table_start: str, filename: str, frames: int):
    from_to = re_lobby_filename.match(filename)
    if not from_to:
        return

    from_idx = int(from_to[1])
    to_idx = int(from_to[2])

    connection_cell = spreadsheet.offset_cell(table_start, column_offset=to_idx, row_offset=from_idx)
    log.info(f"Updating connection {from_idx}-{to_idx} at {connection_cell} to {frames}f")
    spreadsheet.write_sheet(spreadsheet_id, connection_cell, [[str(frames)]])


def format_markdown_list(elements: list[str]) -> str:
    return "- " + "\n- ".join(elements)


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
        data['message'] = f"{timesave}{filename}{chapter_time} from {author}\n\n{message.jump_url}\n{message.content}"
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

    log.info(f"Set commit message to \"{data['message'].partition('\n')[0]}\" (truncated)")
    r = niquests.put(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers, data=orjson.dumps(data))
    utils.handle_potential_request_error(r, 201 if draft else 200)
    commit_url = orjson.loads(r.content)['commit']['html_url']
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
    excluded_items = project['excluded_items']
    project_subdir_base = project_subdir.partition('/')[0]
    log.info(f"Caching {repo} structure ({project_subdir=})")
    r = niquests.get(f'https://api.github.com/repos/{repo}/contents', headers=headers)
    utils.handle_potential_request_error(r, 200)
    contents_json = orjson.loads(r.content)
    studioconfig_path = None
    path_cache = {}  # always start from scratch

    if excluded_items:
        log.info(f"Excluded items: {excluded_items}")

    if 'message' in contents_json and contents_json['message'] == 'This repository is empty.':
        log.info("Repo is empty")
    else:
        for item in contents_json:
            if item['name'] in excluded_items:
                continue

            if item['name'] == '.studioconfig.toml' and not project_subdir:
                studioconfig_path = '.studioconfig.toml'

            if item['type'] == 'dir' and (item['name'].startswith(project_subdir_base) if project_subdir else True):
                # recursively get files in dirs (fyi {'recursive': 1} means true, not a depth of 1)
                dir_sha = item['sha']
                r = niquests.get(f'https://api.github.com/repos/{repo}/git/trees/{dir_sha}', headers=headers, params={'recursive': 1})
                utils.handle_potential_request_error(r, 200)

                for subitem in orjson.loads(r.content)['tree']:
                    if subitem['type'] == 'blob':
                        subitem_name = subitem['path'].split('/')[-1]
                        subitem_full_path = f"{item['name']}/{subitem['path']}"
                        in_subdir = subitem_full_path.startswith(project_subdir) if project_subdir else True

                        if subitem_name.endswith('.tas') and in_subdir and subitem_name not in excluded_items:
                            path_cache[subitem_name] = subitem_full_path

                        if subitem_name == '.studioconfig.toml' and in_subdir:
                            studioconfig_path = subitem_full_path
            elif not project_subdir and item['name'].endswith('.tas'):
                path_cache[item['name']] = item['path']

    db.path_caches.set(project_id, path_cache)
    log.info(f"Cached: {path_cache}")
    previous_room_indexing_includes_reads = project['room_indexing_includes_reads']
    room_indexing_includes_reads = False

    if studioconfig_path:
        try:
            r = niquests.get(f'https://api.github.com/repos/{repo}/contents/{studioconfig_path}', headers=headers)
            r_json = orjson.loads(r.content)
            utils.handle_potential_request_error(r, 200)
            studioconfig_data = base64.b64decode(r_json['content']).decode('UTF8')
            studioconfig_parsed = tomllib.loads(studioconfig_data)

            if 'RoomLabelIndexing' in studioconfig_parsed and studioconfig_parsed['RoomLabelIndexing'] == 'IncludeReads':
                log.info("Found IncludeReads RoomLabelIndexing")
                room_indexing_includes_reads = True
        except Exception:
            utils.report_error(client)

    if previous_room_indexing_includes_reads != room_indexing_includes_reads:
        log.info(f"Set room_indexing_includes_reads to {room_indexing_includes_reads}")
        project['room_indexing_includes_reads'] = room_indexing_includes_reads
        db.projects.set(project['project_id'], project)

    return path_cache


# we know the file exists, so get its SHA for updating
def get_sha(repo: str, file_path: str) -> str:
    r = niquests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    utils.handle_potential_request_error(r, 200)
    repo_contents = orjson.loads(r.content)
    log.info(f"Found SHA of {file_path}: {repo_contents['sha']}")
    return repo_contents['sha']


# haven't processed message before, and wasn't posted before project install
def is_processable_message(message: discord.Message, project: dict) -> bool:
    if message.id in db.project_logs.get(message.channel.id) or message.author == client.user or (safe_mode and message.channel.id not in safe_projects) or not project['enabled']:
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

        if project['project_id'] == 598945702554501130:
            for file in tuple(filetimes.keys()):
                if file.startswith('0 - '):
                    filetimes[file.removeprefix('0 - ')] = filetimes[file]

                del filetimes[file]

        if last_run:
            sync_timestamp = f"<t:{last_run}:R>"
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
    repo_url = f'https://github.com/{repo}/tree/HEAD/{urllib.parse.quote(subdir)}' if subdir else f'https://github.com/{repo}'
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


def download_old_file(project_id: int, repo: str, filename: str, path_cache: Optional[dict] = None) -> Optional[bytes]:
    if path_cache:
        old_file_path = path_cache[filename] if filename in path_cache else None
    else:
        old_file_path = get_file_repo_path(project_id, filename)

    if old_file_path:
        if path_cache is None:
            log.info("Downloading old version of file, for time reference")

        r = niquests.get(f'https://api.github.com/repos/{repo}/contents/{old_file_path}', headers=headers)
        r_json = orjson.loads(r.content)

        if r.status_code == 404 and 'message' in r_json:
            if path_cache is None:
                log.warning("File existed in path cache but doesn't seem to exist in repo. Retrying download with updated path cache")
                new_path_cache = generate_path_cache(project_id)
                return download_old_file(project_id, repo, filename, new_path_cache)
            else:
                log.warning("File not available")
        else:
            utils.handle_potential_request_error(r, 200)
            return base64.b64decode(r_json['content'])
    else:
        log.info("No old version of file exists")


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

    if project['contributors_file_path'] in ('.', '') and not project['subdir']:
        contributors_txt_path = 'Contributors.txt'
    elif project['contributors_file_path']:
        contributors_txt_path = f"{project['contributors_file_path']}/Contributors.txt"
    else:
        contributors_txt_path = f"{project['subdir']}/Contributors.txt"

    db_contributor_names = [project_contributors[id_]['name'] for id_ in project_contributors]
    repo = project['repo']
    r = niquests.get(f'https://api.github.com/repos/{repo}/contents/{contributors_txt_path}', headers=headers)
    r_json = orjson.loads(r.content)

    if r.status_code == 404 and 'message' in r_json and r_json['message'] in ("Not Found", "This repository is empty."):
        commit_message = "Created Contributors.txt"
        created_file = True
        do_commit = True
        repo_contributors = db_contributor_names
    else:
        utils.handle_potential_request_error(r, 200)
        repo_contributors = base64.b64decode(r_json['content']).decode('UTF8').splitlines()
        contributors_added = []
        created_file = False
        do_commit = False

        for db_contributor in db_contributor_names:
            if db_contributor not in repo_contributors:
                repo_contributors.append(db_contributor)
                contributors_added.append(db_contributor)
                do_commit = True

        commit_message = f"Added {', '.join(sorted(contributors_added, key=str.casefold))} to Contributors.txt"

    if do_commit:
        file_data = '\n'.join(sorted(repo_contributors, key=str.casefold)).encode('UTF8')
        commit_data = {'content': base64.b64encode(file_data).decode('UTF8'), 'message': commit_message}

        if not created_file:
            commit_data['sha'] = get_sha(repo, contributors_txt_path)

        log.info(commit_message)
        r = niquests.put(f'https://api.github.com/repos/{repo}/contents/{contributors_txt_path}', headers=headers, data=orjson.dumps(commit_data))
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


def create_logger(name: str, use_file_handler: bool = True) -> logging.Logger:
    filename = f'{name}.log'

    # backup old logs
    if os.path.isfile(filename) and use_file_handler:
        if not os.path.isdir('log_history'):
            os.mkdir('log_history')
            time.sleep(0.2)

        with open(filename, 'rb') as old_log, open(Path(f'log_history/{utils.saved_log_name(name)}'), 'wb') as old_log_backup:
            old_log_backup.write(old_log.read())

    logger = logging.getLogger('bot')
    logger.setLevel(logging.DEBUG)
    log_formatter = logging.Formatter('%(asctime)s:%(levelname)s: %(message)s')

    if use_file_handler:
        file_handler = logging.FileHandler(filename=filename, encoding='UTF8', mode='w')
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_formatter)
    logger.addHandler(stdout_handler)

    global log
    log = logger
    gen_token.log = logger
    validation.log = logger
    utils.log = logger
    commands.log = logger
    spreadsheet.log = logger
    project_editor.log = logger
    tasks.log = logger

    logger.info(f"Log created, host = {utils.host().name}")
    return logger


log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()
headers = None
login_time = None
client: Optional[discord.Client] = None
safe_mode = None
safe_projects = (970380662907482142, 973793458919723088, 975867007868235836, 976903244863381564, 1067206696927248444)
inaccessible_projects = set(safe_projects)
fast_project_ids = set()
re_lobby_filename = re.compile(r'.+_(\d+)-(\d+)\.tas')
