import base64
import datetime
import inspect
import io
import logging
import random
import time
import urllib.parse
import zipfile
from collections import namedtuple
from pathlib import Path

import discord
import requests
from discord.ext import tasks

import db
import main
import utils
from constants import admin_user_id


def start_tasks() -> dict[callable, bool]:
    tasks_running = {handle_game_sync_results_task: False,
                     handle_no_game_sync_results_task: False,
                     alert_server_join_task: False,
                     heartbeat_task: False,
                     daily_maingame_room_task: False}

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


@tasks.loop(time=datetime.time(hour=9, tzinfo=datetime.timezone.utc))
async def daily_maingame_room_task():
    await run_and_catch_task(daily_maingame_room)


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


async def daily_maingame_room():
    log.info("Updating daily maingame room")
    r = requests.get('https://github.com/VampireFlower/CelesteTAS/archive/refs/heads/master.zip', timeout=30)
    utils.handle_potential_request_error(r, 200)
    Room = namedtuple('Room', ['name', 'file', 'line_num'])
    rooms: list[Room] = []

    with zipfile.ZipFile(io.BytesIO(r.content), 'r') as archive_file:
        for file in archive_file.filelist:
            if not file.filename.endswith('.tas'):
                continue

            with archive_file.open(file) as file_opened:
                file_path = file.filename.partition('/')[2]
                file_lines = file_opened.read().decode('UTF8').splitlines()

                for line_num, line in enumerate(file_lines):
                    if line.startswith('#lvl_'):
                        rooms.append(Room(line[5:], file_path, line_num + 1))

    berrycamp = {'0 - Prologue': 'prologue/a', '0 - Epilogue': 'epilogue/a', '9': 'farewell/a',
                 '1B': 'city/b', '1C': 'city/c', '1': 'city/a',
                 '2B': 'site/b', '2C': 'site/c', '2': 'site/a',
                 '3B': 'resort/b', '3C': 'resort/c', '3': 'resort/a',
                 '4B': 'ridge/b', '4C': 'ridge/c', '4': 'ridge/a',
                 '5B': 'temple/b', '5C': 'temple/c', '5': 'temple/a',
                 '6B': 'reflection/b', '6C': 'reflection/c', '6': 'reflection/a',
                 '7B': 'summit/b', '7C': 'summit/c', '7': 'summit/a',
                 '8B': 'core/b', '8C': 'core/c', '8': 'core/a'}

    days_since_init = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime(2025, 5, 30, tzinfo=datetime.timezone.utc)).days
    random.Random(1376210150985170987).shuffle(rooms)
    chosen_room = rooms[days_since_init]
    log.info(f"Chose {chosen_room}, index {days_since_init}")
    github_link = f'https://github.com/VampireFlower/CelesteTAS/blob/master/{urllib.parse.quote(chosen_room.file)}#L{chosen_room.line_num}'
    berrycamp_files1 = []
    berrycamp_files2 = []

    for prefix in berrycamp:
        if chosen_room.file.removeprefix('202/').startswith(prefix):
            room_trimmed = chosen_room.name.partition(' ')[0]
            r = requests.get(f'https://berrycamp.github.io/img/celeste/rooms/{berrycamp[prefix]}/{room_trimmed}.png', timeout=30)
            utils.handle_potential_request_error(r, 200)
            berrycamp_files1 = [discord.File(io.BytesIO(r.content), filename=f'{room_trimmed}.png')]
            berrycamp_files2 = [discord.File(io.BytesIO(r.content), filename=f'{room_trimmed}.png')]
            break

    message = (f"### Daily maingame room to improve\n"
               f"Room: `{chosen_room.name}`\n"
               f"File: [{chosen_room.file} @ line {chosen_room.line_num}](<{github_link}>)\n"
               f"<t:{int(time.time())}:F>\n")
    channel = client.get_channel(1376210150985170987)
    await channel.send(message, files=berrycamp_files1)
    await channel.get_partial_message(1377721486298710159).edit(content=message, attachments=berrycamp_files2)


client: discord.Client | None = None
log: logging.Logger | utils.LogPlaceholder = utils.LogPlaceholder()
mc_server_log_last_update = None
mc_server_log_last_pos = 0
