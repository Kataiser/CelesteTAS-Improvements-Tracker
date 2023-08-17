import subprocess
import time

import game_sync
from utils import plural


def main():
    mods_input = input("\n: ")

    mods = set(mods_input.split())
    replacements = {'sj': 'StrawberryJam2021', 'ssc2': 'SecretSanta2023', 'sc': 'SpringCollab2020', 'wc': 'WinterCollab2021', 'itj': 'Into The Jungle', 'dsides': 'Monika\'s D-Sides',
                    'flp': 'The Frogeline Project', 'mwc': 'MidwayContest2022', 'flcc': 'FLCCcollab', 'egc': 'EGCPACK', 'dmr': 'darkmoonruins', 'ac': 'AnarchyCollab2022'}

    for replacement in replacements:
        if replacement in mods:
            mods.remove(replacement)
            mods.add(replacements[replacement])
            print(f"{replacement} -> {replacements[replacement]}")

    for mod in mods:
        mods = mods.union(game_sync.get_mod_dependencies(mod))

    game_sync.generate_blacklist(mods)
    print(f"Created blacklist, launching game with {len(mods)} mod{plural(mods)}")
    subprocess.Popen(r'E:\Big downloads\celeste\Celeste.exe', creationflags=0x00000010)  # the creationflag is for not waiting until the process exits


if __name__ == '__main__':
    main()
