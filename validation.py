import logging
import re
from typing import List, Optional, Tuple

import discord

import main


class ValidationResult:
    def __init__(self, valid_tas: bool, warning_text: str = None, log_text: str = None, finaltime: str = None, timesave: str = None):
        self.valid_tas = valid_tas
        self.warning_text = warning_text
        self.log_text = log_text
        self.timesave = timesave
        self.finaltime = finaltime

        if valid_tas:
            log.info("TAS file and improvement post have been validated")


def validate(tas: bytes, filename: str, message: discord.Message, old_tas: Optional[bytes], lobby_channel: bool, skip_validation: bool = False) -> ValidationResult:
    log.info(f"Validating{' lobby file' if lobby_channel else ''} {filename}, {len(tas)} bytes, {len(message.content)} char message")

    # validate length
    if not skip_validation and len(tas) > 204800:  # 200 kb
        return ValidationResult(False, f"This TAS file is very large ({len(tas) / 2048} KB). For safety, it won't be processed.", f"{filename} being too long ({len(tas)} bytes)")

    # validate breakpoint doesn't exist and chaptertime does
    tas_lines = as_lines(tas)
    message_lowercase = message.content.lower()
    breakpoints, found_finaltime, finaltime, finaltime_trimmed, finaltime_line = parse_tas_file(tas_lines, True)
    dash_saves = re_dash_saves.search(message.content)
    is_dash_save = dash_saves is not None
    got_timesave = False

    if skip_validation:
        log.info("Skipping validation actually")
        # ok this is really ugly, but we do need final time and timesave

        if old_tas and not is_dash_save:
            old_has_finaltime, old_finaltime, old_finaltime_trimmed = parse_tas_file(as_lines(old_tas), False)[1:4]

            if old_has_finaltime:
                time_saved_num = calculate_time_difference(old_finaltime, finaltime)
                time_saved_text = f'-{time_saved_num}f' if time_saved_num >= 0 else f'+{abs(time_saved_num)}f'
                got_timesave = True

        if got_timesave:
            timesave = time_saved_text
        elif is_dash_save:
            # techically not timesave but whatever
            timesave = str(dash_saves[0])
        else:
            timesave = None

        return ValidationResult(True, finaltime=finaltime, timesave=timesave)

    if old_tas and tas.replace(b'\r', b'') == old_tas.replace(b'\r', b''):
        return ValidationResult(False, "This file is identical to what's already in the repo.", f"file {filename} is unchanged from repo")

    if len(breakpoints) == 1:
        return ValidationResult(False, f"Breakpoint found on line {breakpoints[0]}, please remove it (Ctrl+P in Studio) and post again.", f"breakpoint in {filename}")
    elif len(breakpoints) > 1:
        return ValidationResult(False, f"Breakpoints found on lines: {', '.join(breakpoints)}, please remove them (Ctrl+P in Studio) and post again.",
                                f"{len(breakpoints)} breakpoints in {filename}")
    elif not found_finaltime:
        if lobby_channel:
            return ValidationResult(False, "No final time found in file, please add one and post again.", f"no final time in {filename}")
        else:
            return ValidationResult(False, "No ChapterTime found in file, please add one and post again.", f"no ChapterTime in {filename}")

    # validate chaptertime is in message content
    if not is_dash_save:
        if lobby_channel:
            if finaltime not in message.content:
                return ValidationResult(False, f"The file's final time ({finaltime}) is missing in your message, please add it and post again.",
                                        f"final time ({finaltime}) missing in message content")
        else:
            if finaltime not in message.content and finaltime_trimmed not in message.content:
                chapter_time_notif = finaltime if finaltime == finaltime_trimmed else finaltime_trimmed
                return ValidationResult(False, f"The file's ChapterTime ({chapter_time_notif}) is missing in your message, please add it and post again.",
                                        f"ChapterTime ({chapter_time_notif}) missing in message content")

    # validate level
    if main.projects[message.channel.id]['ensure_level']:
        level = re_remove_punctuation.subn('', filename.lower().removesuffix('.tas'))[0].replace('_', '')

        if level not in re_remove_punctuation.subn('', message_lowercase)[0].replace('_', ''):
            return ValidationResult(False, "The level name is missing in your message, please add it and post again.", f"level name ({level}) missing in message content")

    if old_tas and not is_dash_save:
        # validate timesave frames is in message content
        old_has_finaltime, old_finaltime, old_finaltime_trimmed = parse_tas_file(as_lines(old_tas), False)[1:4]

        if old_has_finaltime:
            time_saved_num = calculate_time_difference(old_finaltime, finaltime)
            time_saved_minus = f'-{abs(time_saved_num)}f'
            time_saved_plus = f'+{abs(time_saved_num)}f'
            time_saved_messages = re_timesave_frames.search(message.content)
            got_timesave = True
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
                    return ValidationResult(False, f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_options}\"), "
                                            f"please fix and post again.",
                                            f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_options}\")")
            else:
                time_saved_actual = time_saved_minus if time_saved_num >= 0 else time_saved_plus

                if time_saved_messages[0] != time_saved_actual:
                    return ValidationResult(False, f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_actual}\"), "
                                                   f"please fix and post again{aleph_moment}.",
                                            f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_actual}\")")
        else:
            log.info("Old file has no final time, skipping validating timesave")
    elif not old_tas:
        # validate draft text
        if "draft" not in message_lowercase:
            return ValidationResult(False, "Since this is a draft, please mention that in your message and post again.", "no \"draft\" text in message")

    if got_timesave:
        timesave = str(time_saved_messages[0])
    elif is_dash_save:
        # techically not timesave but whatever
        timesave = str(dash_saves[0])
    else:
        timesave = None

    return ValidationResult(True, finaltime=finaltime, timesave=timesave)


# get breakpoints and final time in one pass
def parse_tas_file(tas_lines: list, find_breakpoints: bool, allow_comment_time: bool = True) -> Tuple[list, bool, Optional[str], Optional[str], Optional[int]]:
    breakpoints = []
    finaltime_line = None
    finaltime = None
    finaltime_trimmed = None
    found_chaptertime = False

    for line in enumerate(tas_lines):
        if find_breakpoints and '***' in line[1] and not line[1].startswith('#'):
            log.info(f"Found breakpoint at line {line[0] + 1}")
            breakpoints.append(str(line[0] + 1))
        else:
            if re_chapter_time.match(line[1]):
                found_chaptertime = True
                finaltime_line = line[0]
            elif allow_comment_time and not found_chaptertime and re_comment_time.match(line[1]):
                found_chaptertime = False
                finaltime_line = line[0]

    found_finaltime = finaltime_line is not None

    if found_finaltime:
        if found_chaptertime:
            finaltime = tas_lines[finaltime_line].partition(' ')[2].partition('(')[0]
            finaltime_trimmed = finaltime.removeprefix('0:').removeprefix('0').strip()
        else:
            finaltime = finaltime_trimmed = tas_lines[finaltime_line].lstrip('#0:').partition('(')[0].strip()

        finaltime = re_remove_non_digits.sub('', finaltime)
        finaltime_trimmed = re_remove_non_digits.sub('', finaltime_trimmed)

    return breakpoints, found_finaltime, finaltime, finaltime_trimmed, finaltime_line


def calculate_time_difference(time_old: str, time_new: str) -> int:
    if time_old == time_new:
        return 0

    old_has_colon = ':' in time_old
    new_has_colon = ':' in time_new

    if not old_has_colon and not new_has_colon:
        return round((float(time_old) - float(time_new)) / 0.017)

    if old_has_colon:
        colon_partition_old = time_old.partition(':')
    else:
        colon_partition_old = (0, None, time_old)

    if new_has_colon:
        colon_partition_new = time_new.partition(':')
    else:
        colon_partition_new = (0, None, time_new)

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


re_chapter_time = re.compile(r'#{0}ChapterTime: \d+:\d+\.\d+(\d+)')
re_comment_time = re.compile(r'#[\s+]*[\d:]*\d+\.\d+')
re_timesave_frames = re.compile(r'[-+]\d+f')
re_dash_saves = re.compile(r'[-+]\d+x')
re_remove_punctuation = re.compile(r'\W')
re_remove_non_digits = re.compile(r'[^\d.:]')
log: Optional[logging.Logger] = None
