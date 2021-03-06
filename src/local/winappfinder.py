import logging
import re
import asyncio
import pathlib
from typing import Optional, Dict

from local.localgame import LocalHumbleGame
from local.baseappfinder import BaseAppFinder
from local.reg_watcher import WinRegUninstallWatcher, UninstallKey


logger = logging.getLogger(__name__)


def location_exists(location: Optional[pathlib.Path]):
    """Wrapper for Python version < 3.8"""
    try:
        return location and location.exists()
    except OSError:
        return False


class WindowsAppFinder(BaseAppFinder):
    def __init__(self):
        super().__init__()
        self._reg = WinRegUninstallWatcher(ignore_filter=self.is_other_store_game)

    @staticmethod
    def is_other_store_game(key_name) -> bool:
        """Exclude Steam and GOG games using uninstall key name.
        In the future probably more DRM-free stores should be supported
        """
        match = re.match(r'\d{10}_is1', key_name)  # GOG.com
        if match:
            return True
        return "Steam App" in key_name

    @staticmethod
    def _matches(human_name: str, uk: UninstallKey) -> bool:
        def escape(x):
            return x.replace(':', '').lower()
        def escaped_matches(a, b):
            return escape(a) == escape(b)
        def norm(x):
            return x.replace(" III", " 3").replace(" II", " 2")

        if human_name == uk.display_name \
            or escaped_matches(human_name, uk.display_name) \
            or uk.key_name.lower().startswith(human_name.lower()):
            return True

        location = uk.get_install_location()
        if location:
            if escaped_matches(human_name, location.name):
                return True

        # quickfix for Torchlight II ect., until better solution will be provided
        return escaped_matches(norm(human_name), norm(uk.display_name))

    def _find_executable(self, human_name: str, uk: UninstallKey) -> Optional[pathlib.Path]:
        """Returns most probable app executable of given uk or None if not found.
        """
        # sometimes display_icon links to main executable
        upath = uk.local_uninstaller_path
        ipath = uk.display_icon_path
        if ipath and ipath.suffix == '.exe':
            if ipath != upath and 'unins' not in str(ipath):  # exclude uninstaller
                return ipath

        # get install_location if present; if not, check for uninstall or display_icon parents
        location = uk.install_location_path \
            or (upath.parent if upath else None) \
            or (ipath.parent if ipath else None)

        # find all executables and get best machting (exclude uninstall_path)
        if location_exists(location):
            executables = list(set(self._pathfinder.find_executables(location)) - {str(upath)})
            best_match = self._pathfinder.choose_main_executable(human_name, executables)
            if best_match is None:
                logger.warning(f'Main exe not found for {human_name}; \
                    loc: {uk.install_location}; up: {upath}; ip: {ipath}; execs: {executables}')
                return None
            return pathlib.Path(best_match)
        return None

    async def __call__(self, owned_title_id, paths=None):
        local_games: Dict[str, LocalHumbleGame] = {}
        not_found = owned_title_id.copy()

        # match using registry
        self._reg.refresh()
        while self._reg.uninstall_keys:
            uk = self._reg.uninstall_keys.pop()
            try:
                for human_name, machine_name in owned_title_id.items():
                    if self._matches(human_name, uk):
                        exe = self._find_executable(human_name, uk)
                        if exe is not None:
                            game = LocalHumbleGame(machine_name, exe, uk.install_location_path, uk.uninstall_string)
                            logger.info(f'New local game found: {game}')
                            local_games[machine_name] = game
                            del not_found[human_name]
                            break
                        logger.warning(f"Uninstall key matched, but cannot find \
                            game exe for [{human_name}]; uk: {uk}")
            except Exception:
                self._reg.uninstall_keys.add(uk)
                raise
            await asyncio.sleep(0)  # makes this method non blocking

        # try to match the rest using folders scan
        if paths is not None:
            local_games.update(await super().__call__(not_found, paths))

        return local_games