"""Writes the published data to a folder the local Caddy serves (LLD "Development environment")."""

from pathlib import Path


class LocalDataPublisher:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def publish(self, folder: str, files: dict[str, bytes], manifest: bytes) -> None:
        target = self.root / folder.removeprefix("data/")
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (target / name).write_bytes(content)
        # The manifest goes last and atomically, so the front never reads a folder that isn't there yet.
        tmp = self.root / "manifest.json.tmp"
        tmp.write_bytes(manifest)
        tmp.replace(self.root / "manifest.json")
