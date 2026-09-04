"""Single-process ownership for a local Gmail state directory (macOS/Linux)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_state(state_dir: Path) -> Iterator[None]:
    import fcntl

    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never unlink: removing a locked inode would let another process lock a new file.
    with (state_dir / "worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another worker owns this Gmail state directory") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
