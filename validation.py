import functools
import logging
import re
from typing import List, Optional, Tuple

import discord

from utils import projects


class ValidationResult:
    def __init__(self, valid_tas: bool, warning_text: str = None, log_text: str = None, chapter_time: str = None, timesave: str = None):
        self.valid_tas = valid_tas
        self.warning_text = warning_text
        self.log_text = log_text
        self.timesave = timesave
        self.chapter_time = chapter_time

        if valid_tas:
            log.info("TAS file and improvement post have been validated")


def validate(tas: bytes, filename: str, message: discord.Message, old_tas: Optional[bytes], lobby_channel: bool) -> ValidationResult:
    log.info(f"Validating{' lobby file' if lobby_channel else ''} {filename}, {len(tas)} bytes, {len(message.content)} char message")

    # validate length
    if len(tas) > 204800:  # 200 kb
        return ValidationResult(False, f"This TAS file is very large ({len(tas) / 2048} KB). For safety, it won't be processed.", f"{filename} being too long ({len(tas)} bytes)")

    # validate breakpoint doesn't exist and chaptertime does
    tas_lines = as_lines(tas)
    message_lowercase = message.content.lower()
    found_breakpoints, found_chaptertime, chapter_time, chapter_time_trimmed, _ = parse_tas_file(tas_lines, True, lobby_channel)

    if len(found_breakpoints) == 1:
        return ValidationResult(False, f"Breakpoint found on line {found_breakpoints[0]}, please remove it and post again.", f"breakpoint in {filename}")
    elif len(found_breakpoints) > 1:
        return ValidationResult(False, f"Breakpoints found on lines: {', '.join(found_breakpoints)}, please remove them and post again.", f"{len(found_breakpoints)} breakpoints in {filename}")
    elif not found_chaptertime:
        if lobby_channel:
            return ValidationResult(False, "No ChapterTime found in file, please add one and post again.", f"no ChapterTime in {filename}")
        else:
            return ValidationResult(False, "No final time found in file, please add one and post again.", f"no final time in {filename}")

    # validate chaptertime is in message content
    if lobby_channel:
        if chapter_time not in message.content:
            return ValidationResult(False, f"The file's final time ({chapter_time}) is missing in your message, please add it and post again.",
                                    f"final time ({chapter_time}) missing in message content")
    else:
        if chapter_time not in message.content and chapter_time_trimmed not in message.content:
            chapter_time_notif = chapter_time if chapter_time == chapter_time_trimmed else chapter_time_trimmed
            return ValidationResult(False, f"The file's ChapterTime ({chapter_time_notif}) is missing in your message, please add it and post again.",
                                    f"ChapterTime ({chapter_time_notif}) missing in message content")

    # validate level
    if projects[message.channel.id]['ensure_level']:
        level = filename.lower().removesuffix('.tas').replace('_', '')

        if level not in message_lowercase.replace('_', '').replace(' ', ''):
            return ValidationResult(False, "The level name is missing in your message, please add it and post again.", f"level name ({level}) missing in message content")

    if old_tas:
        # validate timesave frames is in message content
        old_chapter_time, old_chapter_time_trimmed = parse_tas_file(as_lines(old_tas), False, lobby_channel)[2:4]
        time_saved_num = calculate_time_difference(old_chapter_time, chapter_time)
        time_saved_minus = f'-{abs(time_saved_num)}f'
        time_saved_plus = f'+{abs(time_saved_num)}f'
        time_saved_messages = re_timesave_frames.match(message.content)
        aleph_moment = " (you suck at math lol)" if message.author.id == 238029047567876096 else ""
        # ok this logic is weird cause it can be '-f', '+f', or in the case of 0 frames saved, either one

        if not time_saved_messages:
            if time_saved_num == 0:
                time_saved_options = f"{time_saved_minus}\" or \"{time_saved_plus}"
            else:
                time_saved_options = time_saved_minus if time_saved_num >= 0 else time_saved_plus

            return ValidationResult(False, f"Please mention how many frames were saved or lost, with the text \"{time_saved_options}\" (if that's correct), and post again.",
                                    f"no timesave in message (should be {time_saved_options})")

        if time_saved_num == 0:
            if time_saved_messages[0] not in (time_saved_minus, time_saved_plus):
                time_saved_options = f"{time_saved_minus}\" or \"{time_saved_plus}"
                return ValidationResult(False, f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_options}\"), please fix and post again.",
                                        f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_options}\")")
        else:
            time_saved_actual = time_saved_minus if time_saved_num >= 0 else time_saved_plus

            if time_saved_messages[0] != time_saved_actual:
                return ValidationResult(False, f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_actual}\"), "
                                               f"please fix and post again{aleph_moment}.",
                                        f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_actual}\")")
    else:
        # validate draft text
        if "draft" not in message_lowercase:
            return ValidationResult(False, "Since this is a draft, please mention that in your message and post again.", "no \"draft\" text in message")

    return ValidationResult(True, chapter_time=chapter_time, timesave=time_saved_messages[0] if old_tas else None)


def parse_tas_file(tas_lines: list, find_breakpoints: bool, is_lobby_file: bool) -> Tuple[list, bool, Optional[str], Optional[str], Optional[int]]:
    found_breakpoints = []
    found_chaptertime = False
    chaptertime_line = None
    chapter_time = None
    chapter_time_trimmed = None

    if is_lobby_file:
        re_lobby_time = re.compile(r'#\d+\.\d+')

    for line in enumerate(tas_lines):
        if find_breakpoints and '***' in line[1] and not line[1].startswith('#'):
            log.info(f"Found breakpoint at line {line[0] + 1}")
            found_breakpoints.append(str(line[0] + 1))
        elif not found_chaptertime:
            if not is_lobby_file and 'ChapterTime:' in line[1] and not line[1].startswith('#') and line[1].strip() != 'ChapterTime:':
                found_chaptertime = True
                chaptertime_line = line[0]
            elif is_lobby_file and line[1].startswith('#') and re_lobby_time.match(line[1]):
                found_chaptertime = True
                chaptertime_line = line[0]

    if found_chaptertime:
        if is_lobby_file:
            chapter_time = chapter_time_trimmed = tas_lines[chaptertime_line].lstrip('#0:').partition('(')[0]
        else:
            chapter_time = tas_lines[chaptertime_line].partition(' ')[2].partition('(')[0]
            chapter_time_trimmed = chapter_time.removeprefix('0:').removeprefix('0')

    return found_breakpoints, found_chaptertime, chapter_time, chapter_time_trimmed, chaptertime_line


def calculate_time_difference(time_old: str, time_new: str) -> int:
    if time_old == time_new:
        return 0

    if ':' not in time_new:
        return round((float(time_old) - float(time_new)) / 0.017)

    colon_partition_old = time_old.partition(':')
    colon_partition_new = time_new.partition(':')
    dot_partition_old = colon_partition_old[2].partition('.')
    dot_partition_new = colon_partition_new[2].partition('.')
    minutes_old = colon_partition_old[0]
    minutes_new = colon_partition_new[0]
    seconds_old = dot_partition_old[0]
    seconds_new = dot_partition_new[0]
    ms_old = dot_partition_old[2]
    ms_new = dot_partition_new[2]
    time_old_seconds = (int(minutes_old) * 60) + int(seconds_old) + (int(ms_old) / 1000)
    time_new_seconds = (int(minutes_new) * 60) + int(seconds_new) + (int(ms_new) / 1000)
    return round((time_old_seconds - time_new_seconds) / 0.017)


def as_lines(tas: bytes) -> List[str]:
    lines = tas.decode('UTF8').splitlines()
    log.info(f"Converted {len(tas)} bytes to {len(lines)} TAS lines")
    return lines


re_timesave_frames = re.compile(r'[-+]\d+f')
log: Optional[logging.Logger] = None
