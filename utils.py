import ctypes
import functools
import logging
import os
import socket
import sys
import time
import tomllib
import traceback
from collections import namedtuple
from typing import Optional, Sized, Union, Tuple

import discord
import niquests
import orjson

import db
from constants import admin_user_id

Host = namedtuple('Host', ('name', 'sleep_scale'))

def plural(count: Union[int, Sized]) -> str:
    if isinstance(count, int):
        return 's' if count != 1 else ''
    else:
        return 's' if len(count) != 1 else ''


def log_error(message: Optional[str] = None, flash_window: bool = True) -> str:
    error = message if message else traceback.format_exc()
    log.error(error)

    if flash_window:
        try:
            ctypes.windll.user32.FlashWindow(ctypes.windll.kernel32.GetConsoleWindow(), True)
        except AttributeError:  # linux
            pass

    return error


async def report_error(client: discord.Client, message: Optional[str] = None):
    error = log_error(message)
    await (await user_from_id(client, admin_user_id)).send(f"```\n{error[-1990:]}```")


def handle_potential_request_error(req: niquests.Response, code: int):
    if req.status_code != code:
        log.warning(f"Bad HTTP status_code: {req.status_code}, should be {code} (url={req.url})")
        log.warning(req.text)


def detailed_user(message: Optional[discord.Message] = None, user: Optional[discord.User] = None) -> Optional[str]:
    if message:
        user = message.author
    elif not user:
        return

    return f'{user.global_name} ({user.name}, {user.id})'


def nickname(author: discord.User) -> str:
    nicknames = {234520815658336258: "Vamp",
                 587491655129759744: "EllaTAS",
                 513223843721117713: "The Senate",
                 671098132959985684: "Mr. Wolf",
                 226515080752267286: "Soloiini",
                 794291191726211103: "Ash",
                 761176028982018048: "Daniell"}

    return nicknames[author.id] if author.id in nicknames else (author.global_name if author.global_name else author.name)


def load_sj_data() -> Tuple[dict, dict]:
    with open('sj.json', 'rb') as sj_file:
        sj_data: dict = orjson.loads(sj_file.read())
        sj_data_filenames = {sj_data[sj_map][4]: sj_map for sj_map in sj_data}
        return sj_data, sj_data_filenames


def log_timestamp() -> str:
    current_time = time.time()
    current_time_local = time.localtime(current_time)
    return time.strftime('%Y-%m-%d %H:%M:%S,', current_time_local) + str(round(current_time % 1, 3))[2:]


def missing_channel_permissions(channel: discord.TextChannel) -> list:
    improvements_channel_permissions = channel.permissions_for(channel.guild.me)
    permissions_needed = {'View Channel': improvements_channel_permissions.view_channel,
                          'Send Messages': improvements_channel_permissions.send_messages,
                          'Read Messages': improvements_channel_permissions.read_messages,
                          'Read Message History': improvements_channel_permissions.read_message_history,
                          'Add Reactions': improvements_channel_permissions.add_reactions,
                          'Manage Messages': improvements_channel_permissions.manage_messages}

    return [perm for perm in permissions_needed if not permissions_needed[perm]]


def get_user_github_account(discord_id: int) -> Optional[list]:
    try:
        return db.githubs.get(discord_id, consistent_read=False)
    except db.DBKeyError:
        return


@functools.cache
def host() -> Host:
    if os.path.isfile('host.toml'):
        with open('host.toml', 'rb') as host_toml:
            host_data = tomllib.load(host_toml)
            return Host(name=host_data['all']['name'],
                        sleep_scale=host_data['game_sync']['sleep_scale'])
    if os.path.isfile('host'):
        with open('host', 'r', encoding='UTF8') as host_file:
            return Host(name=host_file.read().strip('" \n'),
                        sleep_scale=None)
    else:
        log_error("Couldn't determine host for about command")
        return Host(name="Unknown",
                    sleep_scale=None)


def saved_log_name(base_name: str) -> str:
    return f'{base_name}_{int(os.path.getmtime(f'{base_name}.log'))}_{cached_hostname()}.log'


@functools.cache
def cached_hostname() -> str:
    return socket.gethostname()


async def user_from_id(client: discord.Client, user_id: int) -> discord.User:
    user = client.get_user(user_id)

    if not user:
        user = await client.fetch_user(user_id)

    return user


class LogPlaceholder:
    @staticmethod
    def debug(msg):
        print("DEBUG:", msg)

    @staticmethod
    def info(msg):
        print("INFO:", msg)

    @staticmethod
    def warning(msg):
        print("WARNING:", msg, file=sys.stderr)

    @staticmethod
    def error(msg):
        print("ERROR:", msg, file=sys.stderr)

    @staticmethod
    def critical(msg):
        print("CRITICAL:", msg, file=sys.stderr)


log: Union[logging.Logger, LogPlaceholder] = LogPlaceholder()
