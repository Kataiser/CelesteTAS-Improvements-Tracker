import dataclasses
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import discord
import pytest

import db
import gen_token
import main
import spreadsheet
import utils
import validation


@pytest.fixture
def setup_log():
    main.create_logger('tests', False)


@pytest.fixture
def fast_db():
    db.always_inconsistent_read = True


@dataclasses.dataclass
class MockUser:
    id: int
    global_name: str
    name: str


@dataclasses.dataclass
class MockChannel:
    id: Optional[int] = None


@dataclasses.dataclass
class MockMessage:
    content: str
    channel: MockChannel
    author: MockUser


# MAIN

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
    assert main.get_sha('Kataiser/improvements-bot-testing', 'journey.tas') == '7e0291c78555e7fa856f8f2cb9ec4483df6a10be'
    assert main.get_sha('Kataiser/improvements-bot-testing', 'subproject/glitchy_-_Copy.tas') == 'd3a5291ad1119f376cd0a77bd9cf9bcfb112ed7b'


def test_get_file_repo_path():
    assert main.get_file_repo_path(970380662907482142, 'deskilln-deathkontrol.tas') == 'lobby/deskilln-deathkontrol.tas'
    assert main.get_file_repo_path(970380662907482142, 'Prologue.tas') == 'Prologue.tas'
    assert main.get_file_repo_path(970380662907482142, '1k_Kataiser.tas') == 'subproject/1k_Kataiser.tas'


def test_download_old_file(setup_log):
    assert len(main.download_old_file(970380662907482142, 'Kataiser/improvements-bot-testing', 'chaos_assembly_lol_lmao.tas')) == 4824


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
    jwt = gen_token.generate_jwt(1)
    assert jwt
    jwt2 = gen_token.generate_jwt(1)
    assert jwt == jwt2


def test_access_token():
    token = gen_token.access_token('Kataiser', 1)
    assert token.startswith('ghs_')
    assert gen_token.tokens['_jwt']
    assert gen_token.tokens['Kataiser'][0] == token
    assert isinstance(gen_token.tokens['Kataiser'][1], float)
    token2 = gen_token.access_token('Kataiser', 1)
    assert token == token2


# VALIDATION

def test_validate(setup_log, monkeypatch):
    def mock_message(*args, **kwargs):
        return MockMessage(*args, **kwargs)

    test_project = {'is_lobby': False, 'excluded_items': (), 'ensure_level': True}
    monkeypatch.setattr(discord, 'Message', mock_message)
    ehs_valid = Path('test_tases\\expert_heartside.tas').read_bytes()
    ehs_old = Path('test_tases\\expert_heartside_old.tas').read_bytes()
    mock_kataiser = MockUser(219955313334288385, "Kataiser", "kataiser")
    message = mock_message("-229f Expert Heartside (7:54.929)", MockChannel(970380662907482142), mock_kataiser)

    result_valid = validation.ValidationResult(valid_tas=True, finaltime='7:54.929', timesave='-229f')
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, ehs_old, test_project, False) == result_valid

    message_draft = mock_message("Expert Heartside draft in 7:54.929", MockChannel(970380662907482142), mock_kataiser)
    result_valid_draft = validation.ValidationResult(valid_tas=True, finaltime='7:54.929')
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_draft, None, test_project, False) == result_valid_draft

    result_too_big = validation.ValidationResult(valid_tas=False, warning_text="This TAS file is very large (265.5 KB). For safety, it won't be processed.",
                                                 log_text="expert_heartside.tas being too long (271883 bytes)")
    assert validation.validate(Path('test_tases\\invalids\\ehs_too_big.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_too_big

    test_project['excluded_items'] = ('expert_heartside.tas',)
    result_excluded = validation.ValidationResult(valid_tas=False, warning_text="This filename is excluded from the project.",
                                                  log_text="file expert_heartside.tas is excluded from project (in ('expert_heartside.tas',))")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, None, test_project, False) == result_excluded
    test_project['excluded_items'] = ()

    result_identical = validation.ValidationResult(valid_tas=False, warning_text="This file is identical to what's already in the repo.",
                                                   log_text="file expert_heartside.tas is unchanged from repo")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message, ehs_valid, test_project, False) == result_identical

    result_breakpoints = validation.ValidationResult(valid_tas=False, warning_text="Breakpoints found on lines: 1636, 1655, 1661, 1663, please remove them (Ctrl+P in Studio) "
                                                                                   "and post again.", log_text="4 breakpoints in expert_heartside.tas")
    assert validation.validate(Path('test_tases\\invalids\\ehs_breakpoints.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_breakpoints

    result_no_chaptertime = validation.ValidationResult(valid_tas=False, warning_text="No ChapterTime found in file, please add one and post again.",
                                                        log_text="no ChapterTime in expert_heartside.tas")
    assert validation.validate(Path('test_tases\\invalids\\ehs_no_chaptertime.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_no_chaptertime

    result_duplicate_room_label = validation.ValidationResult(valid_tas=False, warning_text="Duplicate room label `#lvl_start-01-Radley` found on line 225, please index revisited rooms "
                                                                                            "starting from zero and post again.",
                                                              log_text="Duplicate room label #lvl_start-01-Radley on line 225 in the_lab.tas")
    assert validation.validate(Path('test_tases\\room indexes\\the_lab not indexed.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_duplicate_room_label

    result_missing_room_label = validation.ValidationResult(valid_tas=False, warning_text="Missing room label index `#lvl_hub` found on line 521, please index revisited rooms starting "
                                                                                          "from zero and post again.", log_text="Missing room label #lvl_hub on line 521 in the_lab.tas")
    assert validation.validate(Path('test_tases\\room indexes\\the_lab unfinished index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_missing_room_label

    result_disordered_room_label = validation.ValidationResult(valid_tas=False, warning_text="Out of order room label index `#lvl_hub (2)` found on line 521, please index revisited rooms "
                                                                                             "starting from zero and post again.",
                                                               log_text="Out of order room label #lvl_hub (2) on line 521 in the_lab.tas")
    assert validation.validate(Path('test_tases\\room indexes\\the_lab disordered index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) == result_disordered_room_label

    result_inconsistent_room_label = validation.ValidationResult(valid_tas=False, warning_text="Incorrect initial room label index `#lvl_start-04-Radley (1)` found on line 80, please index "
                                                                                               "revisited rooms starting from zero and post again.",
                                                                 log_text="Incorrect initial room label #lvl_start-04-Radley (1) on line 80 in the_lab.tas")
    assert (validation.validate(Path('test_tases\\room indexes\\the_lab inconsistent index.tas').read_bytes(), 'the_lab.tas', message, None, test_project, False) ==
            result_inconsistent_room_label)

    result_disallowed_command = validation.ValidationResult(valid_tas=False, warning_text="Incorrect `ExitGame` command usage on line 3575: ExitGame command is not allowed.",
                                                            log_text="incorrect command argument in expert_heartside.tas: ExitGame, ExitGame command is not allowed")
    assert validation.validate(Path('test_tases\\invalids\\ehs_exitgame.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_disallowed_command

    result_wrong_command_args = validation.ValidationResult(valid_tas=False, warning_text="Incorrect number of arguments to `Read` command on line 3452: is 4, should be 1-3.",
                                                            log_text="incorrect command arguments count in expert_heartside.tas: Read, 4 vs 1-3")
    assert validation.validate(Path('test_tases\\invalids\\ehs_bad_read.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_wrong_command_args

    result_bad_command_usage = validation.ValidationResult(valid_tas=False, warning_text="Incorrect `RecordCount:` command usage on line 1: records must be a number, you have \"yes\".",
                                                           log_text="incorrect command argument in expert_heartside.tas: RecordCount:, records must be a number, you have \"yes\"")
    assert validation.validate(Path('test_tases\\invalids\\ehs_bad_recordcount.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_bad_command_usage

    result_wrong_analogmode = validation.ValidationResult(valid_tas=False, warning_text="Incorrect last AnalogMode, is Square but should be Ignore so as to not possibly desync later TASes.",
                                                          log_text="last analogmode in expert_heartside.tas is square")
    assert validation.validate(Path('test_tases\\invalids\\ehs_wrong_analogmode.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_wrong_analogmode

    message_no_chaptertime = mock_message("-229f Expert Heartside", MockChannel(), mock_kataiser)
    result_no_message_chaptertime = validation.ValidationResult(valid_tas=False, warning_text="The file's ChapterTime (7:54.929) is missing in your message, please add it and post again.",
                                                                log_text="ChapterTime (7:54.929) missing in message content")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_chaptertime, None, test_project, False) == result_no_message_chaptertime

    test_project['is_lobby'] = True

    result_no_message_chaptertime_lobby = validation.ValidationResult(valid_tas=False, warning_text="The file's final time (7:54.929) is missing in your message, please add it and "
                                                                                                    "post again.", log_text="final time (7:54.929) missing in message content")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_chaptertime, None, test_project, False) == result_no_message_chaptertime_lobby

    result_no_start = validation.ValidationResult(valid_tas=False, warning_text="No `#Start` found in file, please add one between the console load frame and the intro frames (or first "
                                                                                "room label if none) and post again.", log_text="no #Start in file")
    assert validation.validate(Path('test_tases\\invalids\\ehs_no_start.tas').read_bytes(), 'expert_heartside.tas', message, None, test_project, False) == result_no_start

    message_no_timesave = mock_message("Expert Heartside (7:54.929)", MockChannel(), mock_kataiser)
    result_no_timesave = validation.ValidationResult(valid_tas=False, warning_text="Please mention how many frames were saved or lost, with the text \"-229f\" (if that's correct), "
                                                                                   "and post again.", log_text="no timesave in message (should be -229f)")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_timesave, ehs_old, test_project, False) == result_no_timesave

    message_wrong_timesave = mock_message("-666f Expert Heartside (7:54.929)", MockChannel(), mock_kataiser)
    result_wrong_timesave = validation.ValidationResult(valid_tas=False, warning_text="Frames saved is incorrect (you said \"-666f\", but it seems to be \"-229f\"), please fix and "
                                                                                      "post again. Make sure you improved the latest version of the file.",
                                                        log_text="incorrect time saved in message (is \"-666f\", should be \"-229f\")")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_wrong_timesave, ehs_old, test_project, False) == result_wrong_timesave

    result_wrong_timesave_options = validation.ValidationResult(valid_tas=False, warning_text="Frames saved is incorrect (you said \"-229f\", but it seems to be \"-0f\" or \"+0f\"), please "
                                                                                              "fix and post again. Make sure you improved the latest version of the file.",
                                                                log_text="incorrect time saved in message (is \"-229f\", should be \"-0f\" or \"+0f\")")
    assert (validation.validate(Path('test_tases\\invalids\\ehs_slightly_different.tas').read_bytes(), 'expert_heartside.tas', message, ehs_valid, test_project, False) ==
            result_wrong_timesave_options)

    result_no_draft = validation.ValidationResult(valid_tas=False, warning_text="Since this is a draft, please mention that in your message (just put the word \"draft\" somewhere "
                                                                                "reasonable) and post again. If it shouldn't be a draft, make sure your filename is exactly the same as in "
                                                                                "the repo (did you mean `grandmaster_heartside.tas`?).", log_text="no \"draft\" text in message")
    assert validation.validate(ehs_valid, 'grandmaster_heartside2.tas', message, None, test_project, False) == result_no_draft

    message_no_levelname = mock_message("-229f (7:54.929)", MockChannel(), mock_kataiser)
    result_no_levelname = validation.ValidationResult(valid_tas=False, warning_text="The level name is missing in your message, please add it and post again.",
                                                      log_text="level name (expertheartside) missing in message content")
    assert validation.validate(ehs_valid, 'expert_heartside.tas', message_no_levelname, ehs_old, test_project, False) == result_no_levelname


def test_parse_tas_file(setup_log):
    test_tases = [('0_-_All_C_Sides.tas', 71,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2:43.659', finaltime_trimmed='2:43.659',
                                            finaltime_line_num=70, finaltime_frames=9627, finaltime_type=validation.FinalTimeTypes.File)),
                  ('6AC.tas', 308,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='47.600', finaltime_trimmed='47.600',
                                            finaltime_line_num=305, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('0oi71n.tas', 501,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:08.748', finaltime_trimmed='1:08.748',
                                            finaltime_line_num=498, finaltime_frames=4044, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('0oi71n 2.tas', 501,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:08.748', finaltime_trimmed='1:08.748',
                                            finaltime_line_num=498, finaltime_frames=4044, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('1k_Kataiser.tas', 250,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:46.308', finaltime_trimmed='46.308',
                                            finaltime_line_num=249, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('5C_TPH.tas', 110,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='0:33.745', finaltime_trimmed='33.745',
                                            finaltime_line_num=108, finaltime_frames=1985, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('abby-cookie.tas', 28,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='2.108', finaltime_trimmed='2.108',
                                            finaltime_line_num=27, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)),
                  ('expert_heartside.tas', 3576,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='7:54.929', finaltime_trimmed='7:54.929',
                                            finaltime_line_num=3575, finaltime_frames=27937, finaltime_type=validation.FinalTimeTypes.Chapter)),
                  ('deskilln-deathkontrol.tas', 158,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='12.699', finaltime_trimmed='12.699',
                                            finaltime_line_num=157, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)),
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
                                               finaltime_line_num=298, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)
    assert validation.parse_tas_file(lines, True) == expected_parsed
    expected_parsed = validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='1:26.071', finaltime_trimmed='1:26.071',
                                               finaltime_line_num=298, finaltime_frames=None, finaltime_type=validation.FinalTimeTypes.Comment)
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


# SPREADSHEET


def test_read_sheet():
    read = spreadsheet.read_sheet('Intermediate!A2:I20', multiple_rows=True)
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


# DB

def test_project_get(fast_db):
    test_project = db.projects.get(970380662907482142)
    assert test_project['admins'] == [Decimal('219955313334288385'), Decimal('234520815658336258')]
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
    assert len(projects_all) == 39
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
    assert eval(history_log) == ('skun (skun, 344974874969636865)', 1074148268407275520, 'Strawberry Jam', '-24f intermediate_heartside.tas (4:15.374) from skun',
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
    assert db.sync_results.metadata()['Table']['TableId'] == '30be9a9f-b134-45ec-90e6-863a13fef99c'


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
    def mock_return():
        return 1704954712.2238538

    monkeypatch.setattr(time, 'time', mock_return)
    assert utils.log_timestamp() == '2024-01-11 00:31:52,224'


def test_host():
    assert utils.host() != "Unknown"


def test_get_user_github_account():
    assert utils.get_user_github_account(219955313334288385) == ['Kataiser', 'mecharon1.gm@gmail.com']


def test_nicknames(monkeypatch):
    def mock_user(*args, **kwargs):
        return MockUser(*args, **kwargs)

    monkeypatch.setattr(discord, 'User', mock_user)
    assert utils.nickname(discord.User(587491655129759744, "έλλατάς", "ellatas")) == "Ella"
    assert utils.nickname(discord.User(219955313334288385, "Kataiser", "kataiser")) == "Kataiser"


def test_detailed_user(monkeypatch):
    def mock_user(*args, **kwargs):
        return MockUser(*args, **kwargs)

    monkeypatch.setattr(discord, 'User', mock_user)
    assert utils.detailed_user(user=discord.User(587491655129759744, "έλλατάς", "ellatas")) == "έλλατάς (ellatas, 587491655129759744)"
    assert utils.detailed_user(user=discord.User(219955313334288385, "Kataiser", "kataiser")) == "Kataiser (kataiser, 219955313334288385)"
