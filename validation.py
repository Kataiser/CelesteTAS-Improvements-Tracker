import dataclasses
import enum
import logging
import re
from typing import List, Optional, Tuple, Callable, Union

import discord

import db


@dataclasses.dataclass
class ValidationResult:
    valid_tas: bool
    warning_text: list[str]
    log_text: list[str]
    finaltime: Optional[str] = None
    finaltime_frames: Optional[int] = None
    timesave: Optional[str] = None
    wip: bool = False
    sj_data: Optional[tuple] = None

    def emit_failed_check(self, warning: str, log_message: str):
        self.valid_tas = False
        self.warning_text.append(warning)
        self.log_text.append(log_message)

    def __post_init__(self):
        if self.valid_tas:
            log.info("TAS file and improvement post have been validated")

            if not self.finaltime:
                log.warning("Valid tas result has no finaltime")


def validate(tas: bytes, filename: str, message: discord.Message, old_tas: Optional[bytes], project: dict, skip_validation: bool = False) -> ValidationResult:
    log.info(f"Validating{' lobby file' if project['is_lobby'] else ''} {filename}, {len(tas)} bytes, {len(message.content)} char message")

    if not skip_validation:
        # validate length
        if not tas or tas == b'404: Not Found':
            return ValidationResult(False, [f"This TAS file is empty or couldn't be downloaded."], [f"{filename} being empty"])

        if not skip_validation and len(tas) > 204800:  # 200 kb
            return ValidationResult(False, [f"This TAS file is very large ({len(tas) / 1024:.1f} KB). For safety, it won't be processed."], [f"{filename} being too long ({len(tas)} bytes)"])

    tas_lines = as_lines(tas)
    message_lowercase = message.content.lower()
    tas_parsed = parse_tas_file(tas_lines, True)
    dash_saves = re_dash_saves.search(message.content)
    is_dash_save = dash_saves is not None
    got_timesave = False
    wip_in_message = 'wip' in re_remove_punctuation.subn(' ', message_lowercase)[0].split()
    last_analogmode = 'ignore'
    rooms_found = {}
    uses_one_indexing = None
    found_start = False

    if skip_validation or wip_in_message:
        log.info(f"Skipping validation ({wip_in_message=})")
        # ok this is really ugly, but we do need final time and timesave

        if old_tas and tas_parsed.found_finaltime and not is_dash_save:
            old_tas_parsed = parse_tas_file(as_lines(old_tas), False)

            if old_tas_parsed.found_finaltime:
                time_saved_num = calculate_time_difference(old_tas_parsed.finaltime, tas_parsed.finaltime)
                time_saved_text = f'-{time_saved_num}f' if time_saved_num >= 0 else f'+{abs(time_saved_num)}f'
                got_timesave = True

        if got_timesave:
            timesave = time_saved_text
        elif is_dash_save:
            # techically not timesave but whatever
            timesave = str(dash_saves[0])
        else:
            timesave = None

        return ValidationResult(True, [], [], finaltime=tas_parsed.finaltime,
                                finaltime_frames=tas_parsed.finaltime_frames, timesave=timesave, wip=wip_in_message)

    validation_result = ValidationResult(True, [], [])

    # validate file not in excluded items
    if filename in project['excluded_items']:
        return ValidationResult(False, ["This filename is excluded from the project."], [f"file {filename} is excluded from project (in {project['excluded_items']})"])

    # validate file has been updated
    if old_tas and tas.replace(b'\r', b'') == old_tas.replace(b'\r', b''):
        return ValidationResult(False, ["This file is identical to what's already in the repo."], [f"file {filename} is unchanged from repo"])

    # validate breakpoint doesn't exist and chaptertime does
    if len(tas_parsed.breakpoints) == 1:
        validation_result.emit_failed_check(f"Breakpoint found on line {tas_parsed.breakpoints[0]}, please remove it (Ctrl+P in Studio) and post again.", f"breakpoint in {filename}")
    elif len(tas_parsed.breakpoints) > 1:
        validation_result.emit_failed_check(f"Breakpoints found on lines: {', '.join(tas_parsed.breakpoints)}, please remove them (Ctrl+P in Studio) and post again.",
                                f"{len(tas_parsed.breakpoints)} breakpoints in {filename}")
    elif not tas_parsed.found_finaltime:
        if project['is_lobby']:
            validation_result.emit_failed_check("No final time found in file, please add one and post again.", f"no final time in {filename}")
        else:
            validation_result.emit_failed_check("No ChapterTime found in file, please add one and post again.", f"no ChapterTime in {filename}")

    for line in enumerate(tas_lines):
        # validate room label indexing
        if line[1].startswith('#lvl_'):
            line_partitioned = line[1].partition('(')
            room_name = line_partitioned[0].strip()
            room_index_str = line_partitioned[2].strip(')')
            room_index = int(room_index_str) if room_index_str.isdigit() else None

            if room_name in rooms_found:
                if rooms_found[room_name] is None:
                    validation_result.emit_failed_check(f"Duplicate room label `{line[1]}` found on line {line[0] + 1}, please index revisited rooms starting from zero and post again.",
                                            f"Duplicate room label {line[1]} on line {line[0] + 1} in {filename}")
                elif room_index is None:
                    validation_result.emit_failed_check(f"Missing room label index `{line[1]}` found on line {line[0] + 1}, please index revisited rooms starting from zero and post again.",
                                            f"Missing room label {line[1]} on line {line[0] + 1} in {filename}")
                elif room_index <= rooms_found[room_name]:
                    validation_result.emit_failed_check(f"Out of order room label index `{line[1]}` found on line {line[0] + 1}, please index revisited rooms starting from zero and post again.",
                                            f"Out of order room label {line[1]} on line {line[0] + 1} in {filename}")
            else:
                if uses_one_indexing is None:
                    match room_index:
                        case 0:
                            uses_one_indexing = False
                        case 1:
                            uses_one_indexing = True

                if room_index is not None and ((not uses_one_indexing and room_index != 0) or (uses_one_indexing and room_index != 1)):
                    init_str = "one" if uses_one_indexing else "zero"
                    validation_result.emit_failed_check(f"Incorrect initial room label index `{line[1]}` found on line {line[0] + 1}, please index revisited rooms "
                                            f"starting from {init_str} and post again.", f"Incorrect initial room label {line[1]} on line {line[0] + 1} in {filename}")

            rooms_found[room_name] = room_index

        # validate command usage
        line_num = line[0]
        line_stripped = line[1].strip()
        line_split = line_stripped.split() if re_check_space_command.match(line_stripped) else line_stripped.split(',')
        command = line_split[0].lower()

        if not found_start and command == '#start':
            found_start = True
            continue

        if command in command_rules:
            if command in disallowed_commands and not found_start:
                continue

            if message.channel.id in project_exceptions:
                project_exception = project_exceptions[message.channel.id]
                except_command = False

                for exception in project_exception:
                    if command == exception[0] and exception[1] in line_stripped.lower():
                        except_command = True
                        break

                if except_command:
                    continue

            rules_functions = command_rules[command]

            if command in disallowed_commands and isinstance(rules_functions, str):
                validation_result.emit_failed_check(f"Incorrect `{line_split[0]}` command usage on line {line_num + 1}: {rules_functions}.",
                                                    f"incorrect command argument in {filename}: {line_split[0]}, {rules_functions}")
                continue

            args = [i.strip() for i in line_split[1:] if i]
            required_args_count = len([f for f in rules_functions if not isinstance(f, OptionalArg)])
            args_count_options = required_args_count if required_args_count == len(rules_functions) else f"{required_args_count}-{len(rules_functions)}"

            if len(args) < required_args_count or len(args) > len(rules_functions):
                validation_result.emit_failed_check(f"Incorrect number of arguments to `{line_split[0]}` command on line {line_num + 1}: is {len(args)}, should be {args_count_options}.",
                                                    f"incorrect command arguments count in {filename}: {line_split[0]}, {len(args)} vs {args_count_options}")

            for arg in enumerate(args[:len(rules_functions)]):
                rules_function = rules_functions[arg[0]]
                last_analogmode = arg[1].lower() if command in ('analogmode', 'analoguemode') else last_analogmode

                if isinstance(rules_function, OptionalArg):
                    rules_function = rules_function.validate_func

                if isinstance(rules_function, Callable):
                    arg_validity = rules_function(arg[1])

                    if arg_validity is not True:
                        validation_result.emit_failed_check(f"Incorrect `{line_split[0]}` command usage on line {line_num + 1}: {arg_validity}.",
                                                            f"incorrect command argument in {filename}: {line_split[0]}, {arg_validity}")

    # validate last analogmode is ignore
    if last_analogmode != 'ignore':
        validation_result.emit_failed_check(f"Incorrect last AnalogMode, is {last_analogmode.capitalize()} but should be Ignore so as to not possibly desync later TASes.",
                                f"last analogmode in {filename} is {last_analogmode}")

    time_saved_messages: Union[None, re.Match] = None

    # validate chaptertime is in message content
    if tas_parsed.finaltime and not is_dash_save:
        if project['is_lobby']:
            if tas_parsed.finaltime not in message.content:
                validation_result.emit_failed_check(f"The file's final time ({tas_parsed.finaltime}) is missing in your message, please add it and post again.",
                                        f"final time ({tas_parsed.finaltime}) missing in message content")
        else:
            if tas_parsed.finaltime not in message.content and tas_parsed.finaltime_trimmed not in message.content:
                chapter_time_notif = tas_parsed.finaltime if tas_parsed.finaltime == tas_parsed.finaltime_trimmed else tas_parsed.finaltime_trimmed
                validation_result.emit_failed_check(f"The file's ChapterTime ({chapter_time_notif}) is missing in your message, please add it and post again.",
                                        f"ChapterTime ({chapter_time_notif}) missing in message content")

    # validate #Start exists
    if not found_start:
        validation_result.emit_failed_check(f"No `#Start` found in file, please add one between the console load frame and the intro frames (or first room label if none) and post again.",
                                f"no #Start in file")

    if old_tas and not is_dash_save:
        # validate timesave frames is in message content
        old_tas_parsed = parse_tas_file(as_lines(old_tas), False)

        if old_tas_parsed.found_finaltime:
            time_saved_num = calculate_time_difference(old_tas_parsed.finaltime, tas_parsed.finaltime)
            time_saved_minus = f'-{abs(time_saved_num)}f'
            time_saved_plus = f'+{abs(time_saved_num)}f'
            time_saved_messages = re_timesave_frames.search(message.content)
            got_timesave = True
            linn_moment = " (you suck at math lol)" if message.author.id == 238029047567876096 else ""
            # ok this logic is weird cause it can be '-f', '+f', or in the case of 0 frames saved, either one

            if not time_saved_messages:
                if time_saved_num == 0:
                    time_saved_options = f"{time_saved_minus}\" or \"{time_saved_plus}"
                else:
                    time_saved_options = time_saved_minus if time_saved_num >= 0 else time_saved_plus

                validation_result.emit_failed_check(f"Please mention how many frames were saved or lost, with the text \"{time_saved_options}\" (if that's correct), and post again.",
                                        f"no timesave in message (should be {time_saved_options})")
            else:
                if time_saved_num == 0:
                    if time_saved_messages[0] not in (time_saved_minus, time_saved_plus):
                        time_saved_options = f"{time_saved_minus}\" or \"{time_saved_plus}"
                        validation_result.emit_failed_check(f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_options}\"), "
                                                f"please fix and post again{linn_moment}. Make sure you improved the latest version of the file.",
                                                f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_options}\")")
                else:
                    time_saved_actual = time_saved_minus if time_saved_num >= 0 else time_saved_plus

                    if time_saved_messages[0] != time_saved_actual:
                        validation_result.emit_failed_check(f"Frames saved is incorrect (you said \"{time_saved_messages[0]}\", but it seems to be \"{time_saved_actual}\"), "
                                                f"please fix and post again{linn_moment}. Make sure you improved the latest version of the file.",
                                                f"incorrect time saved in message (is \"{time_saved_messages[0]}\", should be \"{time_saved_actual}\")")
        else:
            log.info("Old file has no final time, skipping validating timesave")
    elif not old_tas:
        # validate draft text
        if "draft" not in message_lowercase:
            path_cache = db.path_caches.get(message.channel.id)

            if path_cache:
                from fuzzywuzzy import process as fuzzy_process
                fuzzes = fuzzy_process.extract(filename, path_cache.keys())
                possible_filename = fuzzes[0][0] if fuzzes[0][1] >= 90 else None
                did_you_mean_text = f" (did you mean `{possible_filename}`?)" if possible_filename else ""
                shouldnt_be_draft_text = f" If it shouldn't be a draft, make sure your filename is exactly the same as in the repo{did_you_mean_text}."
            else:
                shouldnt_be_draft_text = ""

            validation_result.emit_failed_check(f"Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post again."
                                           f"{shouldnt_be_draft_text}", "no \"draft\" text in message")

    # validate level
    if project['ensure_level']:
        filename_level = re_remove_punctuation.subn('', filename.lower().removesuffix('.tas'))[0].replace('_', '').removeprefix('the')
        message_level = re_remove_punctuation.subn('', message_lowercase)[0].replace('_', '')

        if filename_level not in message_level:
            validation_result.emit_failed_check("The level name is missing in your message, please add it and post again.", f"level name ({filename_level}) missing in message content")

    if got_timesave:
        timesave = str(time_saved_messages[0]) if time_saved_messages else None
    elif is_dash_save:
        # techically not timesave but whatever
        timesave = str(dash_saves[0])
    else:
        timesave = None

    sj_data = (tas_lines, tas_parsed.finaltime_line_num) if message.channel.id == 1074148268407275520 else None

    validation_result.finaltime = tas_parsed.finaltime
    validation_result.finaltime_frames = tas_parsed.finaltime_frames
    validation_result.timesave = timesave
    validation_result.sj_data = sj_data

    return validation_result


class FinalTimeTypes(enum.Enum):
    Chapter = 0
    MidwayChapter = 1
    File = 2
    MidwayFile = 3
    Comment = 4

    def as_midway(self):
        match self:
            case self.Chapter:
                return self.MidwayChapter
            case self.File:
                return self.MidwayFile
            case _:
                return self


@dataclasses.dataclass
class ParsedTASFile:
    breakpoints: List[str]
    found_finaltime: bool
    finaltime: Optional[str]
    finaltime_trimmed: Optional[str]
    finaltime_line_num: Optional[int]
    finaltime_frames: Optional[int]
    finaltime_type: Optional[FinalTimeTypes]


# get breakpoints and final time in one pass
# this is easily the worst code in this bot
def parse_tas_file(tas_lines: list, find_breakpoints: bool, allow_comment_time: bool = True, required_finaltime_type: Optional[FinalTimeTypes] = None) -> ParsedTASFile:
    breakpoints = []
    finaltime_line_num = None
    finaltime = None
    finaltime_trimmed = None
    finaltime_frames = None
    finaltime_type = None

    def is_allowed_finaltime_type(line_, type_) -> bool:
        if required_finaltime_type:
            if line_.lower().startswith('midway'):
                return type_.as_midway() == required_finaltime_type
            else:
                return type_ == required_finaltime_type
        else:
            return True

    for line_enum in enumerate(tas_lines):
        line_num, line = line_enum[0], line_enum[1].strip()

        if find_breakpoints and '***' in line and not line.startswith('#'):
            log.info(f"Found breakpoint at line {line_num + 1}")
            breakpoints.append(str(line_num + 1))
        else:
            if re_chapter_time.match(line) and is_allowed_finaltime_type(line, FinalTimeTypes.Chapter):
                finaltime_type = FinalTimeTypes.Chapter
                finaltime_line_num = line_num
                finaltime_line = line
            elif re_file_time.match(line) and is_allowed_finaltime_type(line, FinalTimeTypes.File):
                finaltime_type = FinalTimeTypes.File
                finaltime_line_num = line_num
                finaltime_line = line
            elif allow_comment_time and re_comment_time.match(line) and is_allowed_finaltime_type(line, FinalTimeTypes.Comment):
                finaltime_type = FinalTimeTypes.Comment
                finaltime_line_num = line_num
                finaltime_line = line

    found_finaltime = finaltime_line_num is not None

    if found_finaltime:
        if finaltime_type in (FinalTimeTypes.Chapter, FinalTimeTypes.File):
            if finaltime_line.lower().startswith('midway'):
                finaltime_type = finaltime_type.as_midway()

            finaltime_components = finaltime_line.partition(' ')[2].partition('(')
            finaltime = finaltime_components[0]
            finaltime_trimmed = finaltime.removeprefix('0:').removeprefix('0').strip()
        else:
            if finaltime_type == FinalTimeTypes.Comment:
                finaltime_components = finaltime_line.strip('#\n ').partition(' ')[0].partition('(')
                finaltime = finaltime_components[0].rstrip()
                finaltime_trimmed = finaltime.removeprefix('0:').removeprefix('0')
            else:
                # this seems unreachable. why does this exist
                finaltime_components = finaltime_line.lstrip('#0:').partition('(')
                finaltime = finaltime_trimmed = finaltime_components[0].strip()

        finaltime = re_remove_non_digits.sub('', finaltime)
        finaltime_trimmed = re_remove_non_digits.sub('', finaltime_trimmed)
        finaltime_frames = time_to_frames(finaltime_trimmed)

    return ParsedTASFile(breakpoints, found_finaltime, finaltime, finaltime_trimmed, finaltime_line_num, finaltime_frames, finaltime_type)


def calculate_time_difference(time_old: Union[str, int], time_new: Union[str, int]) -> int:
    time_frames_old = time_old if isinstance(time_old, int) else time_to_frames(time_old)
    time_frames_new = time_new if isinstance(time_new, int) else time_to_frames(time_new)

    if time_old == time_new:
        return 0

    return time_frames_old - time_frames_new


def time_to_frames(time: str) -> int:
    if ':' not in time:
        return round(float(time) / 0.017)

    colon_partition = time.rpartition(':')
    dot_partition = colon_partition[2].partition('.')
    minutes = colon_partition[0]
    hours = minutes.partition(':')[0] if ':' in minutes else '0'
    seconds = dot_partition[0]
    ms = dot_partition[2]
    time_seconds = (int(hours) * 3600) + (int(minutes[-2:]) * 60) + int(seconds) + (int(ms) / 1000)
    return round(time_seconds / 0.017)


def as_lines(tas: bytes) -> List[str]:
    lines = tas.decode('UTF8').splitlines()
    log.info(f"Converted {len(tas)} bytes to {len(lines)} TAS lines")
    return lines


class OptionalArg:
    def __init__(self, validate_func: Optional[Callable] = None):
        self.validate_func = validate_func


re_chapter_time = re.compile(r'#{0}(Midway)*ChapterTime: [\d+:]*\d+:\d+\.\d+(\d+)')
re_file_time = re.compile(r'#{0}(Midway)*FileTime: [\d+:]*\d+:\d+\.\d+(\d+)')
re_comment_time = re.compile(r'#[\s+]*[\d:]*\d+\.\d+')
re_timesave_frames = re.compile(r'[-+]\d+f')
re_dash_saves = re.compile(r'[-+]\d+x')
re_remove_punctuation = re.compile(r'\W')
re_remove_non_digits = re.compile(r'[^\d.:]')
re_check_space_command = re.compile(r'^[^,]+?\s+[^,]')
log: Optional[logging.Logger] = None

analog_modes = (('ignore', 'circle', 'square', 'precise'), "Ignore, Circle, Square, or Precise")
assert_conditions = (('equal', 'notequal', 'contain', 'notcontain', 'startwith', 'notstartwith', 'endwith', 'notendwith'),
                     "Equal, NotEqual, Contain, NotContain, StartWith, NotStartWith, EndWith, or NotEndWith")
stunpause_modes = {'input': True, 'simulate': "Simulate mode is not allowed outside of testing routes"}
mouse_buttons = (('l', 'r', 'm', 'x1', 'x2'), "L, R, M, X1, or X2")
set_exceptions = ('celestetas.simplifiedgraphics', 'celestetas.simplifiedbackdrop')
disallowed_commands = ('console', 'invoke', 'set', 'exportlibtas', 'endexportlibtas', 'exitgame')
project_exceptions = {879081769138286662: (('set', 'session.time'), ('set', 'engine.scene.timeactive')),
                      1155076734450933791: (('invoke', 'luacutscenesutils.triggerbooleanvariant'), ('set', 'extendedvariantmode.everyjumpisultra'))}

command_rules = {'analogmode': (lambda mode: True if mode.lower() in analog_modes[0] else f"mode must be {analog_modes[1]}, you used \"{mode.capitalize()}\"",),
                 'read': (True, OptionalArg(), OptionalArg()),
                 'play': (True, OptionalArg(lambda wait_frames: True if wait_frames.isdigit() else f"wait frames must be a number, you used \"{wait_frames}\"")),
                 'repeat': (lambda count: True if count.isdigit() else f"count must be a number, you used \"{count}\"",),
                 'endrepeat': (),
                 'console': (lambda command: True if command.lower() == 'load' else "Console command is not allowed",
                             OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg()),
                 'set': (lambda field: True if field.lower() in set_exceptions else "Set command is not allowed", OptionalArg(), OptionalArg(), OptionalArg()),
                 'invoke': "Invoke command is not allowed",
                 'unsafe': (),
                 'safe': (),
                 'enforcelegal': (),
                 'assert': (lambda condition: True if condition.lower() in assert_conditions[0] else f"condition must be {assert_conditions[1]}, you used \"{condition}\"",
                            OptionalArg(), OptionalArg()),
                 'stunpause': (OptionalArg(lambda mode: stunpause_modes[mode.lower()] if mode.lower() in stunpause_modes else True),),
                 'endstunpause': (),
                 'autoinput': (lambda cycle: True if cycle.isdigit() else f"cycle length must be a number, you used \"{cycle}\"",),
                 'startautoinput': (),
                 'endautoinput': (),
                 'skipinput': (OptionalArg(lambda skip: True if skip.isdigit() else f"skip frames must be a number, you used \"{skip}\""),
                               OptionalArg(lambda wait: True if wait.isdigit() else f"waiting frames must be a number, you used \"{wait}\"")),
                 'press': (True, OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg()),
                 'mouse': (lambda x: True if x.isdigit() else f"X coordinate must be a number, you used \"{x}\"",
                           lambda y: True if y.isdigit() else f"Y coordinate must be a number, you used \"{y}\"",
                           OptionalArg(lambda button: True if button.lower() in mouse_buttons[0] else f"button must be {mouse_buttons[1]}, you used \"{button}\""),
                           OptionalArg(lambda button: True if button.lower() in mouse_buttons[0] else f"button must be {mouse_buttons[1]}, you used \"{button}\"")),
                 'exportgameinfo': (OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg(), OptionalArg()),
                 'endexportgameinfo': (),
                 'exportroominfo': (OptionalArg(),),
                 'endexportroominfo': (),
                 'completeinfo': (lambda side: True if side.lower() in ('a', 'b', 'c') else f"side must be A, B, or C, you used \"{side}\"", True),
                 'recordcount:': (OptionalArg(lambda count: True if count.isdigit() else f"records must be a number, you have \"{count}\""),),
                 'exportlibtas': "ExportLibTAS command is not allowed",
                 'endexportlibtas': "EndExportLibTAS command is not allowed",
                 'add': (True,),
                 'skip': (lambda frames: True if frames.isdigit() else f"frame count must be a number, you used \"{frames}\"",),
                 'exitgame': "ExitGame command is not allowed",
                 'startrecording': "StartRecording command is not allowed",
                 'stoprecording': "StopRecording command is not allowed",
                 'saveandquitreenter': ()}
command_rules['analoguemode'] = command_rules['analogmode']
command_rules['stunpausemode'] = command_rules['stunpause']
