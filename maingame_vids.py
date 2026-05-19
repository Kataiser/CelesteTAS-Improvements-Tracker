import copy
import dataclasses
import logging
import os
import subprocess
import time
import zlib
from pathlib import Path

import niquests

import game_sync
import main


def generate_all():
    global log
    log = main.create_logger('generate_maingame_vids')
    game_sync.generate_blacklist({'CelesteTAS', 'TASRecorder'})
    game_sync.close_game()
    game_sync.start_game()
    cwdir = os.getcwd()
    os.chdir(maingame_vids_path / 'CelesteTAS')
    subprocess.run(['git', 'reset', '--hard'])
    subprocess.run(['git', 'pull'])
    os.chdir(cwdir)
    log.info("Reset repo, finding all rooms")
    all_rooms = []

    for tas_path in (maingame_vids_path / 'CelesteTAS').rglob('**/*.tas'):
        if tas_path.name == '9NMG.tas' or (tas_path.name.startswith('0 - ') and tas_path.name not in
                                           ('0 - Epilogue.tas', '0 - EpilogueFast.tas', '0 - Prologue.tas')):
            log.info(f"Skipping {tas_path.name} (excluded)")
            continue

        log.info(f"Finding rooms for {tas_path.name}...")

        with open(tas_path, 'rb') as tas_file:
            file_lines = tas_file.read().decode('UTF8').splitlines()
            file_lines_cache[tas_path.name] = file_lines

        rooms_found, excluded_count = get_rooms_from_tas(file_lines, tas_path)
        log.info(f"Found {len(rooms_found)} ({excluded_count} excluded)")
        all_rooms.extend(rooms_found)

    log.info(f"Finished finding {len(all_rooms)} rooms")

    for new_recorded_vid in get_new_recorded_vids():
        log.info(f"Deleting {new_recorded_vid}")
        new_recorded_vid.unlink()

    for video in [f for f in maingame_vids_path.glob('*_*_*_*.mp4')]:
        if video.name not in [room.video_filename(False) for room in all_rooms] + [room.video_filename(True) for room in all_rooms]:
            log.info(f"Deleting outdated {video.name}")
            video.unlink()

    game_sync.wait_for_game_load({'CelesteTAS', 'TASRecorder'}, '')
    existing_vids = [v.name for v in maingame_vids_path.glob('*.mp4')]
    current_filename = all_rooms[0].tas_path.name
    log.info("Starting video generation")

    for room in all_rooms:
        if current_filename != room.tas_path.name:
            log.info(f"Writing back original {room.tas_path.name}")

            with open(room.tas_path, 'w', encoding='UTF8') as tas_file:
                tas_file.truncate()
                tas_file.write('\n'.join(file_lines_cache[room.tas_path.name]))

            time.sleep(0.1)

        current_filename = room.tas_path.name
        generate_vid_for_room(room, existing_vids, False)
        generate_vid_for_room(room, existing_vids, True)

    log.info("Finished")
    game_sync.close_game()


@dataclasses.dataclass
class Room:
    tas_path: Path | str
    name: str
    line_num_start: int
    line_num_end: int = 0
    inputs: list[str] = None
    inputs_hash: int = 0

    def __post_init__(self):
        self.inputs = []

    def add_input_line(self, line: str, line_num: int):
        self.inputs.append(line)
        self.line_num_end = line_num

    def finalize(self):
        self.inputs_hash = zlib.adler32('\n'.join(self.inputs).encode('UTF8'))

    def tas_name(self) -> str:
        return self.tas_path.name if isinstance(self.tas_path, Path) else self.tas_path.rpartition('/')[2]

    def video_filename(self, hitboxes: bool):
        return f'{self.tas_name()[:-4]}_{self.name}_{self.inputs_hash}_{'hitboxes' if hitboxes else 'main'}.mp4'

    def suggestion_id(self) -> str:
        return f'{self.tas_name()[:-4]}_{self.name}'

    def __str__(self) -> str:
        return f'file={self.tas_name()}, room={self.name}, line_num={self.line_num_start}'


def get_rooms_from_tas(tas_lines: list[str], tas_path: Path | str) -> tuple[list[Room], int]:
    rooms = []
    current_room = None
    rooms_excluded_count = 0

    for line_num, line in enumerate([*tas_lines, '#lvl_']):
        if line.startswith('#lvl_'):  # start new room
            if current_room:
                if len(current_room.inputs) > 3:
                    current_room.finalize()
                else:
                    rooms.remove(current_room)
                    rooms_excluded_count += 1

            if line_num < len(tas_lines):  # excludes appended #lvl_
                current_room = Room(tas_path=tas_path, name=line[5:], line_num_start=line_num)
                rooms.append(current_room)
        elif current_room:  # add inputs to current room
            current_room.add_input_line(line, line_num)

    return rooms, rooms_excluded_count


def generate_vid_for_room(room: Room, existing_vids: list[str], hitboxes: bool):
    video_filename = room.video_filename(hitboxes)

    if video_filename in existing_vids:
        log.info(f"Skipping existing {video_filename}")
        return
    elif f'{room.tas_path.name[:-4]}_{room.name}' in ('7AG_f-02', '5SHCG_a-10 (0)', '6BG_a-05 (1)', '7AG_f-02', '5SHCG_a-10 (0)', '5SHCG_e-00 (1)', '4SHCG_a-00'):
        log.info(f"Skipping {video_filename} (borked)")
        return

    log.info(f"Generating {video_filename}")
    tas_lines = copy.copy(file_lines_cache[room.tas_path.name])
    tas_lines.insert(room.line_num_start, '***')
    tas_lines.insert(room.line_num_start + 1, 'StartRecording')
    tas_lines.insert(room.line_num_start + 2, f'Set,TASRecorder.Speed,{'0.5' if hitboxes else '1.0'}')
    tas_lines.insert(room.line_num_end + 2, 'StopRecording')
    tas_lines.insert(0, 'Set,Everest.ShowModOptionsInGame,False')
    tas_lines.insert(0, 'Set,SpeedrunClock,Chapter')
    tas_lines.insert(0, f'Set,CelesteTAS.ShowHitboxes,{'True' if hitboxes else 'False'}')
    tas_lines.insert(0, f'Set,CelesteTAS.SimplifiedGraphics,{'True' if hitboxes else 'False'}')
    tas_lines.insert(0, f'Set,CelesteTAS.CenterCamera,{'True' if hitboxes else 'False'}')
    tas_lines.insert(0, f'Set,CelesteTAS.InfoHud,{'True' if hitboxes else 'False'}')
    tas_lines.insert(0, 'Set,CelesteTAS.InfoGame,True')
    tas_lines.insert(0, 'Set,CelesteTAS.InfoTasInput,True')
    tas_lines = tas_lines[:room.line_num_end + 15]

    with open(room.tas_path, 'w', encoding='UTF8') as tas_file:
        tas_file.truncate()
        tas_file.write('\n'.join(tas_lines))

    try:
        time.sleep(0.5)
        niquests.post(f'http://localhost:32270/tas/playtas?filePath={room.tas_path}', timeout=10)
        time.sleep(2)
        prev_state = None
        start_time = time.perf_counter()

        while prev_state != (game_state := niquests.get('http://localhost:32270/tas/info', timeout=10).content):
            if time.perf_counter() - start_time > 30:
                log.info("Game seems to have gotten stuck, abandoning")
                return

            log.info("Waiting for breakpoint...")
            prev_state = game_state
            time.sleep(0.2)

        niquests.post('http://localhost:32270/tas/sendhotkey?id=Pause', timeout=10)
        log.info("Started recording")
        start_time = time.perf_counter()
    except niquests.RequestException as error:
        log.error(error)
        log.info("Restarting game")
        time.sleep(10)
        game_sync.close_game()
        game_sync.start_game()
        game_sync.wait_for_game_load({'CelesteTAS', 'TASRecorder'}, '')
        return

    while True:
        time.sleep(2)

        if time.perf_counter() - start_time > 120:
            log.info("Game seems to have gotten stuck, abandoning")
            return

        if not (new_recorded_vid_paths := get_new_recorded_vids()):
            continue

        prev_name = new_recorded_vid_paths[0].name

        # wait for recording to finish
        try:
            new_recorded_vid_paths[0].rename(maingame_vids_path / video_filename)
        except PermissionError:
            continue

        log.info(f"Renamed from {prev_name}")
        time.sleep(1)
        break


def get_new_recorded_vids() -> list[Path]:
    return [f for f in maingame_vids_path.glob('202*.mp4')]


log: logging.Logger = None
maingame_vids_path = Path('maingame_vids').absolute()
file_lines_cache: dict[str, list[str]] = {}


if __name__ == '__main__':
    generate_all()
