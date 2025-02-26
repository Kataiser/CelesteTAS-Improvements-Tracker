import ast
import dataclasses
import datetime
import functools
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import discord
import pytest

import bot
import commands
import db
import game_sync
import gen_token
import main
import spreadsheet
import utils
import validation


@pytest.fixture
def setup_log():
    if not main.log:
        main.create_logger('tests', False)


@pytest.fixture
def setup_client():
    bot.share_client(MockClient())


@pytest.fixture
def fast_db():
    db.always_inconsistent_read = True


@dataclasses.dataclass
class MockUser:
    id: int = 219955313334288385
    global_name: str = "Kataiser"
    name: str = "kataiser"
    sent_messages = []

    async def send(self, content: str):
        self.sent_messages.append(content)


@dataclasses.dataclass
class MockChannel:
    id: Optional[int] = None
    guild: Optional = None
    sent_messages = []

    def __post_init__(self):
        guilds = {970380662907482142: MockGuild("Improvements bot testing")}

        if self.id and self.id in guilds:
            self.guild = guilds[self.id]

    async def send(self, content: str):
        self.sent_messages.append(content)

    def get_partial_message(self, id: int):
        mock_messages = {973344238261657650: MockMessage('', self, MockUser(), id=973344238261657650)}
        return mock_messages[id]


@dataclasses.dataclass
class MockMessage:
    content: str
    channel: MockChannel
    author: MockUser
    created_at: datetime.datetime = datetime.datetime.now()
    id: Optional[int] = None
    jump_url: Optional[str] = None
    attachments: Optional[list] = None
    replies: Optional[list] = None
    reactions = set()
    guild = None

    def __post_init__(self):
        self.replies = []
        guilds = {970380662907482142: MockGuild("Improvements bot testing")}
        jump_urls = {973344238261657650: 'https://discord.com/channels/970379400887558204/970380662907482142/973344238261657650'}

        if self.channel.id in guilds:
            self.guild = guilds[self.channel.id]

        if self.id and self.id in jump_urls:
            self.jump_url = jump_urls[self.id]

    async def add_reaction(self, emoji: str):
        self.reactions.add(emoji)

    async def clear_reaction(self, emoji: str):
        if emoji in self.reactions:
            self.reactions.remove(emoji)

    async def reply(self, content: str):
        self.replies.append(content)

    async def edit(self, content: str):
        self.content = content


class MockClient:
    def get_user(self, user_id: int) -> MockUser:
        mock_users = {219955313334288385: MockUser(),
                      234520815658336258: MockUser(234520815658336258, 'Vamp', 'vampire_flower')}

        return mock_users[user_id]

    async def fetch_user(self, user_id: int) -> MockUser:
        return self.get_user(user_id)

    def get_channel(self, id_: int) -> MockChannel:
        return MockChannel(id_)


@dataclasses.dataclass
class MockGuild:
    name: str
    id: Optional[int] = None


def mock_message(*args, **kwargs):
    return MockMessage(*args, **kwargs)


def mock_user(*args, **kwargs):
    return MockUser(*args, **kwargs)


def mock_client():
    return MockClient()


def mock_channel():
    channel = MockChannel()
    channel.sent_messages = []
    return channel


def mock_passthrough(*args, **kwargs):
    pass


async def mock_passthrough_async(*args, **kwargs):
    pass


# MAIN

@pytest.mark.asyncio
async def test_process_improvement_message(setup_log, monkeypatch):
    @dataclasses.dataclass
    class MockAttachment:
        filename: str
        url: str

    def mock_commit(*args) -> tuple:
        return "-0f 0oi71n.tas (1:08.748) from Kataiser", 'https://github.com/Kataiser/improvements-bot-testing/commit/8cffb3495b8b8423a8762834cc1b1a329bf86a47'

    monkeypatch.setattr(main, 'commit', mock_commit)
    monkeypatch.setattr(main, 'add_project_log', mock_passthrough)
    monkeypatch.setattr(main, 'set_status', mock_passthrough_async)
    monkeypatch.setattr(db.history_log, 'set', mock_passthrough)
    monkeypatch.setattr(discord, 'Message', mock_message)
    tas_file = MockAttachment('0oi71n.tas', 'https://raw.githubusercontent.com/Kataiser/improvements-bot-testing/main/0oi71n 2.tas')

    message = discord.Message('-0f 0oi71n 1:08.748', MockChannel(970380662907482142), MockUser(), attachments=[tas_file], id=1178068061358653480)
    assert await main.process_improvement_message(message)
    assert message.replies == []
    assert message.reactions == {'📝'}

    message = discord.Message('-1f 0oi71n 1:08.748', MockChannel(970380662907482142), MockUser(), attachments=[tas_file], id=1178068061358653480)
    message.reactions = set()
    assert await main.process_improvement_message(message)
    assert message.replies == ["Frames saved is incorrect (you said \"-1f\", but it seems to be \"-0f\" or \"+0f\"), please fix and post again. "
                               "Make sure you updated the time and improved the latest version of the file."]
    assert message.reactions == {'⏭', '❌'}

    message = discord.Message('-1f 0oi71n 1:08.748', MockChannel(970380662907482142), MockUser(), attachments=[tas_file], id=1178068061358653480)
    message.reactions = set()
    assert await main.process_improvement_message(message, skip_validation=True)
    assert message.replies == []
    assert message.reactions == {'📝'}


def test_generate_path_cache(setup_log):
    path_cache = main.generate_path_cache(970380662907482142)
    assert path_cache['0oi71n.tas'] == '0oi71n.tas'
    assert path_cache['6AC.tas'] == '6AC.tas'
    assert path_cache['abby-cookie.tas'] == 'lobby/abby-cookie.tas'
    assert path_cache['1k_Kataiser.tas'] == 'subproject/1k_Kataiser.tas'
    assert path_cache['The_Mines_Kataiser.tas'] == 'a_folder_yes/Lobby/The_Mines_Kataiser.tas'
    assert path_cache['glitchy.tas'] == 'sync_testing/glitchy.tas'
    assert path_cache['royal_gardens_renamed.tas'] == 'subproject/royal_gardens_renamed.tas'
    assert 'abby-cookie2.tas' not in path_cache


def test_get_sha(setup_log):
    assert main.get_sha('Kataiser/improvements-bot-testing', 'The_Mines_Kataiser.tas') == 'f434b8ca2104111bec514c8c875f3e8beb9d3340'
    assert main.get_sha('Kataiser/improvements-bot-testing', 'subproject/glitchy_-_Copy.tas') == 'd3a5291ad1119f376cd0a77bd9cf9bcfb112ed7b'


def test_get_file_repo_path():
    assert main.get_file_repo_path(970380662907482142, 'deskilln-deathkontrol.tas') == 'lobby/deskilln-deathkontrol.tas'
    assert main.get_file_repo_path(970380662907482142, 'Prologue.tas') == 'Prologue.tas'
    assert main.get_file_repo_path(970380662907482142, '1k_Kataiser.tas') == 'subproject/1k_Kataiser.tas'


def test_download_old_file(setup_log):
    assert len(main.download_old_file(970380662907482142, 'Kataiser/improvements-bot-testing', 'chaos_assembly_lol_lmao.tas')) == 4827


def test_convert_line_endings(setup_log):
    tas_crlf = Path('test_tases\\line endings\\raindrops_on_roses_crlf.tas').read_bytes()
    tas_lf = Path('test_tases\\line endings\\raindrops_on_roses_lf.tas').read_bytes()
    assert main.convert_line_endings(tas_crlf, tas_crlf) == tas_crlf
    assert main.convert_line_endings(tas_lf, tas_lf) == tas_lf
    assert main.convert_line_endings(tas_lf, tas_crlf) == tas_crlf
    assert main.convert_line_endings(tas_crlf, tas_lf) == tas_lf
    assert main.convert_line_endings(tas_crlf, None) == tas_crlf
    assert main.convert_line_endings(tas_lf, None) == tas_crlf


# GEN TOKEN

def test_generate_jwt(setup_log):
    db.tokens.delete_item('_jwt')
    time.sleep(1)
    jwt = gen_token.generate_jwt(1)
    assert jwt
    jwt2 = gen_token.generate_jwt(1)
    assert jwt == jwt2


def test_access_token():
    db.tokens.delete_item('_jwt')
    db.tokens.delete_item('Kataiser')
    time.sleep(1)
    token = gen_token.access_token('Kataiser', 1)
    assert token.startswith('ghs_')
    assert gen_token.tokens_local['_jwt']
    assert gen_token.tokens_local['Kataiser'][0] == token
    assert isinstance(gen_token.tokens_local['Kataiser'][1], int)
    token2 = gen_token.access_token('Kataiser', 1)
    assert token == token2


# VALIDATION

def test_validate(setup_log, monkeypatch):
    def mock_path_caches_get(*args, **kwargs):
        return {'0oi71n.tas': '0oi71n.tas', '6AC.tas': '6AC.tas', 'Prologue.tas': 'Prologue.tas', 'Untitled-12.tas': 'Untitled-12.tas',
                'The_Mines_Kataiser.tas': 'a_folder_yes/Lobby/The_Mines_Kataiser.tas', 'chaos_assembly_lol_lmao.tas': 'chaos_assembly_lol_lmao.tas',
                'grandmaster_heartside.tas': 'grandmaster_heartside.tas', 'journey.tas': 'journey.tas', 'abby-cookie.tas': 'lobby/abby-cookie.tas',
                'deskilln-deathkontrol.tas': 'lobby/deskilln-deathkontrol.tas', 'loopy_lagoon_crlf.tas': 'loopy_lagoon_crlf.tas',
                'seeing_is_believing_newer.tas': 'seeing_is_believing_newer.tas', 'seeing_is_believing_older.tas': 'seeing_is_believing_older.tas',
                '1k_Kataiser.tas': 'subproject/1k_Kataiser.tas', 'royal_gardens_renamed.tas': 'subproject/royal_gardens_renamed.tas',
                'glitchy.tas': 'sync_testing/glitchy.tas', 'glitchy_-_Copy.tas': 'sync_testing/glitchy_-_Copy.tas'}

    test_project = {'project_id': 0, 'is_lobby': False, 'excluded_items': (), 'ensure_level': True, 'disallowed_command_exemptions': []}
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(db.path_caches, 'get', mock_path_caches_get)
    ehs_valid = Path('test_tases\\expert_heartside.tas').read_bytes()
    ehs_old = Path('test_tases\\expert_heartside_old.tas').read_bytes()
    mock_kataiser = MockUser(219955313334288385, "Kataiser", "kataiser")
    message = discord.Message("-229f Expert Heartside (7:54.929)", MockChannel(970380662907482142), mock_kataiser)

    result_valid = validation.ValidationResult(valid_tas=True, warning_text=[], log_text=[], finaltime='7:54.929', timesave='-229f', finaltime_frames=27937)
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, ehs_old, test_project, False) == result_valid

    message_draft = discord.Message("Expert Heartside draft in 7:54.929", MockChannel(970380662907482142), mock_kataiser)
    result_valid_draft = validation.ValidationResult(valid_tas=True, warning_text=[], log_text=[], finaltime='7:54.929', finaltime_frames=27937)
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_draft, None, test_project, False) == result_valid_draft

    result_too_big = validation.ValidationResult(valid_tas=False, warning_text=["This TAS file is very large (265.5 KB). For safety, it won't be processed."],
                                                 log_text=["expert_heartside.tas being too long (271883 bytes)"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_too_big.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_too_big

    test_project['excluded_items'] = ('expert_heartside.tas',)
    result_excluded = validation.ValidationResult(valid_tas=False, warning_text=["This filename is excluded from the project."],
                                                  log_text=["file expert_heartside.tas is excluded from project (in ('expert_heartside.tas',))"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, None, test_project, False) == result_excluded
    test_project['excluded_items'] = ()

    result_identical = validation.ValidationResult(valid_tas=False, warning_text=["This file is identical to what's already in the repo."],
                                                   log_text=["file expert_heartside.tas is unchanged from repo"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, ehs_valid, test_project, False) == result_identical

    result_breakpoints = validation.ValidationResult(valid_tas=False,
                                                     warning_text=["Breakpoints found on lines: 1636, 1655, 1661, 1663, please remove them (Ctrl+P in Studio) and post again.",
                                                                   "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post "
                                                                   "again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                     log_text=["4 breakpoints in expert_heartside.tas", "no \"draft\" text in message"], finaltime='7:54.929', finaltime_frames=27937)
    assert validation.validate(Path('test_tases\\invalids\\ehs_breakpoints.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_breakpoints

    result_no_chaptertime = validation.ValidationResult(valid_tas=False,
                                                        warning_text=["No ChapterTime found in file, please add one and post again.",
                                                                      "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and "
                                                                      "post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                        log_text=["no ChapterTime in expert_heartside.tas", "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_no_chaptertime.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_no_chaptertime

    result_duplicate_room_label = validation.ValidationResult(valid_tas=False, finaltime='1:32.871', finaltime_frames=5463,
                                                              warning_text=["Duplicate room label `#lvl_start-01-Radley` found on line 225, please index revisited rooms starting from zero "
                                                                            "and post again.", "Duplicate room label `#lvl_hub` found on line 240, please index revisited rooms starting "
                                                                            "from zero and post again.", "Duplicate room label `#lvl_start-04-Radley` found on line 376, please index "
                                                                            "revisited rooms starting from zero and post again.", "Duplicate room label `#lvl_hub` found on line 383, please "
                                                                            "index revisited rooms starting from zero and post again.", "Duplicate room label `#lvl_start-02-Radley` found "
                                                                            "on line 515, please index revisited rooms starting from zero and post again.", "Duplicate room label `#lvl_hub` "
                                                                            "found on line 521, please index revisited rooms starting from zero and post again.", "The file's ChapterTime "
                                                                            "(1:32.871) is missing in your message, please add it and post again.", "Since this is a draft, please mention "
                                                                            "that in your message (just put the word \"draft\" somewhere reasonable) and post again. If it shouldn't be a "
                                                                            "draft, make sure your filename is exactly the same as in the repo.", "The level name is missing in your "
                                                                            "message, please add it and post again."],
                                                              log_text=["duplicate room label #lvl_start-01-Radley on line 225 in the_lab.tas",
                                                                        "duplicate room label #lvl_hub on line 240 in the_lab.tas",
                                                                        "duplicate room label #lvl_start-04-Radley on line 376 in the_lab.tas",
                                                                        "duplicate room label #lvl_hub on line 383 in the_lab.tas",
                                                                        "duplicate room label #lvl_start-02-Radley on line 515 in the_lab.tas",
                                                                        "duplicate room label #lvl_hub on line 521 in the_lab.tas",
                                                                        "ChapterTime (1:32.871) missing in message content",
                                                                        "no \"draft\" text in message",
                                                                        "level name ['lab'] missing in message content"])
    assert validation.validate(Path('test_tases\\room indexes\\the_lab not indexed.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_duplicate_room_label

    result_missing_room_label = validation.ValidationResult(valid_tas=False, finaltime='1:32.871', finaltime_frames=5463,
                                                            warning_text=["Missing room label index `#lvl_hub` found on line 521, please index revisited rooms starting from zero and post "
                                                                          "again.", "The file's ChapterTime (1:32.871) is missing in your message, please add it and post again.", "Since "
                                                                          "this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and "
                                                                          "post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo.", "The level "
                                                                          "name is missing in your message, please add it and post again."],
                                                            log_text=["missing room label #lvl_hub on line 521 in the_lab.tas",
                                                                      "ChapterTime (1:32.871) missing in message content",
                                                                      "no \"draft\" text in message",
                                                                      "level name ['lab'] missing in message content"])
    assert validation.validate(Path('test_tases\\room indexes\\the_lab unfinished index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_missing_room_label

    result_disordered_room_label = validation.ValidationResult(valid_tas=False, finaltime='1:32.871', finaltime_frames=5463,
                                                               warning_text=["Out of order room label index `#lvl_hub (2)` found on line 521, please index revisited rooms starting from "
                                                                             "zero and post again.", "The file's ChapterTime (1:32.871) is missing in your message, please add it and post "
                                                                             "again.", "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere "
                                                                             "reasonable) and post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the "
                                                                             "repo.", "The level name is missing in your message, please add it and post again."],
                                                               log_text=["out of order room label #lvl_hub (2) on line 521 in the_lab.tas",
                                                                         "ChapterTime (1:32.871) missing in message content",
                                                                         "no \"draft\" text in message",
                                                                         "level name ['lab'] missing in message content"])
    assert validation.validate(Path('test_tases\\room indexes\\the_lab disordered index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_disordered_room_label

    result_inconsistent_room_label = validation.ValidationResult(valid_tas=False, finaltime='1:32.871', finaltime_frames=5463,
                                                                 warning_text=["Incorrect initial room label index `#lvl_start-04-Radley (1)` found on line 80, please index revisited "
                                                                               "rooms starting from zero and post again.", "The file's ChapterTime (1:32.871) is missing in your message, "
                                                                               "please add it and post again.", "Since this is a draft, please mention that in your message (just put the "
                                                                               "word \"draft\" somewhere reasonable) and post again. If it shouldn't be a draft, make sure your filename is "
                                                                               "exactly the same as in the repo.",
                                                                               "The level name is missing in your message, please add it and post again."],
                                                                 log_text=["incorrect initial room label #lvl_start-04-Radley (1) on line 80 in the_lab.tas",
                                                                           "ChapterTime (1:32.871) missing in message content",
                                                                           "no \"draft\" text in message",
                                                                           "level name ['lab'] missing in message content"])
    assert (validation.validate(Path('test_tases\\room indexes\\the_lab inconsistent index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) ==
            result_inconsistent_room_label)

    result_last_room_label = validation.ValidationResult(valid_tas=False, finaltime='4:01.145', finaltime_frames=14185,
                                                         warning_text=["The file's ChapterTime (4:01.145) is missing in your message, please add it and post again.",
                                                                       "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) "
                                                                       "and post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo.",
                                                                       "The level name is missing in your message, please add it and post again."],
                                                         log_text=["ChapterTime (4:01.145) missing in message content",
                                                                           "no \"draft\" text in message",
                                                                   "level name ['area36'] missing in message content"])
    assert (validation.validate(Path('test_tases\\room indexes\\area_36.tas').read_bytes(), 'area_36.tas', message, None, test_project, False) ==
            result_last_room_label)

    result_disallowed_command = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                            warning_text=["Incorrect `ExitGame` command usage on line 3575: ExitGame command is not allowed.", "Since this is a draft, "
                                                                          "please mention that in your message (just put the word \"draft\" somewhere reasonable) and post again. If it "
                                                                          "shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                            log_text=["incorrect command argument in expert_heartside.tas: ExitGame, ExitGame command is not allowed",
                                                                      "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_exitgame.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_disallowed_command

    result_disallowed_command2 = validation.ValidationResult(valid_tas=False, finaltime='0:45.016', finaltime_frames=2648,
                                                             warning_text=["Incorrect `Set` command usage on line 10: Set command is not allowed.",
                                                                           "Incorrect `Set` command usage on line 394: Set command is not allowed.",
                                                                           "The file's ChapterTime (45.016) is missing in your message, please add it and post again.",
                                                                           "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) "
                                                                           "and post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo.",
                                                                           "The level name is missing in your message, please add it and post again."],
                                                             log_text=["incorrect command argument in nyoom.tas: Set, Set command is not allowed",
                                                                       "incorrect command argument in nyoom.tas: Set, Set command is not allowed",
                                                                       "ChapterTime (45.016) missing in message content",
                                                                       "no \"draft\" text in message",
                                                                       "level name ['nyoom'] missing in message content"])
    assert validation.validate(Path('test_tases\\nyoom.tas').read_bytes(), 'nyoom.tas', message, None, test_project, False) == result_disallowed_command2

    result_wrong_command_args = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                            warning_text=["Incorrect number of arguments to `Read` command on line 3452: is 4, should be 1-3.", "Since this is a draft, "
                                                                          "please mention that in your message (just put the word \"draft\" somewhere reasonable) and post again. If it "
                                                                          "shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                            log_text=["incorrect command arguments count in expert_heartside.tas: Read, 4 vs 1-3",
                                                                      "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_bad_read.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_wrong_command_args

    result_bad_command_usage = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                           warning_text=["Incorrect `RecordCount:` command usage on line 1: records must be a number, you have \"yes\".", "Since this is a "
                                                                         "draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post again. If "
                                                                         "it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                           log_text=["incorrect command argument in expert_heartside.tas: RecordCount:, records must be a number, you have \"yes\"",
                                                                     "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_bad_recordcount.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_bad_command_usage

    result_wrong_analogmode = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                          warning_text=["Incorrect last AnalogMode, is Square but should be Ignore so as to not possibly desync later TASes.", "Since this "
                                                                        "is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post again. "
                                                                        "If it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                          log_text=["last analogmode in expert_heartside.tas is square", "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_wrong_analogmode.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_wrong_analogmode

    message_no_chaptertime = discord.Message("-229f Expert Heartside", MockChannel(), mock_kataiser)
    result_no_message_chaptertime = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                                warning_text=["The file's ChapterTime (7:54.929) is missing in your message, please add it and post again.", "Since this "
                                                                              "is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post "
                                                                              "again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                                log_text=["ChapterTime (7:54.929) missing in message content", "no \"draft\" text in message"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_chaptertime, None, test_project, False) == result_no_message_chaptertime

    test_project['is_lobby'] = True

    result_no_message_chaptertime_lobby = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                                      warning_text=["The file's final time (7:54.929) is missing in your message, please add it and post again.",
                                                                                    "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere "
                                                                                    "reasonable) and post again. If it shouldn't be a draft, make sure your filename is exactly the same "
                                                                                    "as in the repo."],
                                                                      log_text=["final time (7:54.929) missing in message content", "no \"draft\" text in message"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_chaptertime, None, test_project, False) == result_no_message_chaptertime_lobby

    result_no_start = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                  warning_text=["No `#Start` found in file, please add one between the console load frame and the intro frames (or first room label if "
                                                                "none) and post again.", "Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere "
                                                                "reasonable) and post again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo."],
                                                  log_text=["no #Start in file", "no \"draft\" text in message"])
    assert validation.validate(Path('test_tases\\invalids\\ehs_no_start.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_no_start

    message_no_timesave = discord.Message("Expert Heartside (7:54.929)", MockChannel(), mock_kataiser)
    result_no_timesave = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                     warning_text=["Please mention how many frames were saved or lost, with the text \"-229f\" (if that's correct), and post again."],
                                                     log_text=["no timesave in message (should be -229f)"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_timesave, ehs_old, test_project, False) == result_no_timesave

    message_wrong_timesave = discord.Message("-666f Expert Heartside (7:54.929)", MockChannel(), mock_kataiser)
    result_wrong_timesave = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937, timesave='-666f',
                                                        warning_text=["Frames saved is incorrect (you said \"-666f\", but it seems to be \"-229f\"), please fix and post again. Make sure "
                                                                      "you updated the time and improved the latest version of the file."],
                                                        log_text=["incorrect time saved in message (is \"-666f\", should be \"-229f\")"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_wrong_timesave, ehs_old, test_project, False) == result_wrong_timesave

    result_wrong_timesave_options = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937, timesave='-229f',
                                                                warning_text=["Frames saved is incorrect (you said \"-229f\", but it seems to be \"-0f\" or \"+0f\"), please fix and post "
                                                                              "again. Make sure you updated the time and improved the latest version of the file."],
                                                                log_text=["incorrect time saved in message (is \"-229f\", should be \"-0f\" or \"+0f\")"])
    assert (validation.validate(Path('test_tases\\invalids\\ehs_slightly_different.tas').read_bytes(), 'expert_heartside.tas', message, ehs_valid, test_project, False) ==
            result_wrong_timesave_options)

    result_no_draft = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937,
                                                  warning_text=["Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere reasonable) and post "
                                                                "again. If it shouldn't be a draft, make sure your filename is exactly the same as in the repo (did you mean "
                                                                "`grandmaster_heartside.tas`?).", "The level name is missing in your message, please add it and post again."],
                                                  log_text=["no \"draft\" text in message", "level name ['grandmasterheartside2'] missing in message content"])
    assert validation.validate(ehs_valid, 'grandmaster_heartside2.tas', message, None, test_project, False) == result_no_draft

    message_no_levelname = discord.Message("-229f (7:54.929)", MockChannel(), mock_kataiser)
    result_no_levelname = validation.ValidationResult(valid_tas=False, finaltime='7:54.929', finaltime_frames=27937, timesave='-229f',
                                                      warning_text=["The level name is missing in your message, please add it and post again."],
                                                      log_text=["level name ['expertheartside'] missing in message content"])
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_levelname, ehs_old, test_project, False) == result_no_levelname

    test_project['project_id'] = 598945702554501130
    message_maingame_levelname = discord.Message("-0f Farewell 8:04.177(28481)", MockChannel(), mock_kataiser)
    result_maingame_levelname = validation.ValidationResult(valid_tas=True, finaltime='8:04.177', finaltime_frames=28481, timesave='-0f', warning_text=[], log_text=[])
    farewell = Path('test_tases\\9.tas').read_bytes()
    farewell_prev = Path('test_tases\\9_prev.tas').read_bytes()
    assert validation.validate(farewell, '9.tas', message_maingame_levelname, farewell_prev, test_project, False) == result_maingame_levelname

    message_maingame_levelname2 = discord.Message("-1f 4SH (3:24.867 → 3:24.850)", MockChannel(), mock_kataiser)
    result_maingame_levelname2 = validation.ValidationResult(valid_tas=True, finaltime='3:24.850', finaltime_frames=12050, timesave='-1f', warning_text=[], log_text=[])
    ridge_sh = Path('test_tases\\4SH0.tas').read_bytes()
    ridge_sh_prev = Path('test_tases\\4SH0_prev.tas').read_bytes()
    assert validation.validate(ridge_sh, '4SH0.tas', message_maingame_levelname2, ridge_sh_prev, test_project, False) == result_maingame_levelname2


def test_parse_tas_file(setup_log):
    test_tases = [('0_-_All_C_Sides.tas', 71,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:43.659', finaltime_trimmed='2:43.659',
                                            finaltime_line_num=70, finaltime_frames=9627, finaltime_type=validation.FinalTimeTypes.File)),
                  ('6AC.tas', 308,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='47.600', finaltime_trimmed='47.600',
                                            finaltime_line_num=305, finaltime_frames=2800, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('0oi71n.tas', 501,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:08.748', finaltime_trimmed='1:08.748',
                                            finaltime_line_num=498, finaltime_frames=4044, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('0oi71n 2.tas', 501,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:08.748', finaltime_trimmed='1:08.748',
                                            finaltime_line_num=498, finaltime_frames=4044, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('1k_Kataiser.tas', 250,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:46.308', finaltime_trimmed='46.308',
                                            finaltime_line_num=249, finaltime_frames=2724, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('5C_TPH.tas', 110,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:33.745', finaltime_trimmed='33.745',
                                            finaltime_line_num=108, finaltime_frames=1985, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('abby-cookie.tas', 28,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2.108', finaltime_trimmed='2.108',
                                            finaltime_line_num=27, finaltime_frames=124, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('expert_heartside.tas', 3576,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='7:54.929', finaltime_trimmed='7:54.929',
                                            finaltime_line_num=3575, finaltime_frames=27937, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('deskilln-deathkontrol.tas', 158,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='12.699', finaltime_trimmed='12.699',
                                            finaltime_line_num=157, finaltime_frames=747, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('drifting_deep.tas', 1029,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:44.662', finaltime_trimmed='2:44.662',
                                            finaltime_line_num=1028, finaltime_frames=9686, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('temple_of_a_thousand_skies.tas', 395,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:22.392', finaltime_trimmed='2:22.392',
                                            finaltime_line_num=394, finaltime_frames=8376, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('pufferfish_transportation.tas', 466,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:26.377', finaltime_trimmed='1:26.377',
                                            finaltime_line_num=465, finaltime_frames=5081, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('royal_gardens.tas', 985,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:34.853', finaltime_trimmed='2:34.853',
                                            finaltime_line_num=984, finaltime_frames=9109, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('sky_palace.tas', 645,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:29.879', finaltime_trimmed='1:29.879',
                                            finaltime_line_num=644, finaltime_frames=5287, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('undergrowth.tas', 618,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:34.333', finaltime_trimmed='1:34.333',
                                            finaltime_line_num=617, finaltime_frames=5549, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('the_lab.tas', 751,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:32.497', finaltime_trimmed='1:32.497',
                                            finaltime_line_num=750, finaltime_frames=5441, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('The_Mines_Kataiser.tas', 238,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:39.321', finaltime_trimmed='39.321',
                                            finaltime_line_num=237, finaltime_frames=2313, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('glitchy\\glitchy.tas', 684,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:47.651', finaltime_trimmed='47.651',
                                            finaltime_line_num=683, finaltime_frames=2803, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('3H.tas', 466,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:53.519', finaltime_trimmed='2:53.519',
                                            finaltime_line_num=465, finaltime_frames=10207, finaltime_type=validation.FinalTimeTypes.Chapter))]

    for test_tas in test_tases:
        print(test_tas[0])
        lines = validation.as_lines(Path(f'test_tases\\{test_tas[0]}').read_bytes())
        assert len(lines) == test_tas[1]
        assert validation.parse_tas_file(lines, True) == test_tas[2]

    custom_test_tas = '3H.tas'
    print(custom_test_tas)
    lines = validation.as_lines(Path(f'test_tases\\{custom_test_tas}').read_bytes())
    assert len(lines) == 466
    expected_parsed = validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:26.717', finaltime_trimmed='1:26.717',
                                               finaltime_line_num=279, finaltime_frames=5101, finaltime_type=validation.FinalTimeTypes.MidwayChapter)
    assert validation.parse_tas_file(lines, True, required_finaltime_type=validation.FinalTimeTypes.MidwayChapter) == expected_parsed

    custom_test_tas = 'Caper_Cavortion.tas'
    print(custom_test_tas)
    lines = validation.as_lines(Path(f'test_tases\\{custom_test_tas}').read_bytes())
    assert len(lines) == 304
    expected_parsed = validation.ParsedTASFile(breakpoints=['287'], found_finaltime=True, finaltime='1:26.071', finaltime_trimmed='1:26.071',
                                               finaltime_line_num=298, finaltime_frames=5063, finaltime_type=validation.FinalTimeTypes.Comment)
    assert validation.parse_tas_file(lines, True) == expected_parsed
    expected_parsed = validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:26.071', finaltime_trimmed='1:26.071',
                                               finaltime_line_num=298, finaltime_frames=5063, finaltime_type=validation.FinalTimeTypes.Comment)
    assert validation.parse_tas_file(lines, False) == expected_parsed
    expected_parsed = validation.ParsedTASFile(breakpoints=['287'], found_finaltime=False, finaltime=None, finaltime_trimmed=None,
                                               finaltime_line_num=None, finaltime_frames=None, finaltime_type=None)
    assert validation.parse_tas_file(lines, True, allow_comment_time=False) == expected_parsed


def test_time_to_frames():
    assert validation.time_to_frames('2:43.659') == 9627
    assert validation.time_to_frames('47.600') == 2800
    assert validation.time_to_frames('1:08.748') == 4044
    assert validation.time_to_frames('0:46.308') == 2724
    assert validation.time_to_frames('46.308') == 2724
    assert validation.time_to_frames('2.108') == 124
    assert validation.time_to_frames('7:58.822') == 28166
    assert validation.time_to_frames('12.699') == 747
    assert validation.time_to_frames('0:12.699') == 747
    assert validation.time_to_frames('2:44.662') == 9686
    assert validation.time_to_frames('2:22.392') == 8376
    assert validation.time_to_frames('1:26.377') == 5081
    assert validation.time_to_frames('2:34.853') == 9109
    assert validation.time_to_frames('1:29.879') == 5287
    assert validation.time_to_frames('1:34.333') == 5549
    assert validation.time_to_frames('1:32.497') == 5441
    assert validation.time_to_frames('0:39.321') == 2313
    assert validation.time_to_frames('39.321') == 2313
    assert validation.time_to_frames('0:47.651') == 2803
    assert validation.time_to_frames('47.651') == 2803


def test_calculate_time_difference():
    assert validation.calculate_time_difference('4:23.160', '4:23.007') == 9
    assert validation.calculate_time_difference(15480, 15471) == 9
    assert validation.calculate_time_difference('4:23.160', 15471) == 9
    assert validation.calculate_time_difference(15480, '4:23.007') == 9
    assert validation.calculate_time_difference('4:23.007', '4:23.007') == 0
    assert validation.calculate_time_difference(15471, 15471) == 0
    assert validation.calculate_time_difference('19.431', '19.380') == 3
    assert validation.calculate_time_difference('1:13.797', ' 1:13.695') == 6


def test_as_lines(setup_log):
    lines = ['console load SpringCollab2020/0-Lobbies/1-Beginner 3192 160', '   1', '', '#Start', '  36', '', '#lvl_lobby_main', '   4,D,X', '   2,R,X', '   1,R,J', '   7,R',
             '   3,R,J', '   9,R,X', '   3,R,J,G', '   8,R', '  11,R,J', '  15,U,X', '  18,L,J', '   4,L,K,G', '   7,L', '   4,D,X', '   1,L,J', '   7,L', '   5,L,J', '   2,L',
             '  27,L,D,C', '   1,X', '#2.108']

    assert validation.as_lines(Path('test_tases\\abby-cookie.tas').read_bytes()) == lines


def test_filter_out_links(setup_log):
    message = ("-17f The [House](https://discord.com/channels/1269042133541585049/1269042134124855321/1269247598250819635) on Ash Tree Lane 0:15.742(926) -> 0:15.453(909)\n"
               "+4f [a-00-outside] Build up speed\n"
               "[second link](https://ptb.discord.com/channels/1269042133541585049/1269042134124855321/1269422472097300563) I guess")

    assert validation.filter_out_links(message) == "-17f The House on Ash Tree Lane 0:15.742(926) -> 0:15.453(909)\n+4f [a-00-outside] Build up speed\nsecond link I guess"


# COMMANDS

@pytest.mark.xfail
@pytest.mark.asyncio
async def test_is_admin(setup_log, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    channel = discord.TextChannel()
    assert await commands.is_project_admin(discord.Message('', channel, MockUser()), {'admins': ()})
    assert await commands.is_project_admin(discord.Message('', channel, MockUser(id=651190712288935939)), {'admins': (403759119758131211, 651190712288935939)})
    assert not channel.sent_messages
    assert not await commands.is_project_admin(discord.Message('', channel, MockUser(id=651190712288935939)), {'admins': ()})
    assert channel.sent_messages == ["Not allowed, you are not a project admin."]


@pytest.mark.asyncio
async def test_dm_echo(setup_log, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    channel = discord.TextChannel()
    await commands.handle_direct_dm(discord.Message("ok", channel, MockUser()))
    await commands.handle_direct_dm(discord.Message("hello", channel, MockUser()))
    assert channel.sent_messages == ["ok", "hello"]


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_help(setup_log, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    channel = discord.TextChannel()
    user = MockUser()
    user.id = 219955313334288386
    await commands.handle(discord.Message("help", channel, user))
    assert channel.sent_messages == ["Alright, looks you want to add your TAS project to this bot (or are just curious about what the help command says). Awesome! So, steps:\n\n"
                                     "1. Register GitHub app with your account and repo (you likely need to be the repo owner): <https://github.com/apps/celestetas-improvements-tracker>\n"
                                     "2. Add bot to your server: <https://discord.com/oauth2/authorize?client_id=970375635027525652&scope=bot&permissions=2147560512>\n"
                                     "3. Run the `register_project` command, see `help register_project` for parameters. You can also use this to edit existing projects.\n"
                                     "4. (Optional) Add other admins with `edit_admins`, and add mod(s) for sync testing with `add_mods`.\n\n"
                                     "Available commands:\n"
                                     "`help`: Get bot installation instructions, or the info for a command.\n"
                                     "`register_project`: Add or edit a project (improvements channel).\n"
                                     "`link_lobby_sheet`: Link the lobby project to a google sheet, so that improvements will automatically be written to the routing table.\n"
                                     "`add_mods`: Set the game mods a sync check needs to load.\n"
                                     "`rename_file`: Rename a file in the repo of a project. Recommended over "
                                     "manually committing.\n"
                                     "`edit_admin`: Add or remove admins from a project.\n"
                                     "`about`: Get bot info and status.\n"
                                     "`about_project`: Get the info and settings of a project.\n"
                                     "`projects`: Get the basic info and settings of all projects.\n"
                                     "`projects_admined`: List projects you're an admin of."]
    await commands.handle(discord.Message("help", channel, MockUser()))
    assert 'sync_log' in channel.sent_messages[1]


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_about(setup_log, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    channel = discord.TextChannel()
    await commands.handle(discord.Message("about", channel, MockUser()))
    sent_messages_split = channel.sent_messages[0].splitlines()

    assert sent_messages_split[0] == "Source: <https://github.com/Kataiser/CelesteTAS-Improvements-Tracker>"
    assert sent_messages_split[1] == "Projects (improvement channels): 0"
    assert sent_messages_split[2] == "Servers: 0"
    assert sent_messages_split[3].startswith("Github installations: ")
    assert sent_messages_split[4] == "Bot uptime: 0.0 hours"
    assert sent_messages_split[5].startswith("Host uptime: ")
    assert sent_messages_split[6].startswith("Current host: ")
    assert sent_messages_split[7].startswith("Sync checks: ")
    assert sent_messages_split[8].startswith("Improvements/drafts processed and committed: ")


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_about_project(setup_log, setup_client, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    monkeypatch.setattr(discord, 'Client', mock_client)
    channel = discord.TextChannel()
    await commands.handle(discord.Message("about_project \"Improvements bot testing\"", channel, MockUser()))
    assert channel.sent_messages == ["Name: **Improvements bot testing**\n"
                                     "Repo: <https://github.com/Kataiser/improvements-bot-testing>\n"
                                     "Improvement channel: <#970380662907482142>\n"
                                     "Admins: Kataiser (kataiser, 219955313334288385), Vamp (vampire_flower, 234520815658336258)\n"
                                     "Github installation owner: Kataiser\n"
                                     "Install time: <t:1652133751>\n"
                                     "Pin: <https://discord.com/channels/970379400887558204/970380662907482142/973344238261657650>\n"
                                     "Commits drafts: `True`\n"
                                     "Is lobby: `False`\n"
                                     "Ensures level name in posts: `True`\n"
                                     "Does sync check: `False`\n"
                                     "Uses contributors file: `True`"]


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_projects_admined(setup_log, monkeypatch):
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    channel = discord.TextChannel()
    await commands.handle(discord.Message("projects_admined", channel, MockUser()))
    assert "FLCC" in channel.sent_messages[0]
    assert "Improvements bot directory testing" in channel.sent_messages[0]
    assert "Improvements bot testing" in channel.sent_messages[0]
    assert "Midway Contest" in channel.sent_messages[0]
    assert "Strawberry Jam" in channel.sent_messages[0]
    assert channel.sent_messages[0].count('\n') >= 9


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_add_mods(setup_log, monkeypatch):
    @dataclasses.dataclass
    class MockItem:
        stem: str
        suffix: str

    class MockModsPath:
        def iterdir(self):
            return MockItem('RandomStuffHelper', '.zip'), MockItem('Glitchy_Platformer', '.zip')

    def mock_mods_dir() -> MockModsPath:
        return MockModsPath()

    @functools.cache
    def mock_get_mod_dependencies(*args) -> set:
        return set()

    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    monkeypatch.setattr(game_sync, 'mods_dir', mock_mods_dir)
    monkeypatch.setattr(game_sync, 'get_mod_dependencies', mock_get_mod_dependencies)
    sync_project = db.projects.get(976903244863381564)
    sync_project['do_run_validation'] = True
    sync_project['mods'] = ['Glitchy_Platformer']
    db.projects.set(976903244863381564, sync_project)

    channel = discord.TextChannel()
    assert db.projects.get(976903244863381564)['mods'] == ['Glitchy_Platformer']
    await commands.handle(discord.Message("add_mods 970380662907482142 RandomStuffHelper", channel, MockUser()))
    assert channel.sent_messages == ["Project \"Improvements bot testing\" has sync checking disabled.", "No projects (with sync checking enabled) matching that name or ID found."]
    await commands.handle(discord.Message("add_mods 976903244863381564 RandomStuffHelper", channel, MockUser()))
    assert channel.sent_messages[2:] == ["Project \"Improvements bot sync testing\" now has 1 mod (plus 0 dependencies) to load for sync testing."]
    assert set(db.projects.get(976903244863381564)['mods']) == {'Glitchy_Platformer', 'RandomStuffHelper'}
    sync_project['do_run_validation'] = False
    db.projects.set(976903244863381564, sync_project)


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_command_register_project_editing(setup_log, setup_client, monkeypatch):
    def mock_missing_channel_permissions(*args) -> list:
        return []

    monkeypatch.setattr(utils, 'missing_channel_permissions', mock_missing_channel_permissions)
    monkeypatch.setattr(discord, 'Message', mock_message)
    monkeypatch.setattr(discord, 'TextChannel', mock_channel)
    monkeypatch.setattr(discord, 'Client', mock_client)
    project_before = db.projects.get(970380662907482142)
    channel = discord.TextChannel()
    await commands.handle(discord.Message("register_project \"Improvements bot testing\" 970380662907482142 Kataiser/improvements-bot-testing Kataiser Y N Y Y N", channel, MockUser()))
    assert channel.sent_messages == ['Verifying...', 'Successfully verified and edited project.']
    assert db.projects.get(970380662907482142) == project_before


# GAME SYNC

def test_get_mod_everest_yaml(monkeypatch):
    assert game_sync.get_mod_everest_yaml('', Path('test_tases\\PuzzleHelper.zip')) == {'DLL': 'PuzzleHelper.dll', 'Dependencies': [{'Name': 'Everest', 'Version': '1.3366.0'}],
                                                                                        'Name': 'PuzzleHelper', 'Version': '1.1.0'}
    assert game_sync.get_mod_everest_yaml('', Path('test_tases\\TASides.zip')) == {'Dependencies': [{'Name': 'Everest', 'Version': '1.796.0'}],
                                                                                   'Name': 'TASides', 'Version': '0.2.0'}


# SPREADSHEET

def test_read_sheet():
    read = spreadsheet.read_sheet(spreadsheet.SJ_SHEET_ID, 'Intermediate!A2:I20', multiple_rows=True)
    assert read[9][0] == 'Pointless Machines'
    assert read[9][1] == 'pointless_machines.tas'
    assert read[9][2] == 'ImDart'
    assert read[9][3]  # current time
    assert read[9][4] == '1:00.146'
    assert read[9][5]  # time saved
    assert int(read[9][6]) >= 5955
    assert float(read[9][7]) >= 2.063
    assert read[9][8].count('/') == 2


def test_sj_fuzzy_match():
    assert spreadsheet.sj_fuzzy_match('') == []
    assert spreadsheet.sj_fuzzy_match('lab') == ['The Lab', 'Lethal Laser Laboratory']
    assert spreadsheet.sj_fuzzy_match('summit') == ['summit', 'Summit Down-Side']
    assert spreadsheet.sj_fuzzy_match('heart') == ['Beginner Heartside', 'Intermediate Heartside', 'Advanced Heartside', 'Expert Heartside', 'Grandmaster Heartside']
    assert spreadsheet.sj_fuzzy_match('Storm Runner') == ['Storm Runner']


def test_correct_map_case():
    assert spreadsheet.correct_map_case('world abyss') == 'World Abyss'
    assert spreadsheet.correct_map_case('WORLD ABYSS') == 'World Abyss'
    assert spreadsheet.correct_map_case('Eat Girl') == 'EAT GIRL'
    assert spreadsheet.correct_map_case('Paint') == 'paint'
    assert spreadsheet.correct_map_case('not a map') == 'not a map'

def test_offset_cell():
    assert spreadsheet.offset_cell('A2', 1, 10) == 'B12'
    assert spreadsheet.offset_cell('AC30', 3, 4) == 'AF34'

    assert spreadsheet.offset_cell('Z0', 1, 0) == 'AA0'
    assert spreadsheet.offset_cell('AZ0', 1, 0) == 'BA0'
    assert spreadsheet.offset_cell('ZZ0', 1, 0) == 'AAA0'
    assert spreadsheet.offset_cell('AAZ0', 1, 0) == 'ABA0'
    assert spreadsheet.offset_cell('AZZ0', 1, 0) == 'BAA0'
    assert spreadsheet.offset_cell('ZZZ0', 1, 0) == 'AAAA0'

    assert spreadsheet.offset_cell('Lobby!C1', 1, 1) == 'Lobby!D2'

# DB

def test_project_get(fast_db):
    test_project = db.projects.get(970380662907482142)
    assert set(test_project['admins']) == {Decimal('219955313334288385'), Decimal('234520815658336258')}
    assert test_project['commit_drafts']
    assert test_project['contributors_file_path'] == ''
    assert test_project['desyncs'] == []
    assert not test_project['do_run_validation']
    assert test_project['ensure_level']
    assert test_project['excluded_items'] == ['abby-cookie2.tas']
    assert test_project['filetimes'] == {}
    assert test_project['install_time'] == Decimal('1652133751')
    assert test_project['installation_owner'] == 'Kataiser'
    assert not test_project['is_lobby']
    assert test_project['last_commit_time'] >= Decimal('1702421113')
    assert test_project['last_run_validation'] is None
    assert test_project['mods'] == ['Randomizer']
    assert test_project['name'] == 'Improvements bot testing'
    assert test_project['pin'] == Decimal('973344238261657650')
    assert test_project['project_id'] == Decimal('970380662907482142')
    assert test_project['repo'] == 'Kataiser/improvements-bot-testing'
    assert test_project['subdir'] == ''
    assert not test_project['sync_check_timed_out']
    assert test_project['sync_environment_state'] == {'everest_version': None, 'host': None, 'last_commit_time': None, 'mod_versions': {}}
    assert test_project['use_contributors_file']


def test_project_get_all(fast_db):
    projects_all = db.projects.get_all()
    projects_dict = db.projects.dict()
    assert len(projects_all) >= 40
    assert len(projects_all) == len(projects_dict)
    assert isinstance(projects_all, list)
    assert isinstance(projects_dict, dict)
    assert 970380662907482142 in projects_dict


def test_project_get_by_name_or_id(fast_db):
    assert db.projects.get_by_name_or_id('Improvements bot testing')[0]['project_id'] == Decimal('970380662907482142')
    assert db.projects.get_by_name_or_id(970380662907482142)[0]['name'] == 'Improvements bot testing'


def test_various_gets(fast_db):
    contributions = db.contributors.get(1074148268407275520)['219955313334288385']
    assert contributions['count'] >= 185
    assert contributions['name'] == "Kataiser"
    assert db.githubs.get(219955313334288385) == ['Kataiser', 'mecharon1.gm@gmail.com']
    history_log = db.history_log.get('2023-08-07 10:25:52,547')
    assert history_log == ("('skun (skun, 344974874969636865)', 1074148268407275520, 'Strawberry Jam', '-24f intermediate_heartside.tas (4:15.374) from skun', "
                           "'https://github.com/VampireFlower/StrawberryJamTAS/commit/796a366da367504b3caed4913d23c3f65b4b7141', "
                           "'https://cdn.discordapp.com/attachments/1074148268407275520/1138130893865766962/intermediate_heartside.tas')")
    assert ast.literal_eval(history_log) == ('skun (skun, 344974874969636865)', 1074148268407275520, 'Strawberry Jam', '-24f intermediate_heartside.tas (4:15.374) from skun',
                                             'https://github.com/VampireFlower/StrawberryJamTAS/commit/796a366da367504b3caed4913d23c3f65b4b7141',
                                             'https://cdn.discordapp.com/attachments/1074148268407275520/1138130893865766962/intermediate_heartside.tas')
    assert 20000000 < db.installations.get('Kataiser') < 30000000
    assert "last processed post from" in db.misc.get('status')
    assert db.path_caches.get(970380662907482142)['glitchy_-_Copy.tas'] == 'sync_testing/glitchy_-_Copy.tas'
    project_log = db.project_logs.get(970380662907482142)
    assert 1184274417929433098 in project_log
    assert 1178067958975705088 not in project_log
    assert db.sheet_writes.get('2023-10-12 15:49:00,087') == {'log': '(\'The Lab\', \'Advanced!A22:J22\', "(\'current_time\', \'1:28.757\') (\'improvement_date\', \'2023-10-12\') '
                                                                     '(\'time_saved\', \'924f (15.0%)\') (\'records\', \'7810\') (\'rpf\', \'1.496\')")',
                                                              'status': 'INFO', 'timestamp': '2023-10-12 15:49:00,087'}
    assert db.sid_caches.get(1074148268407275520)['1_Beginner/azure_caverns.tas'] == 'StrawberryJam2021/1-Beginner/cellularAutomaton'


def test_set_delete_and_size():
    current_time = int(time.time())
    size = db.misc.size()
    db.misc.set('TEST', current_time)
    time.sleep(0.5)
    assert db.misc.get('TEST') == current_time
    assert db.misc.size() == size + 1
    db.misc.delete_item('TEST')
    time.sleep(0.5)
    assert 'TEST' not in [item['key'] for item in db.misc.get_all()]
    assert db.misc.size() == size
    assert db.history_log.size(False) > 9000


# UTILS

def test_plural():
    assert utils.plural(0) == 's'
    assert utils.plural(1) == ''
    assert utils.plural(2) == 's'
    assert utils.plural(()) == 's'
    assert utils.plural(('a',)) == ''
    assert utils.plural(('a', 'b')) == 's'


def test_load_sj_data():
    sj_data, sj_data_filenames = utils.load_sj_data()
    assert isinstance(sj_data, dict)
    assert isinstance(sj_data_filenames, dict)
    assert len(sj_data) == 117
    assert len(sj_data_filenames) == 117


def test_log_timestamp(monkeypatch):
    def mock_time():
        return 1704954712.2238538

    monkeypatch.setattr(time, 'time', mock_time)
    assert utils.log_timestamp() in ('2024-01-11 00:31:52,224', '2024-01-11 06:31:52,224')


def test_host():
    assert utils.host() != "Unknown"


def test_get_user_github_account():
    assert utils.get_user_github_account(219955313334288385) == ['Kataiser', 'mecharon1.gm@gmail.com']


def test_nicknames(monkeypatch):
    monkeypatch.setattr(discord, 'User', mock_user)
    assert utils.nickname(discord.User(587491655129759744, "έλλατάς", "ellatas")) == "EllaTAS"
    assert utils.nickname(discord.User(219955313334288385, "Kataiser", "kataiser")) == "Kataiser"


def test_detailed_user(monkeypatch):
    monkeypatch.setattr(discord, 'User', mock_user)
    assert utils.detailed_user(user=discord.User(587491655129759744, "έλλατάς", "ellatas")) == "έλλατάς (ellatas, 587491655129759744)"
    assert utils.detailed_user(user=discord.User(219955313334288385, "Kataiser", "kataiser")) == "Kataiser (kataiser, 219955313334288385)"
