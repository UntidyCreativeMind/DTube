"""Container entry point: apply the file owner from settings, then start the server."""
import os
import sys

from . import db


def chown_tree(path, uid, gid):
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)


def main():
    db.init()
    if os.geteuid() == 0:
        settings = db.get_settings()
        uid, gid = int(settings["file_uid"]), int(settings["file_gid"])
        chown_tree(db.CONFIG_DIR, uid, gid)
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    os.umask(0o002)
    os.execv(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "dtube.main:app", "--host", "0.0.0.0", "--port", "3434"],
    )


if __name__ == "__main__":
    main()
