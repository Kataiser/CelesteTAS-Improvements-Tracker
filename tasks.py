import base64
import datetime
import functools
import inspect
import io
import logging
import random
import re
import time
import urllib.parse
import zipfile
from collections import namedtuple
from pathlib import Path

import cron_validator
import discord
import requests
from discord.ext import tasks

import db
import main
import spreadsheet
import utils
from constants import admin_user_id


def start_tasks() -> dict[callable, bool]:
    tasks_running = {handle_game_sync_results_task: False,
                     handle_no_game_sync_results_task: False,
                     alert_server_join_task: False,
                     heartbeat_task: False,
                     room_suggestions_task: False}

    for task in tasks_running:
        tasks_running[task] = task.is_running()

        if not tasks_running[task]:
            task.start()
            log.info(f"Started {task._name}")
            tasks_running[task] = True

    return tasks_running


async def run_and_catch_task(task_function: callable):
    if inspect.iscoroutinefunction(task_function):
        try:
            await task_function()
        except Exception:
            await utils.report_error(client)
    else:
        try:
            task_function()
        except Exception:
            utils.log_error()


@tasks.loop(minutes=1)
async def handle_game_sync_results_task():
    await run_and_catch_task(handle_game_sync_results)


@tasks.loop(hours=2)
async def handle_no_game_sync_results_task():
    await run_and_catch_task(handle_no_game_sync_results)


@tasks.loop(seconds=30)
async def alert_server_join_task():
    await run_and_catch_task(alert_server_join)


@tasks.loop(minutes=2)
async def heartbeat_task():
    await run_and_catch_task(heartbeat)


@tasks.loop(minutes=1)
async def room_suggestions_task():
    await run_and_catch_task(room_suggestions)


async def handle_game_sync_results():
    sync_results = db.get_sync_results()

    if not sync_results:
        return

    global client

    for sync_result in sync_results:
        log.info(f"Handling {str(sync_result)}")

        if sync_result.type in (db.SyncResultType.NORMAL, db.SyncResultType.AUTO_DISABLE):
            project_id = sync_result.data['project_id']
            project = db.projects.get(project_id)
            project_name = project['name']
            improvements_channel = client.get_channel(project_id)
            await main.edit_pin(improvements_channel)

        match sync_result.type:
            case db.SyncResultType.NORMAL:
                sync_check_time = project['last_run_validation']
                game_log = sync_result.data['log']
                files = []

                if game_log:
                    files.append(discord.File(io.BytesIO(base64.b64decode(sync_result.data['log'])), filename=f'game_sync_{project_name}_{sync_check_time}.log.gz'))

                    for crash_log_name in sync_result.data['crash_logs']:
                        crash_log_data = sync_result.data['crash_logs'][crash_log_name]
                        files.append(discord.File(io.BytesIO(base64.b64decode(crash_log_data)), filename=crash_log_name))

                    await improvements_channel.send(sync_result.data['report_text'], files=files[:10])

                if sync_result.data['disabled_text']:
                    await improvements_channel.send(sync_result.data['disabled_text'])

            case db.SyncResultType.AUTO_DISABLE:
                await improvements_channel.send(sync_result.data['disabled_text'])

            case db.SyncResultType.REPORTED_ERROR:
                await (await utils.user_from_id(client, admin_user_id)).send(f"<t:{sync_result.data['time']}:R>\n```\n{sync_result.data['error']}```")

            case db.SyncResultType.MAINGAME_COMMIT:
                await (await client.fetch_channel(1323811411226263654)).send(sync_result.data['maingame_message'])

        try:
            db.delete_sync_result(sync_result)
        except Exception as e:
            if "The receipt handle has expired" in str(e):
                utils.log_error()
            else:
                raise

    db.misc.set('last_game_sync_result_time', int(time.time()))


async def handle_no_game_sync_results():
    time_since_last_game_sync_result = time.time() - float(db.misc.get('last_game_sync_result_time'))

    if time_since_last_game_sync_result > 86400:  # 24 hours
        warning_text = f"Last sync check was {round(time_since_last_game_sync_result / 3600, 1)} hours ago"
        log.warning(warning_text)
        await (await utils.user_from_id(client, admin_user_id)).send(warning_text)


async def alert_server_join():
    global mc_server_log_last_update, mc_server_log_last_pos
    log_file = Path('C:/Users/Vamp/Documents/tas_offtopic server/logs/latest.log')

    if not log_file.is_file():
        return

    log_mtime = log_file.stat().st_mtime

    if log_mtime == mc_server_log_last_update:
        return

    first_scan = not mc_server_log_last_update
    mc_server_log_last_update = log_mtime

    with open(log_file, 'rb') as log_file_open:
        log_file_open.seek(mc_server_log_last_pos)
        new_lines = log_file_open.readlines()
        mc_server_log_last_pos = log_file_open.tell()

    if first_scan or not new_lines:
        return

    log.info(f"Minecraft server log has been updated with {len(new_lines)} lines")

    for line in new_lines:
        if (b"joined the game" in line or b"left the game" in line) and b"MechKataiser" not in line:
            await (await utils.user_from_id(client, admin_user_id)).send(f"MC server: `{line.decode('UTF8').rstrip()}`")


def heartbeat(killed=False):
    hb_time = 0 if killed else int(time.time())

    db.misc.set('heartbeat', {'host_socket': utils.cached_hostname(),
                              'host': utils.host().name,
                              'time': hb_time})


async def room_suggestions():
    async def send_message_update_pin(message_text: str, pin_id: int):
        channel = client.get_channel(channel_id)
        message = await channel.send(message_text)

        try:
            pin_message = await channel.fetch_message(pin_id)
        except discord.NotFound:
            pin_id = 0
            pin_message = message

        if pin_id != 0:
            await pin_message.edit(content=message_text)
        else:
            await message.pin()
            project['room_suggestion_pin'] = message.id
            db.projects.set(project_id, project)

    now = datetime.datetime.now(datetime.timezone.utc)
    crons = get_crons()

    for project_id in crons:
        if not cron_validator.CronValidator.match_datetime(crons[project_id], now):
            continue

        project = db.projects.get(project_id)
        repo = project['repo']
        pin_id = int(project['room_suggestion_pin'])
        rooms_index = int(project['room_suggestion_index'])
        channel_id = int(project['room_suggestion_channel'])

        if channel_id == 0:
            log.warning(f"Can't do room improvement suggestion for project \"{project['name']}\" because channel ID is 0")
            continue

        project['room_suggestion_index'] += 1
        db.projects.set(project_id, project)

        if project_id == 1074148268407275520:  # sj
            log.info("Updating room improvement suggestion for SJ")
            sj_maps = [level for level in spreadsheet.sj_data]
            random.Random(project_id + (rooms_index // len(sj_maps))).shuffle(sj_maps)
            chosen_map = sj_maps[rooms_index % len(sj_maps)]
            chosen_map_filename = spreadsheet.sj_data[chosen_map][4]
            log.info(f"Chose {chosen_map} ({chosen_map_filename}), index {rooms_index}/{len(sj_maps)}")
            github_link = f'https://github.com/VampireFlower/StrawberryJamTAS/blob/main/{db.path_caches.get(project_id)[chosen_map_filename]}'
            last_improved_value = spreadsheet.MapRow(chosen_map).improvement_date_cell.value()
            last_improved_timestamp = int(datetime.datetime.strptime(last_improved_value, '%m/%d/%Y').replace(hour=12).timestamp())
            message_text = (f"### Level improvement suggestion\n"
                            f"Level: {chosen_map}\n"
                            f"File: [{chosen_map_filename}](<{github_link}>)\n"
                            f"Last improved: <t:{last_improved_timestamp}:D> (<t:{last_improved_timestamp}:R>)")
            await send_message_update_pin(message_text, pin_id)
            return

        log.info(f"Updating room improvement suggestion for project \"{project['name']}\"")
        r = requests.get(f'https://github.com/{repo}/archive/refs/heads/master.zip', timeout=30)
        utils.handle_potential_request_error(r, 200)
        Room = namedtuple('Room', ['name', 'file', 'line_num'])
        rooms: list[Room] = []

        with zipfile.ZipFile(io.BytesIO(r.content), 'r') as archive_file:
            for file in archive_file.filelist:
                file_path = file.filename.partition('/')[2]

                if [item for item in project['excluded_items'] if file_path.startswith(item)] or not file.filename.endswith('.tas'):
                    continue

                with archive_file.open(file) as file_opened:
                    file_lines = file_opened.read().decode('UTF8').splitlines()

                for line_num, line in enumerate(file_lines):
                    if line.startswith('#lvl_'):
                        rooms.append(Room(line[5:], file_path, line_num + 1))

        random.Random(project_id + (rooms_index // len(rooms))).shuffle(rooms)
        chosen_room = rooms[rooms_index % len(rooms)]
        log.info(f"Chose {chosen_room}, index {rooms_index}/{len(rooms)}")
        github_link = f'https://github.com/{repo}/blob/master/{urllib.parse.quote(chosen_room.file)}#L{chosen_room.line_num}'
        room_display = f"`{chosen_room.name}`"
        maingame_emojis = ''

        if project_id == 598945702554501130:  # maingame
            filename_only = chosen_room.file.rpartition('/')[2]
            berrycamp = {'0 - Prologue': 'prologue/a', '0 - Epilogue': 'epilogue/a', '9': 'farewell/a',
                         '1B': 'city/b', '1C': 'city/c', '1': 'city/a',
                         '2B': 'site/b', '2C': 'site/c', '2': 'site/a',
                         '3B': 'resort/b', '3C': 'resort/c', '3': 'resort/a',
                         '4B': 'ridge/b', '4C': 'ridge/c', '4': 'ridge/a',
                         '5B': 'temple/b', '5C': 'temple/c', '5': 'temple/a',
                         '6B': 'reflection/b', '6C': 'reflection/c', '6': 'reflection/a',
                         '7B': 'summit/b', '7C': 'summit/c', '7': 'summit/a',
                         '8B': 'core/b', '8C': 'core/c', '8': 'core/a'}

            for prefix in berrycamp:
                if filename_only.startswith(prefix):
                    berrycamp_url = f'https://berrycamp.github.io/img/celeste/rooms/{berrycamp[prefix]}/{chosen_room.name.partition(' ')[0]}.png'
                    room_display = f'[`{chosen_room.name}`]({berrycamp_url})'
                    break

            maingame_emojis = get_maingame_emojis(filename_only)

        message_text = (f"### Room improvement suggestion\n"
                        f"Room: {room_display}\n"
                        f"File: [{chosen_room.file} @ line {chosen_room.line_num}](<{github_link}>) {maingame_emojis}")
        await send_message_update_pin(message_text, pin_id)


@functools.cache
def get_crons() -> dict[int, str]:
    crons = {}

    for project in db.projects.get_all():
        project_cron = project['room_suggestion_cron']

        if project_cron:
            if project['room_suggestion_channel'] == 0:
                log.warning(f"Can't add room suggestion cron for project \"{project['name']}\" because channel ID is 0")
            else:
                crons[int(project['project_id'])] = project_cron

    return crons


def get_maingame_emojis(filename: str) -> str:
    filename = filename.removesuffix('.tas')

    match filename:
        case '9NMG':
            return '<:unimpressedbirb:971377619687850034>'
        case '9S':
            return '<:wow:628615320365432853>'
        case '9':
            return '<:creature_f:903066740534149130>'

    match = re_maingame_filenames.match(filename)

    if not match:
        return ''

    emojis = []
    letters = match.group(1)

    for letter in letters:
        match letter:
            case 'A':
                emojis.append(':mountain_snow:')
            case 'B':
                emojis.append('<:heartred:911514304916901920>')
            case 'C':
                emojis.append(':vhs:' if letters.replace('C', '') else '<:heartyellow:916877000680026143>')
            case 'S':
                emojis.append('<:strawberry:916877000487100508>')
            case 'H':
                emojis.append('<:heartblue:916877000612913173>')
            case 'D':
                emojis.append('<:napeline:523614058092429322>')

    if filename.endswith('G'):
        emojis.append('<:goldenberry:916877000755535953>')

    return ' '.join(emojis)


client: discord.Client | None = None
log: logging.Logger | utils.LogPlaceholder = utils.LogPlaceholder()
mc_server_log_last_update = None
mc_server_log_last_pos = 0
re_maingame_filenames = re.compile(r'\d+([A-Z]+)')
