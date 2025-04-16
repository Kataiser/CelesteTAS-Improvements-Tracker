import base64
import inspect
import io
import logging
import time
from pathlib import Path

import discord
from discord.ext import tasks

import db
import main
import utils
from constants import admin_user_id


def start_tasks() -> dict[callable, bool]:
    tasks_running = {handle_game_sync_results_task: False,
                     handle_no_game_sync_results_task: False,
                     alert_server_join_task: False,
                     heartbeat_task: False}

    for task in tasks_running:
        tasks_running[task] = task.is_running()

        if not tasks_running[task]:
            task.start()
            log.info(f"Started {task._name}")
            tasks_running[task] = True

    return tasks_running


async def run_and_catch_task(task_function: callable):
    if inspect.iscoroutinefunction(task_function):
        await task_function()
    else:
        task_function()


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

                    await improvements_channel.send(sync_result.data['report_text'], files=files)

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


def heartbeat():
    db.misc.set('heartbeat', {'host_socket': utils.cached_hostname(),
                              'host': utils.host().name,
                              'time': int(time.time())})


client: discord.Client | None = None
log: logging.Logger | utils.LogPlaceholder = utils.LogPlaceholder()
mc_server_log_last_update = None
mc_server_log_last_pos = 0