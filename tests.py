import time
from decimal import Decimal
from pathlib import Path

import pytest

import db
import gen_token
import main
import utils
import validation


@pytest.fixture
def setup_log():
    main.create_logger('tests', False)


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
                  ('expert_heartside.tas', 3382,
                   validation.ParsedTASFile(breakpoints=[], found_finaltime=True, finaltime='7:58.822', finaltime_trimmed='7:58.822',
                                            finaltime_line_num=3381, finaltime_frames=28166, finaltime_type=validation.FinalTimeTypes.Chapter)),
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


# DB

def test_project_get():
    test_project = db.projects.get(970380662907482142, consistent_read=False)
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


def test_project_get_all():
    projects_all = db.projects.get_all(consistent_read=False)
    projects_dict = db.projects.dict(consistent_read=False)
    assert len(projects_all) == 39
    assert len(projects_all) == len(projects_dict)
    assert isinstance(projects_all, list)
    assert isinstance(projects_dict, dict)
    assert 970380662907482142 in projects_dict


def test_project_get_by_name_or_id():
    assert db.projects.get_by_name_or_id('Improvements bot testing', consistent_read=False)[0]['project_id'] == Decimal('970380662907482142')
    assert db.projects.get_by_name_or_id(970380662907482142, consistent_read=False)[0]['name'] == 'Improvements bot testing'


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
    def mockreturn():
        return 1704954712.2238538

    monkeypatch.setattr(time, 'time', mockreturn)
    assert utils.log_timestamp() == '2024-01-11 00:31:52,224'


def test_host():
    assert utils.host() != "Unknown"


def test_get_user_github_account():
    assert utils.get_user_github_account(219955313334288385) == ['Kataiser', 'mecharon1.gm@gmail.com']
